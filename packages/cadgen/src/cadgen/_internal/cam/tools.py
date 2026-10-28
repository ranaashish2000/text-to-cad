"""Tools: the closed tool vocabulary a job's ``tools`` list is read in (:func:`parse_tool`).

A tool is one of FreeCAD's built-in tool bit shapes (``freecad_job.SHAPES``): ``flat``, ``ball``, ``bull`` or ``drill``. What a job leaves out is filled in here, and only here, and every such length is listed in
:attr:`Tool.assumed` so the plan says it guessed:

* ``flute_length_mm``: three diameters (a drill's ten), cut back to a stated ``stickout_mm``;
* ``stickout_mm``: the flutes and a quarter more (at least 5 mm more);
* ``shank_diameter_mm``: the diameter.

Stdlib only.
"""

from __future__ import annotations

import math
from dataclasses import asdict, dataclass

from cadgen._internal.cam.job_errors import CamJobError, check_keys, choice, read_number, shown

__all__ = ["TOOL_KEYS", "TOOL_TYPES", "Tool", "parse_tool"]

#: Each tool type: the keys only it takes, and its plain name.
TOOL_TYPES: dict[str, dict] = {
    "flat": {"keys": (), "plain": "flat"},
    "ball": {"keys": (), "plain": "ball"},
    "bull": {"keys": ("corner_radius_mm",), "plain": "bull nose"},
    "drill": {"keys": ("point_angle",), "plain": "drill"},
}
#: The keys every tool takes. ``number`` is the T number posted (default: the digits of ``id``, else its position).
TOOL_KEYS = ("id", "type", "number", "name", "diameter_mm", "flutes", "flute_length_mm", "stickout_mm",
             "shank_diameter_mm")
#: Past these a length is a units slip (mm); the highest T number a controller takes.
MIN_TOOL_MM = 0.01
MAX_TOOL_MM = 1000.0
MAX_TOOL_NUMBER = 9999


@dataclass(frozen=True)
class Tool:
    id: str
    type: str
    number: int
    diameter_mm: float
    flutes: int
    flute_length_mm: float
    stickout_mm: float
    shank_diameter_mm: float
    name: str = ""
    corner_radius_mm: float = 0.0
    point_angle: float = 118.0
    #: The lengths and counts the job left to the defaults above: the plan lists each as assumed.
    assumed: tuple[str, ...] = ()

    @property
    def radius(self) -> float:
        return self.diameter_mm / 2.0

    @property
    def plain(self) -> str:
        """The viewer's words for it: ``6 mm flat``, ``6 mm R1 bull nose``."""
        if self.type == "bull":
            return f"{self.diameter_mm:g} mm R{self.corner_radius_mm:g} bull nose"
        return f"{self.diameter_mm:g} mm {TOOL_TYPES[self.type]['plain']}"

    @property
    def tip_length_mm(self) -> float:
        """How far a drill's point reaches past its full diameter."""
        return self.radius / math.tan(math.radians(self.point_angle / 2.0)) if self.type == "drill" else 0.0

    def as_dict(self) -> dict:
        """The tool as the GLB's extras and the Results carry it, with ``plain``."""
        out = {key: value for key, value in asdict(self).items() if key != "assumed"}
        out["plain"] = self.plain
        return out


def parse_tool(raw: object, position: int) -> Tool:
    """One entry of ``tools``: closed keys per type, defaults filled in, teaching errors naming the allowed set."""
    where = f"tools[{position}]"
    if not isinstance(raw, dict):
        raise CamJobError(f"{where} must be an object like {{\"id\": \"T1\", \"type\": \"flat\", \"diameter_mm\": 6}}")
    kind = choice(raw.get("type"), tuple(TOOL_TYPES), f"{where}.type")
    check_keys(raw, (*TOOL_KEYS, *TOOL_TYPES[kind]["keys"]), where, hint=f"a {kind} tool")
    tool_id = raw.get("id")
    if not isinstance(tool_id, str) or not tool_id.strip() or tool_id != tool_id.strip() or len(tool_id) > 40:
        raise CamJobError(f"{where} needs an \"id\": a short name the operations use, like \"T1\" (not "
                          f"{shown(tool_id)})")
    number = raw.get("number")
    if number is None:
        digits = "".join(ch for ch in tool_id if ch.isdigit())
        number = int(digits) if digits else position + 1
    if isinstance(number, bool) or not isinstance(number, int) or not 0 <= number <= MAX_TOOL_NUMBER:
        raise CamJobError(f"{where}.number must be a whole number from 0 to {MAX_TOOL_NUMBER} (the T number posted)")
    if "diameter_mm" not in raw:
        raise CamJobError(f"{where} ({tool_id}, a {kind} tool) needs \"diameter_mm\"")
    diameter = read_number(raw, "diameter_mm", where, minimum=MIN_TOOL_MM)
    flutes = raw.get("flutes", 2)
    if isinstance(flutes, bool) or not isinstance(flutes, int) or not 1 <= flutes <= 100:
        raise CamJobError(f"{where}.flutes must be a whole number from 1 to 100")
    stickout = read_number(raw, "stickout_mm", where, minimum=MIN_TOOL_MM)
    default_flutes = (10.0 if kind == "drill" else 3.0) * diameter
    if stickout is not None:
        default_flutes = min(default_flutes, stickout)      # a stated stickout wins over a defaulted flute length
    flute_length = read_number(raw, "flute_length_mm", where, default=default_flutes, minimum=MIN_TOOL_MM)
    if stickout is None:
        stickout = max(flute_length * 1.25, flute_length + 5.0)
    if stickout < flute_length:
        raise CamJobError(f"{where}: stickout_mm ({stickout:g}) is shorter than flute_length_mm ({flute_length:g}); "
                          "the stickout is the length out of the holder, flutes included")
    tool = Tool(
        id=tool_id, type=kind, number=number, diameter_mm=diameter, flutes=flutes, flute_length_mm=flute_length,
        stickout_mm=stickout, shank_diameter_mm=read_number(raw, "shank_diameter_mm", where, default=diameter,
                                                            minimum=MIN_TOOL_MM),
        name=str(raw.get("name", "")),
        corner_radius_mm=read_number(raw, "corner_radius_mm", where, default=0.0, minimum=0.0),
        point_angle=read_number(raw, "point_angle", where, default=118.0),
        # A drill is fed per revolution: its flute count sets nothing.
        assumed=tuple(key for key in ("flute_length_mm", "stickout_mm", "flutes")
                      if key not in raw and not (key == "flutes" and kind == "drill")),
    )
    for key in ("diameter_mm", "flute_length_mm", "stickout_mm", "shank_diameter_mm"):
        if getattr(tool, key) > MAX_TOOL_MM:
            raise CamJobError(f"{where}.{key} is {getattr(tool, key):g} mm; no cutter is over {MAX_TOOL_MM:g} mm: "
                              "check the units (every length in a job is mm)")
    if kind == "bull" and not 0 < tool.corner_radius_mm <= tool.radius:
        raise CamJobError(f"{where}: a bull nose needs corner_radius_mm, more than 0 and at most its radius")
    if kind == "drill" and not 60 <= tool.point_angle <= 180:
        raise CamJobError(f"{where}.point_angle must be between 60 and 180 degrees (118 or 135 for most drills)")
    return tool
