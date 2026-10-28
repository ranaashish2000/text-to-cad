"""Skills pin their CLI/bootstrap and project dependency to the current release.

Project Python executes model source. It can use a separate environment from the
server because the selected engine launches project workers with matching cadgen.
Skills that only hand off a cadgen command to another skill need no dependency.
"""

from __future__ import annotations

import re
import unittest
from pathlib import Path

from tests.python.support.paths import add_repo_path

add_repo_path("packages/cadgen/src")
from cadgen.cli import read_skill_pin  # noqa: E402

REPO_ROOT = Path(__file__).resolve().parents[3]
SKILLS = sorted(p for p in (REPO_ROOT / "skills").iterdir() if p.is_dir())

VERSION = (REPO_ROOT / "VERSION").read_text(encoding="utf-8").strip()
LAUNCH = "uvx --no-config --managed-python --python 3.13 --from cadgen=={version} {tool}"
_HANDOFF_MARKER = re.compile(r"\$(?P<name>[a-z0-9-]+)")


def _own_lines(skill: Path, text: str) -> str:
    """Drop lines that route the agent to ANOTHER skill (`$cad`, `$dxf`, ...).

    A remediation line like "export an STL with $cad: `cadgen stl build ...`"
    teaches the CAD skill's command, not a dependency of the skill that says it
    — that skill installs nothing and runs nothing; the named skill's own
    requirements cover the command.
    """
    kept = []
    for line in text.splitlines():
        markers = {m.group("name") for m in _HANDOFF_MARKER.finditer(line)}
        if markers and skill.name not in markers:
            continue
        kept.append(line)
    return "\n".join(kept)


def _imports_cadgen(skill: Path) -> bool:
    return any(
        "cadgen" in _own_lines(skill, path.read_text(encoding="utf-8"))
        for path in skill.rglob("*.py")
        if "__pycache__" not in path.parts
    )


_CADGEN_INVOCATION = re.compile(r"(?:^|[`\s])cadgen\s+[a-z]", re.M)


def _docs_text(skill: Path) -> str:
    return "\n".join(
        _own_lines(skill, path.read_text(encoding="utf-8"))
        for path in skill.rglob("*.md")
        if "__pycache__" not in path.parts
    )


def _teaches_cadgen(skill: Path) -> bool:
    """The skill's docs instruct the agent to run the cadgen CLI (or its code imports it)."""
    return _imports_cadgen(skill) or bool(_CADGEN_INVOCATION.search(_docs_text(skill)))


class SkillLaunchCommand(unittest.TestCase):
    def test_skills_were_found(self) -> None:
        self.assertGreaterEqual(len(SKILLS), 8, "the skills/ glob found almost nothing")

    def test_every_skill_that_uses_cadgen_names_its_version_and_install_command(self) -> None:
        for skill in SKILLS:
            text = (skill / "SKILL.md").read_text(encoding="utf-8")
            with self.subTest(skill=skill.name):
                if not _teaches_cadgen(skill):
                    self.assertNotIn("--from cadgen==", text, "a skill that never runs cadgen downloads nothing")
                    continue
                self.assertIn(f"`cadgen` below means `~/.local/bin/cadgen +{VERSION}`", text)
                self.assertEqual(read_skill_pin(skill / "SKILL.md"), VERSION)
                setup = skill / "references" / "setup.md"
                self.assertRegex(text, r"\]\(references/setup\.md(?:#[^)]*)?\)", "runtime setup must be reachable from the skill")
                self.assertTrue(setup.is_file(), "the installed skill carries its own setup guide")
                guide = setup.read_text(encoding="utf-8")
                self.assertEqual(read_skill_pin(setup), VERSION)
                self.assertIn(LAUNCH.format(version=VERSION, tool="cadgen server"), guide)
                self.assertIn(LAUNCH.format(version=VERSION, tool="cadgen doctor"), guide)
                if skill.name in {"cad", "dxf", "engineering-drawing"}:
                    self.assertIn("`python` below means the project's Python interpreter", text)
                    self.assertIn(f"python -m pip install cadgen=={VERSION}", guide)
                    self.assertIn(f"uv pip install --python <project-python> cadgen=={VERSION}", guide)
                self.assertNotRegex(text, r"`python` below means `cadgen[^`]*`")
                self.assertIn("CADGEN_PORT", text + guide)

    def test_the_launch_command_and_the_refusal_are_cadgens_own(self) -> None:
        import sys

        sys.path.insert(0, str(REPO_ROOT / "packages" / "cadgen" / "src"))
        from cadgen._internal.launch import launch_command, pinned
        from cadgen._internal.proxy import NOT_INSTALLED

        self.assertEqual(launch_command("cadgen server", VERSION), LAUNCH.format(version=VERSION, tool="cadgen server"))
        self.assertEqual(pinned(VERSION), f"cadgen +{VERSION}")
        # The line the skills tell the agent to act on is the one the command says.
        self.assertIn(f"{VERSION} is not installed", NOT_INSTALLED.format(version=VERSION))
        self.assertIn(LAUNCH.format(version=VERSION, tool="cadgen doctor"), NOT_INSTALLED.format(version=VERSION))

if __name__ == "__main__":
    unittest.main()
