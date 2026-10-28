"""One setup's toolpaths, planned and posted by FreeCAD's CAM workbench: run by ``freecadcmd`` (``freecad.run``), never
imported by cadgen.

It reads ``plan.json`` from the folder ``CADGEN_CAM_FOLDER`` names, with the part beside it as ``part.brep``, already
placed in the setup's work coordinates (the tool comes from +Z, the origin is the work zero). It makes one FreeCAD
job: the part, a box of stock, a tool controller per tool and feed, and one FreeCAD operation per cadgen operation,
its faces named ``FaceN`` (the N of cadgen's ``face N``: both number a solid's faces in the same order). It writes
``result.json``: each operation's path commands, in the order run, and the files posted, each holding the operations
``plan["files"]`` gives it. Any failure is ``{"error": ...}`` in ``result.json``.

FreeCAD's Python, FreeCAD's modules and the standard library only.
"""

import json
import os
import traceback

FOLDER = os.environ["CADGEN_CAM_FOLDER"]
#: cadgen's operation -> the FreeCAD module that makes it (``Path.Op.<module>.Create``).
OPERATIONS = {"face": "MillFace", "profile": "Profile", "pocket": "PocketShape", "adaptive": "Adaptive",
              "drill": "Drilling"}
#: cadgen's tool type -> FreeCAD's built-in tool bit shape.
SHAPES = {"flat": "endmill.fcstd", "ball": "ballend.fcstd", "bull": "bullnose.fcstd", "drill": "drill.fcstd"}
COOLANT = {"off": "None", "flood": "Flood", "mist": "Mist"}


def _tool(doc, spec):
    from Path.Tool.toolbit import ToolBit

    bit = ToolBit.from_shape_id(SHAPES[spec["type"]], label=spec["id"]).attach_to_doc(doc=doc)
    bit.Diameter = f"{spec['diameter_mm']} mm"
    bit.Flutes = spec["flutes"]
    bit.Length = f"{spec['stickout_mm']} mm"
    if hasattr(bit, "CuttingEdgeHeight"):
        bit.CuttingEdgeHeight = f"{spec['flute_length_mm']} mm"
    if hasattr(bit, "ShankDiameter"):
        bit.ShankDiameter = f"{spec['shank_diameter_mm']} mm"
    if spec["type"] == "bull":
        bit.CornerRadius = f"{spec['corner_radius_mm']} mm"
    if spec["type"] == "drill":
        bit.TipAngle = f"{spec['point_angle']} deg"
    return bit


def _controller(job, bit, number, feeds):
    import Path.Tool.Controller as Controller

    tc = Controller.Create(f"T{number} {feeds['rpm']:g} rpm", tool=bit, toolNumber=number)
    tc.SpindleSpeed = float(feeds["rpm"])
    tc.SpindleDir = "Forward"
    tc.HorizFeed = f"{feeds['feed_mm_min']} mm/min"
    tc.VertFeed = f"{feeds['plunge_mm_min']} mm/min"
    tc.HorizRapid = tc.VertRapid = "0 mm/min"
    job.Proxy.addToolController(tc)
    return tc


def _operation(model, spec, tc, plan):
    import importlib

    module = importlib.import_module(f"Path.Op.{OPERATIONS[spec['op']]}")
    op = module.Create(f"{spec['index']} {spec['op']}")
    op.ToolController = tc
    op.CoolantMode = COOLANT[spec["coolant"]]
    if spec["faces"]:
        op.Base = [(model, [f"Face{n}" for n in spec["faces"]])]
    kind = spec["op"]
    if kind == "face":
        op.BoundaryShape = "Stock"
        op.StepOver = round(spec["stepover"] * 100)
    if kind == "profile":
        outside = spec["side"] == "outside"
        op.Side = "Outside" if outside else "Inside"
        op.Direction = "CW" if outside else "CCW"       # climb milling, the spindle turning clockwise
        # Only the named faces' outlines: FreeCAD's own hole and circle finding cuts holes nobody named and skips ones
        # that were (an inside profile names the walls it runs along).
        op.processPerimeter, op.processHoles, op.processCircles = True, False, False
        op.OffsetExtra = f"{spec['leave_mm']} mm"
    if kind == "pocket":
        op.StepOver = round(spec["stepover"] * 100)
        op.ExtraOffset = f"{-spec['leave_mm']} mm"
    if kind == "adaptive":
        op.OperationType = "Clearing"
        op.Side = "Inside"
        op.StepOver = round(spec["stepover"] * 100)
        op.StockToLeave = f"{spec['leave_mm']} mm"
        op.Tolerance = plan["tolerance_mm"]
    if kind == "drill":
        op.PeckEnabled = spec.get("peck_mm") is not None
        if op.PeckEnabled:
            op.PeckDepth = f"{spec['peck_mm']} mm"
        op.AddTipLength = False     # cadgen's depth already takes the point and the break through
    if kind != "drill":
        op.StepDown = f"{spec['stepdown_mm']} mm"
    # The depth cadgen worked out wins over FreeCAD's own reading of the faces: the expression is cleared first, or the
    # next recompute puts FreeCAD's back.
    if spec.get("final_z") is not None:
        op.setExpression("FinalDepth", None)
        op.FinalDepth = f"{spec['final_z']} mm"
    return op


