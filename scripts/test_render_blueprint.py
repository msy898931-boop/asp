"""Guards the Render blueprint against drifting from the running service.

``render.yaml`` owns ``ASTRA_ADVISOR_PROVIDER``. A Blueprint sync rewrites the
env vars it declares from the file, so a dashboard edit is silently reverted on
the next deploy — the exact failure that left the advisor pointed at an expired
provider. This test reads the file and pins the two facts that matter: the
declared provider is one the code can actually dispatch, and it matches a key
the blueprint also declares.

Run: python3 -m unittest discover -s scripts -p "test_*.py"
"""

from __future__ import annotations

import os
import re
import sys
import unittest

HERE = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.dirname(HERE)
if os.path.join(ROOT, "advisor") not in sys.path:
    sys.path.insert(0, os.path.join(ROOT, "advisor"))

import ai_advisor as advisor  # noqa: E402

BLUEPRINT = os.path.join(ROOT, "render.yaml")


def _env_block() -> str:
    with open(BLUEPRINT, encoding="utf-8") as fh:
        return fh.read()


def _env_entries() -> dict[str, str | None]:
    """Each declared env key mapped to its literal ``value:``, or None.

    A ``sync: false`` entry carries no value — the dashboard owns it — which is
    a distinct state from a declared empty string. Entries are separated by a
    ``- key:`` line, so the block for one key ends where the next begins.
    """
    entries: dict[str, str | None] = {}
    current: str | None = None
    for raw in _env_block().splitlines():
        line = raw.strip()
        key_match = re.match(r"-\s*key:\s*(\S+)", line)
        if key_match:
            current = key_match.group(1)
            entries[current] = None
            continue
        if current is None:
            continue
        value_match = re.match(r"value:\s*(.*)$", line)
        if value_match and entries[current] is None:
            literal = value_match.group(1).split("#", 1)[0].strip()
            entries[current] = literal.strip('"').strip("'")
    return entries


def _declared_value(key: str) -> str | None:
    return _env_entries().get(key)


class RenderBlueprintTests(unittest.TestCase):
    def test_the_blueprint_declares_the_provider_it_runs(self):
        # A declared value is what a sync enforces, so it must name a provider
        # the code can dispatch rather than a stale one.
        provider = _declared_value("ASTRA_ADVISOR_PROVIDER")
        self.assertIsNotNone(
            provider, "ASTRA_ADVISOR_PROVIDER must be declared in render.yaml")
        self.assertIn(
            provider, advisor._KEY_ENV,
            "render.yaml names a provider the code cannot dispatch: %r" % provider)

    def test_the_declared_provider_has_a_key_entry_beside_it(self):
        # The provider is inert without its key variable; both must be declared
        # so a Blueprint sync cannot leave one without the other.
        provider = _declared_value("ASTRA_ADVISOR_PROVIDER")
        key_var = advisor._KEY_ENV[provider]
        text = _env_block()
        self.assertIn("key: %s" % key_var, text,
                      "render.yaml sets provider %r but does not declare %s"
                      % (provider, key_var))

    def test_the_declared_provider_does_not_need_a_model_override(self):
        # ``ASTRA_ADVISOR_MODEL`` is shared by every provider. Pinning it in the
        # blueprint would silently hand one provider's model name to another, so
        # it must stay out of the file unless it names the declared provider's
        # own model.
        model = _declared_value("ASTRA_ADVISOR_MODEL")
        if model is None:
            return
        provider = _declared_value("ASTRA_ADVISOR_PROVIDER")
        expected = {"groq": advisor.GROQ_MODEL,
                    "gemini": advisor.GEMINI_MODEL,
                    "mistral": advisor.MISTRAL_MODEL}.get(provider)
        self.assertEqual(
            model, expected,
            "render.yaml pins ASTRA_ADVISOR_MODEL=%r, which is not %s's model (%r)"
            % (model, provider, expected))


if __name__ == "__main__":
    unittest.main()
