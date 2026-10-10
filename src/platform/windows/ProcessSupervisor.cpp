// HOST-0006 - Process supervisor implementation (portable).

#include "platform/windows/ProcessSupervisor.h"

#include "foundation/Timestamp.h"

#include <chrono>
#include <cstdlib>
#include <thread>
#include <vector>

#ifdef _WIN32
#include <windows.h>
#include <process.h>
// wingdi.h (pulled in by windows.h) defines ERROR as 0, which would rewrite
// ServiceState::ERROR below into ServiceState::0. Drop the macro.
#undef ERROR
#else
#include <csignal>
#include <cerrno>
#include <fcntl.h>
#include <poll.h>
#include <sys/types.h>
#include <sys/wait.h>
#include <unistd.h>
#endif

namespace aura {

namespace {

std::int64_t nowMillis() {
    return Timestamp::now().epochMillis();
}

}  // namespace

ProcessSupervisor::ProcessSupervisor(ProcessLaunchSpec spec, RestartPolicy policy)
    : spec_(std::move(spec)), policy_(policy) {}

bool ProcessSupervisor::isRunning() const {
    const_cast<ProcessSupervisor*>(this)->reapIfExited();
    return info_.running;
}

std::string ProcessSupervisor::lastError() const { return lastError_; }

void ProcessSupervisor::reapIfExited() {
    if (!info_.running || info_.pid == 0) return;
#ifdef _WIN32
    // On Windows we do not hold a process handle after detaching; rely on
    // explicit stop or a failed health probe to transition state.
#else
    int status = 0;
    const pid_t rc = ::waitpid(static_cast<pid_t>(info_.pid), &status, WNOHANG);
    if (rc == static_cast<pid_t>(info_.pid)) {
        info_.running = false;
        info_.exitCode = WIFEXITED(status) ? WEXITSTATUS(status) : -1;
        state_ = ServiceState::OFFLINE;
    }
#endif
}

bool ProcessSupervisor::spawn(std::string& error) {
    if (spec_.executable.empty()) {
        error = "no executable configured";
        return false;
    }

#ifdef _WIN32
    std::string commandLine = "\"" + spec_.executable + "\"";
    for (const auto& arg : spec_.args) {
        commandLine += " \"" + arg + "\"";
    }
    std::vector<char> mutableCmd(commandLine.begin(), commandLine.end());
    mutableCmd.push_back('\0');

    STARTUPINFOA si{};
    si.cb = sizeof(si);
    PROCESS_INFORMATION pi{};
    const DWORD flags = CREATE_NO_WINDOW;
    const BOOL ok = CreateProcessA(
        nullptr, mutableCmd.data(), nullptr, nullptr, FALSE, flags,
        nullptr, spec_.workingDir.empty() ? nullptr : spec_.workingDir.c_str(),
        &si, &pi);
    if (!ok) {
        error = "CreateProcess failed with code " + std::to_string(GetLastError());
        return false;
    }
    info_.pid = static_cast<long>(pi.dwProcessId);
    info_.running = true;
    CloseHandle(pi.hThread);
    CloseHandle(pi.hProcess);
    return true;
#else
    std::vector<char*> argv;
    std::string exe = spec_.executable;
    argv.push_back(const_cast<char*>(exe.c_str()));
    std::vector<std::string> argStore = spec_.args;
    for (auto& arg : argStore) argv.push_back(const_cast<char*>(arg.c_str()));
    argv.push_back(nullptr);

    // Self-pipe: the child writes a byte only if it fails before exec. The
    // write end is close-on-exec, so a successful exec closes it and the
    // parent observes EOF. This makes a failed exec visible immediately
    // instead of reporting a dead process as ONLINE.
    int execPipe[2] = {-1, -1};
    if (::pipe(execPipe) != 0) {
        error = "pipe failed";
        return false;
    }
    const int pipeFlags = ::fcntl(execPipe[1], F_GETFD);
    if (pipeFlags >= 0) ::fcntl(execPipe[1], F_SETFD, pipeFlags | FD_CLOEXEC);

    const pid_t pid = ::fork();
    if (pid < 0) {
        ::close(execPipe[0]);
        ::close(execPipe[1]);
        error = "fork failed";
        return false;
    }
    if (pid == 0) {
        // Child.
        ::close(execPipe[0]);
        if (!spec_.workingDir.empty()) {
            if (::chdir(spec_.workingDir.c_str()) != 0) {
                const int rc = 127;
                ssize_t ignored = ::write(execPipe[1], &rc, sizeof(rc));
                (void)ignored;
                _exit(127);
            }
        }
        for (const auto& kv : spec_.environment) {
            ::setenv(kv.first.c_str(), kv.second.c_str(), 1);
        }
        ::execv(exe.c_str(), argv.data());
        const int rc = 127;
        ssize_t ignored = ::write(execPipe[1], &rc, sizeof(rc));
        (void)ignored;
        _exit(127);   // exec failed
    }

    // Parent.
    ::close(execPipe[1]);
    struct pollfd pfd{};
    pfd.fd = execPipe[0];
    pfd.events = POLLIN;
    const int pollResult = ::poll(&pfd, 1, spec_.execProbeMillis);
    bool execFailed = false;
    if (pollResult > 0 && (pfd.revents & (POLLIN | POLLHUP))) {
        int childStatus = 0;
        const ssize_t n = ::read(execPipe[0], &childStatus, sizeof(childStatus));
        if (n > 0) execFailed = true;   // child reported a pre-exec failure
    }
    ::close(execPipe[0]);

    if (execFailed) {
        int status = 0;
        ::waitpid(pid, &status, 0);
        error = "failed to execute: " + exe;
        return false;
    }
    info_.pid = static_cast<long>(pid);
    info_.running = true;
    return true;
#endif
}

bool ProcessSupervisor::start(std::string& error) {
    if (info_.running) {
        error = "process already running";
        return false;
    }
    state_ = ServiceState::STARTING;
    if (!spawn(error)) {
        state_ = ServiceState::ERROR;
        lastError_ = error;
        return false;
    }
    state_ = ServiceState::ONLINE;
    lastError_.clear();
    return true;
}

bool ProcessSupervisor::stop(int graceMillis, std::string& error) {
    if (!info_.running || info_.pid == 0) {
        state_ = ServiceState::OFFLINE;
        return true;
    }
    state_ = ServiceState::PAUSED;
#ifdef _WIN32
    HANDLE handle = OpenProcess(PROCESS_TERMINATE, FALSE, static_cast<DWORD>(info_.pid));
    if (handle != nullptr) {
        TerminateProcess(handle, 0);
        CloseHandle(handle);
    }
#else
    ::kill(static_cast<pid_t>(info_.pid), SIGTERM);
    const int steps = graceMillis / 50;
    for (int i = 0; i < steps; ++i) {
        int status = 0;
        const pid_t rc = ::waitpid(static_cast<pid_t>(info_.pid), &status, WNOHANG);
        if (rc == static_cast<pid_t>(info_.pid)) {
            info_.running = false;
            state_ = ServiceState::OFFLINE;
            return true;
        }
        std::this_thread::sleep_for(std::chrono::milliseconds(50));
    }
    ::kill(static_cast<pid_t>(info_.pid), SIGKILL);
    ::waitpid(static_cast<pid_t>(info_.pid), nullptr, 0);
#endif
    info_.running = false;
    state_ = ServiceState::OFFLINE;
    error.clear();
    return true;
}

bool ProcessSupervisor::restart(std::string& error) {
    const std::int64_t now = nowMillis();
    if (windowStartMillis_ == 0 ||
        now - windowStartMillis_ > policy_.windowMillis) {
        windowStartMillis_ = now;
        restartCount_ = 0;
    }
    if (restartCount_ >= policy_.maxRestarts) {
        state_ = ServiceState::BLOCKED;
        error = "restart budget exhausted";
        lastError_ = error;
        return false;
    }
    ++restartCount_;

    std::string stopError;
    stop(2000, stopError);
    state_ = ServiceState::RECOVERING;
    if (!start(error)) {
        lastError_ = error;
        return false;
    }
    return true;
}

void ProcessSupervisor::markHealthy() {
    if (state_ != ServiceState::ONLINE) {
        state_ = ServiceState::ONLINE;
    }
    lastError_.clear();
}

void ProcessSupervisor::markUnhealthy(const std::string& reason) {
    if (state_ == ServiceState::ONLINE) {
        state_ = ServiceState::DEGRADED;
    }
    lastError_ = reason;
}

}  // namespace aura
