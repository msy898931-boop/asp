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

    def test_blank_env_falls_back_to_default(self):
        # A workflow writing `ASTRA_URL: ${{ vars.ASTRA_URL }}` sets it to the
        # empty string when the repo variable is absent; that must not become
        # the host.
        url, repo = self._reload(ASTRA_URL="", GITHUB_REPOSITORY="   ")
        self.assertEqual(url, astra_env.DEFAULT_URL)
        self.assertEqual(repo, astra_env.DEFAULT_REPO)

    def test_blank_env_reports_default_to_callers(self):
        # daily_report builds its URL from astra_env.URL; a blank env must not
        # produce "/tracker/v1/signals".
        self._reload(ASTRA_URL="", GITHUB_REPOSITORY=None)
        self.assertTrue(astra_env.URL.startswith("https://"))

    def _reload_auth(self, token):
        saved = os.environ.get("ASTRA_ACCESS_TOKEN")
        try:
            if token is None:
                os.environ.pop("ASTRA_ACCESS_TOKEN", None)
            else:
                os.environ["ASTRA_ACCESS_TOKEN"] = token
            return importlib.reload(astra_env).auth_headers()
        finally:
            if saved is None:
                os.environ.pop("ASTRA_ACCESS_TOKEN", None)
            else:
                os.environ["ASTRA_ACCESS_TOKEN"] = saved

    def test_no_token_sends_no_cookie(self):
        # The gate is dormant by default; monitoring must stay unauthenticated.
        self.assertEqual(self._reload_auth(None), {})
        self.assertEqual(self._reload_auth("   "), {})

    def test_token_becomes_the_access_cookie(self):
        # The cookie name must match what serve_mobile.py sets on a ?k= visit.
        self.assertEqual(self._reload_auth("s3cret"),
                         {"Cookie": "astra_key=s3cret"})


if __name__ == "__main__":
    unittest.main()
