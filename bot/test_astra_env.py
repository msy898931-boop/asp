#!/usr/bin/env python3
"""astra_env resolves the host/repo from the environment, with sane defaults."""

import importlib
import os
import sys
import unittest

HERE = os.path.dirname(os.path.abspath(__file__))
if HERE not in sys.path:
    sys.path.insert(0, HERE)

import astra_env  # noqa: E402


class AstraEnvTest(unittest.TestCase):
    def _reload(self, **env):
        """Reload astra_env with a temporary environment, return (URL, REPO).

        Values are captured before the environment is restored: modules are
        singletons, so reloading again in cleanup would overwrite the module
        the caller is about to inspect.
        """
        saved = {k: os.environ.get(k) for k in env}
        try:
            for k, v in env.items():
                if v is None:
                    os.environ.pop(k, None)
                else:
                    os.environ[k] = v
            mod = importlib.reload(astra_env)
            return mod.URL, mod.REPO
        finally:
            for k, v in saved.items():
                if v is None:
                    os.environ.pop(k, None)
                else:
                    os.environ[k] = v

    def test_defaults_when_unset(self):
        url, repo = self._reload(ASTRA_URL=None, GITHUB_REPOSITORY=None)
        self.assertEqual(url, astra_env.DEFAULT_URL)
        self.assertEqual(repo, astra_env.DEFAULT_REPO)

    def test_env_override_wins(self):
        url, repo = self._reload(ASTRA_URL="https://new.example.com",
                                 GITHUB_REPOSITORY="someone/else")
        self.assertEqual(url, "https://new.example.com")
        self.assertEqual(repo, "someone/else")

    def test_trailing_slash_is_normalised(self):
        url, _ = self._reload(ASTRA_URL="https://new.example.com/",
                              GITHUB_REPOSITORY=None)
        self.assertEqual(url, "https://new.example.com")


if __name__ == "__main__":
    unittest.main()
