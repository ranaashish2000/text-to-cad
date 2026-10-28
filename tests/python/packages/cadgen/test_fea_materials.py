"""``cadgen._internal.fea.materials``: the table and the material object.

Stdlib only: no FEA stack. The table and the skill's materials reference must agree number for number.
"""

from __future__ import annotations

import re
import unittest
from pathlib import Path

from tests.python.support.fea_fixtures import value_error

from cadgen._internal.fea.materials import MATERIALS, lookup_material, material_from_spec  # noqa: E402

REFERENCE = Path(__file__).resolve().parents[4] / "skills" / "fea" / "references" / "materials.md"


def _reference_rows() -> dict[str, list[float]]:
    """materials.md's table: table key -> E, ν, yield and density as written."""
    rows = {}
    for line in REFERENCE.read_text(encoding="utf-8").splitlines():
        match = re.match(r"\|\s*`([a-z0-9-]+)`", line)
        if match:
            cells = [cell.strip() for cell in line.strip().strip("|").split("|")][1:5]
            rows[match.group(1)] = [float(cell.replace(" ", "").replace(" ", "")) for cell in cells]
    return rows


class Table(unittest.TestCase):
    def test_the_reference_gives_the_tables_numbers(self):
        rows = _reference_rows()
        self.assertEqual(set(rows), set(MATERIALS))
        for key, material in MATERIALS.items():
            with self.subTest(material=key):
                self.assertEqual(rows[key], [material.E, material.nu, material.yield_strength, material.density])


class MaterialObject(unittest.TestCase):
    def test_a_name_reads_the_table(self):
        self.assertIs(material_from_spec("6061"), lookup_material("aluminum-6061-t6"))

    def test_an_object_extends_a_table_entry(self):
        steel = material_from_spec({"name": "steel", "yield_MPa": 355})
        self.assertEqual((steel.name, steel.E, steel.yield_strength), (MATERIALS["steel"].name, MATERIALS["steel"].E, 355.0))

    def test_an_object_is_called_by_a_tidy_name(self):
        """A table entry's display name where the name is one or an alias; an object's own name tidied, never left raw."""
        own = {"E_MPa": 10, "nu": 0.45, "yield_MPa": 8}
        names = {
            "steel": "Steel (structural, generic)", "Mild_Steel": "Steel (structural, generic)", "stainless": "Stainless steel 304",
            "copper-c110": "Copper C110", "soft silicone": "Soft silicone", "rubber reed": "Rubber reed",
            "rubber 50A (estimate)": "Rubber 50A (estimate)", "Viton": "Viton",
        }
        for written, shown in names.items():
            with self.subTest(written=written):
                self.assertEqual(material_from_spec({"name": written, **own}).name, shown)
        self.assertEqual(material_from_spec(own).name, "Custom")

    def test_the_errors_name_the_field(self):
        cases = [
            ({"name": "steel", "uts_MPa": 400}, "material: unknown keys ['uts_MPa']; a material object takes name, E_MPa, nu"),
            ({"name": "steel", "nu": 0.5}, "material.nu: Poisson's ratio must be in [0, 0.5)"),
            ({"name": "steel", "yield_MPa": 0}, "material.yield_MPa: must be > 0"),
            ({"name": "steel", "density_t_per_mm3": -1}, "material.density_t_per_mm3: must be zero or more"),
            ({"E_MPa": 1}, "an object needs E_MPa, nu and yield_MPa"),
            ("unobtainium", "unknown material 'unobtainium'"),
            (3, "give a name from the table"),
        ]
        for spec, fragment in cases:
            with self.subTest(fragment=fragment):
                self.assertIn(fragment, value_error(self, material_from_spec, spec))

    def test_as_dict_names_the_five_numbers(self):
        custom = material_from_spec({"E_MPa": 1000, "nu": 0.3, "yield_MPa": 10})
        self.assertEqual(custom.as_dict(), {"name": "Custom", "E_MPa": 1000.0, "nu": 0.3, "yield_MPa": 10.0, "density_t_per_mm3": 0.0})


if __name__ == "__main__":
    unittest.main()
