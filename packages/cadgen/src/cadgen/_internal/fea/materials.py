"""The built-in isotropic material table for ``cadgen fea``.

Textbook room-temperature values for the alloys and polymers a desktop part is usually made of. They are starting
points, not certified data: a study that matters names its own numbers through the study file's ``material`` object,
which overrides any field here. Units: MPa for E and yield; tonne/mm^3 for density (so mass = density x volume in
mm^3 is in tonnes; x1000 for kg). Stdlib only.
"""

from __future__ import annotations

import re
from dataclasses import dataclass
from typing import Any

from cadgen._internal.fea.values import number as _number

__all__ = ["MATERIALS", "MATERIAL_KEYS", "Material", "lookup_material", "material_from_spec"]


@dataclass(frozen=True)
class Material:
    name: str
    #: Young's modulus, MPa.
    E: float
    #: Poisson's ratio.
    nu: float
    #: Yield strength, MPa. Safety factor = yield / max von Mises.
    yield_strength: float
    #: tonne/mm^3; 0 when an object leaves it out (a body load then asks for it).
    density: float

    def as_dict(self) -> dict:
        return {"name": self.name, "E_MPa": self.E, "nu": self.nu, "yield_MPa": self.yield_strength,
                "density_t_per_mm3": self.density}


#: The keys a material object takes.
MATERIAL_KEYS = ("name", "E_MPa", "nu", "yield_MPa", "density_t_per_mm3")

# Keyed by the lower-case name the study file may use; aliases below.
MATERIALS: dict[str, Material] = {
    "steel": Material("Steel (structural, generic)", 200_000.0, 0.30, 250.0, 7.85e-9),
    "stainless-304": Material("Stainless steel 304", 193_000.0, 0.29, 215.0, 8.00e-9),
    "aluminum-6061-t6": Material("Aluminum 6061-T6", 68_900.0, 0.33, 276.0, 2.70e-9),
    "aluminum-7075-t6": Material("Aluminum 7075-T6", 71_700.0, 0.33, 503.0, 2.81e-9),
    "titanium-6al-4v": Material("Titanium Ti-6Al-4V", 113_800.0, 0.342, 880.0, 4.43e-9),
    "brass": Material("Brass (C36000)", 97_000.0, 0.31, 310.0, 8.50e-9),
    "abs": Material("ABS", 2_200.0, 0.35, 40.0, 1.04e-9),
    "pla": Material("PLA", 3_500.0, 0.36, 60.0, 1.24e-9),
    "nylon-pa12": Material("Nylon PA12", 1_700.0, 0.40, 48.0, 1.01e-9),
    "petg": Material("PETG", 2_100.0, 0.38, 50.0, 1.27e-9),
}

_ALIASES = {
    **dict.fromkeys(("6061", "6061-t6", "al6061", "aluminium-6061-t6", "aluminum", "aluminium"), "aluminum-6061-t6"),
    **dict.fromkeys(("7075", "7075-t6"), "aluminum-7075-t6"),
    **dict.fromkeys(("304", "stainless", "ss304"), "stainless-304"),
    "mild-steel": "steel",
    **dict.fromkeys(("titanium", "ti-6al-4v", "ti6al4v"), "titanium-6al-4v"),
    **dict.fromkeys(("nylon", "pa12"), "nylon-pa12"),
}


def _key(name: str) -> str:
    return name.strip().lower().replace("_", "-").replace(" ", "-")


def lookup_material(name: str) -> Material:
    """The table entry for ``name`` (case-insensitive, common aliases), or a ValueError that lists what is available."""
    key = _key(name)
    material = MATERIALS.get(_ALIASES.get(key, key))
    if material is None:
        raise ValueError(
            f"unknown material {name!r}; one of {', '.join(sorted(MATERIALS))}, "
            "or give an object with E_MPa, nu and yield_MPa"
        )
    return material


def display_name(name: str) -> str:
    """An object material's own name, tidied as the table's names read: its first letter capitalised ("soft silicone" is
    "Soft silicone"), and a slug's words spaced, an alloy code among them in capitals ("copper-c110" is "Copper C110").
    Nothing else changes: "rubber 50A (estimate)" is "Rubber 50A (estimate)"."""
    text = " ".join(name.split())
    if not text:
        return "Custom"
    if " " not in text and re.search(r"[A-Za-z][-_]+[A-Za-z0-9]", text):
        text = " ".join(word.upper() if any(c.isdigit() for c in word) else word for word in re.split(r"[-_]+", text) if word)
    return text[0].upper() + text[1:]


def material_from_spec(spec: Any) -> Material:
    """The study's ``material``: a table name, or an object that gives the numbers or extends a table entry.

    An object may extend a table entry (``{"name": "steel", "yield_MPa": 355}``), and is then called by the entry's
    display name; one with no table name needs E_MPa, nu and yield_MPa, and is called by its own name, tidied.
    """
    if isinstance(spec, str):
        return lookup_material(spec)
    if not isinstance(spec, dict):
        raise ValueError("material: give a name from the table or an object with E_MPa, nu and yield_MPa")
    unknown = set(spec) - set(MATERIAL_KEYS)
    if unknown:
        raise ValueError(f"material: unknown keys {sorted(unknown)}; a material object takes {', '.join(MATERIAL_KEYS)}")
    base = None
    if isinstance(spec.get("name"), str):
        try:
            base = lookup_material(spec["name"])
        except ValueError:
            pass
    if base is None and not {"E_MPa", "nu", "yield_MPa"} <= set(spec):
        raise ValueError("material: an object needs E_MPa, nu and yield_MPa (density_t_per_mm3 optional)")
    E = _number(spec.get("E_MPa", base.E if base else None), where="material.E_MPa", positive=True)
    nu = _number(spec.get("nu", base.nu if base else None), where="material.nu")
    if not 0 <= nu < 0.5:
        raise ValueError(f"material.nu: Poisson's ratio must be in [0, 0.5), got {nu}")
    yield_strength = _number(spec.get("yield_MPa", base.yield_strength if base else None), where="material.yield_MPa",
                             positive=True)
    density = _number(spec.get("density_t_per_mm3", base.density if base else 0.0), where="material.density_t_per_mm3")
    if density < 0:
        raise ValueError(f"material.density_t_per_mm3: must be zero or more, got {density:g}")
    name = base.name if base is not None else display_name(str(spec.get("name", "custom")))
    return Material(name, E, nu, yield_strength, density)
