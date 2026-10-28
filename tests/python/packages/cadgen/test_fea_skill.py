"""The fea skill's pages against the code, and the package's own rules: parsing imports nothing heavy, no module
reaches into another's private names. Stdlib only."""

from __future__ import annotations

import ast
import re
import unittest
from pathlib import Path

from tests.python.support.fea_fixtures import heavy_at_import

SKILL = Path(__file__).resolve().parents[4] / "skills" / "fea"
FEA = Path(__file__).resolve().parents[4] / "packages" / "cadgen" / "src" / "cadgen" / "_internal" / "fea"


def _pages() -> list[Path]:
    return [SKILL / "SKILL.md", *sorted((SKILL / "references").glob("*.md"))]


class StdlibAtImport(unittest.TestCase):
    def test_the_study_the_checks_and_the_static_module_load_without_the_solver_stack(self):
        # A malformed study fails before numpy, scipy, netgen or OCP is imported.
        modules = ("cadgen._internal.fea.study", "cadgen._internal.fea.checks", "cadgen._internal.fea.static")
        self.assertEqual(heavy_at_import(*modules), "")


class SkillDocs(unittest.TestCase):
    def test_nothing_removed_is_still_taught(self):
        stale = (
            "`fit`", "fit.allow", "adapted:", "ladder", "linear_elements", "local_refine", "defeature", "idealise",
            "built-in solver", "downloads it once", "downloading the CalculiX", "CADGEN_CCX=0", "scikit-fem", "pyamg",
            "Gauss-point peak is", "always higher", "Lite limits", "`analysis`", "`view`", "presets", "threshold",
            "planned-next", "piezo", "orthotropic",
        )
        for path in _pages():
            text = " ".join(path.read_text(encoding="utf-8").split())
            for phrase in stale:
                with self.subTest(page=path.name, phrase=phrase):
                    self.assertNotIn(phrase, text)

    def test_the_study_keys_and_the_check_kinds_are_the_parsers(self):
        from cadgen._internal.fea.study import CHECK_KEYS, STUDY_KEYS

        text = (SKILL / "references" / "study-file.md").read_text(encoding="utf-8")
        keys = text.split("## Keys", 1)[1].split("\n## ", 1)[0]
        self.assertEqual(sorted(set(re.findall(r"^\| `([a-z_]+)`", keys, re.M)) | {"connections", "contact_tolerance_mm"}),
                         sorted(STUDY_KEYS))
        checks = text.split("## `checks`", 1)[1].split("\n## ", 1)[0]
        self.assertEqual(re.findall(r"^\| `([a-z_]+)` \|", checks, re.M)[1:], list(CHECK_KEYS))  # past the header

    def test_the_setup_names_the_mesher_and_the_installed_solver(self):
        from cadgen._internal.fea.calculix import CCX_ENV, MINIMUM, install_hint

        text = " ".join((SKILL / "SKILL.md").read_text(encoding="utf-8").split())
        setup = text.split("## Setup", 1)[1].split("## Workflow", 1)[0]
        self.assertIn(f"`{CCX_ENV}`", setup)
        self.assertIn(f"{MINIMUM[0]}.{MINIMUM[1]} or newer", setup)
        self.assertIn("conda install -c conda-forge calculix", install_hint())
        self.assertIn("conda install -c conda-forge calculix", setup)
        self.assertIn("calculix-ccx", setup)
        self.assertIn("netgen", setup)
        self.assertIn("downloads nothing", setup)

    def test_the_nodal_peak_is_the_one_documented_as_usually_higher(self):
        for path in (SKILL / "SKILL.md", SKILL / "references" / "linear-static.md"):
            text = " ".join(path.read_text(encoding="utf-8").split())
            with self.subTest(page=path.name):
                self.assertIn("usually the higher of the two", text)

    def test_the_description_is_short_and_every_reference_page_is_linked(self):
        text = (SKILL / "SKILL.md").read_text(encoding="utf-8")
        description = re.search(r"^description: (.*)$", text, re.M).group(1)
        self.assertLess(len(description), 1024)
        self.assertNotIn("—", description)
        for page in (SKILL / "references").glob("*.md"):
            self.assertIn(f"references/{page.name}", text)

    def test_every_relative_link_lands_on_a_page_and_its_heading(self):
        def anchor(heading: str) -> str:
            slug = re.sub(r"[^\w\- ]", "", heading.strip().lower())
            return slug.replace(" ", "-")

        anchors = {}
        for path in _pages():
            headings = re.findall(r"^#{1,6} (.+)$", path.read_text(encoding="utf-8"), re.M)
            anchors[path.resolve()] = {anchor(heading) for heading in headings}
        for path in _pages():
            for target in re.findall(r"\]\(([^)\s]+)\)", path.read_text(encoding="utf-8")):
                if re.match(r"[a-z]+:", target):
                    continue
                page, _, fragment = target.partition("#")
                resolved = (path.parent / page).resolve() if page else path.resolve()
                with self.subTest(page=path.name, link=target):
                    self.assertIn(resolved, anchors, "links to a page the skill does not ship")
                    if fragment:
                        self.assertIn(fragment, anchors[resolved])


class PrivateNames(unittest.TestCase):
    """No fea module reaches into another's private names: each shares what others use under a public name."""

    def test_no_module_imports_or_reads_another_modules_private_name(self):
        found = []
        for path in sorted(FEA.rglob("*.py")):
            tree = ast.parse(path.read_text(encoding="utf-8"))
            own = "cadgen._internal." + ".".join(path.relative_to(FEA.parent).with_suffix("").parts)
            modules = {}  # a name bound to a cadgen module in this file -> that module
            for node in ast.walk(tree):
                if isinstance(node, ast.ImportFrom) and (node.module or "").startswith("cadgen"):
                    for alias in node.names:
                        if alias.name.startswith("_") and not alias.name.startswith("__") and node.module != own:
                            found.append(f"{path.name}:{node.lineno} from {node.module} import {alias.name}")
                        modules[alias.asname or alias.name] = f"{node.module}.{alias.name}"
                elif isinstance(node, ast.Import):
                    for alias in node.names:
                        if alias.name.startswith("cadgen"):
                            modules[alias.asname or alias.name.split(".")[0]] = alias.name
            for node in ast.walk(tree):
                if (isinstance(node, ast.Attribute) and node.attr.startswith("_") and not node.attr.startswith("__")
                        and isinstance(node.value, ast.Name) and node.value.id in modules):
                    found.append(f"{path.name}:{node.lineno} {node.value.id}.{node.attr}")
        self.assertEqual(found, [])


if __name__ == "__main__":
    unittest.main()