class _Fixture:
    """The work offset a posted file starts with, as FreeCAD's own job hands its posts one."""

    def __init__(self, word):
        import Path

        self.Name = self.Label = "Fixture"
        self.Path = Path.Path([Path.Command(word)])


def main():
    import FreeCAD
    import Part

    with open(os.path.join(FOLDER, "plan.json"), encoding="utf-8") as handle:
        plan = json.load(handle)
    doc = FreeCAD.newDocument("cadgen")
    model = doc.addObject("Part::Feature", "Part")
    model.Shape = Part.read(os.path.join(FOLDER, "part.brep"))
    doc.recompute()

    import Path.Main.Job as PathJob
    import Path.Main.Stock as PathStock

    job = PathJob.Create("Job", [model])
    for tc in list(job.Tools.Group):            # FreeCAD's default 5 mm end mill
        job.Proxy.removeToolController(tc) if hasattr(job.Proxy, "removeToolController") else None
        doc.removeObject(tc.Tool.Name)
        doc.removeObject(tc.Name)
    low, high = plan["stock"]["min"], plan["stock"]["max"]
    old = job.Stock
    job.Stock = PathStock.CreateBox(job, FreeCAD.Vector(*(h - l for l, h in zip(low, high))),
                                    FreeCAD.Placement(FreeCAD.Vector(*low), FreeCAD.Rotation()))
    doc.removeObject(old.Name)
    job.SetupSheet.SafeHeightOffset = f"{plan['retract_mm']} mm"
    job.SetupSheet.ClearanceHeightOffset = f"{plan['clearance_mm']} mm"
    job.GeometryTolerance = f"{plan['tolerance_mm']} mm"
    doc.recompute()

    bits = {spec["id"]: _tool(doc, spec) for spec in plan["tools"]}
    numbers = {spec["id"]: spec["number"] for spec in plan["tools"]}
    controllers, ops = {}, []
    for spec in plan["operations"]:
        feeds = spec["feeds"]
        key = (spec["tool"], feeds["rpm"], feeds["feed_mm_min"], feeds["plunge_mm_min"])
        if key not in controllers:
            controllers[key] = _controller(job, bits[spec["tool"]], numbers[spec["tool"]], feeds)
        ops.append((spec, controllers[key], _operation(model, spec, controllers[key], plan)))
    doc.recompute()

    out = {"freecad": ".".join(FreeCAD.Version()[:3]), "operations": [], "posts": []}
    for spec, _, op in ops:
        commands = [[c.Name, {k: float(v) for k, v in c.Parameters.items()}] for c in op.Path.Commands]
        out["operations"].append({"index": spec["index"], "commands": commands})

    import importlib

    post = importlib.import_module(f"Path.Post.scripts.{plan['post']}_post")
    by_index = {spec["index"]: (tc, op) for spec, tc, op in ops}
    for name, indices in plan["files"]:
        objects, last_tc = [_Fixture(plan["work_offset"])], None
        for index in indices:
            tc, op = by_index[index]
            if tc is not last_tc:
                objects.append(tc)
                last_tc = tc
            objects.append(op)
        post.export(objects, os.path.join(FOLDER, name), plan["post_args"])
        out["posts"].append(name)
    return out


try:
    result = main()
except Exception as exc:  # noqa: BLE001 - every failure goes back to cadgen as words, never a hang
    result = {"error": f"{type(exc).__name__}: {exc}", "traceback": traceback.format_exc()[-2000:]}
with open(os.path.join(FOLDER, "result.json"), "w", encoding="utf-8") as handle:
    json.dump(result, handle)
