"""The public ``fea`` namespace: finite element analysis of a part or assembly in a STEP
or an engine document (``.xbf``), its faces named by the engine's refs (``bracket/face 17``).

A study is static: linear elastic strength, with face and body loads, on a part
or a bonded assembly.

Three verbs, each mirrored by a generated CLI (``cadgen fea faces``,
``cadgen fea parts``, ``cadgen fea solve``): ``faces`` lists a part's faces
with the refs a study names them by (``bracket/face 17``), ``parts`` lists an
assembly's parts and the pairs that touch, ``solve`` runs one study
and writes a result GLB the viewer renders (von Mises as vertex colour on the
deformed shape) beside a JSON sidecar with the numbers.

``solve`` requires the ``fea`` extra (``pip install 'cadgen[fea]'``): netgen
for the mesh; and CalculiX (``ccx``), installed by the person, for the solve
(``CADGEN_CCX`` names it where it is not on PATH); ``faces`` and ``parts`` do
not. Import discipline: nothing here may
pull in OCP, build123d or the solver stack at module scope (see
:mod:`cadgen.urdf`) -- ``--help`` must stay cheap, and the extra may be absent.
"""

from __future__ import annotations

from pathlib import Path

from cadgen.results import FeaFacesResult, FeaPartsResult, FeaResult

__all__ = ["faces", "parts", "solve"]


def faces(target: Path, *, occurrence: str | None = None, verbose: bool = False) -> FeaFacesResult:
    """List one part's faces: selector, area, centre, surface type and a hint.

    target: the STEP/STP or .xbf document.
    occurrence: which part occurrence to list (its name or ref, ``arm``) when the document is an
        assembly. Omitted, the document must hold one part.
    verbose: show progress on stderr.
    """
    from cadgen._internal.fea.run import list_faces

    return list_faces(target, occurrence=occurrence, verbose=verbose)


def parts(target: Path, *, contact_tolerance_mm: float = 0.1, verbose: bool = False) -> FeaPartsResult:
    """List an assembly's parts and the pairs that touch, before writing a study.

    target: the STEP/STP or .xbf document.
    contact_tolerance_mm: faces this close (or closer) count as touching and are
        bonded by default; a pair a little farther apart is listed as not connected.
    verbose: show progress on stderr.
    """
    from cadgen._internal.fea.run import list_assembly_parts

    return list_assembly_parts(target, contact_tolerance_mm=contact_tolerance_mm, verbose=verbose)


def solve(
    target: Path,
    out: Path | None = None,
    *,
    study: str | dict | None = None,
    occurrence: str | None = None,
    mesh_size: float | None = None,
    vtu: bool = False,
    verbose: bool = False,
) -> FeaResult:
    """Run one static study and write its result GLB and JSON sidecar.

    target: the STEP/STP or .xbf document holding the part.
    out: the result .glb path. Omitted, writes ``<document>.fea.glb`` beside the
        document; the sidecar is the same name with ``.json``.
    study: the study: a JSON file path, inline JSON, or (library callers) a
        dict. It names the material, the fixed faces and the loads
        (forces, pressures, gravity and acceleration); an unknown or
        malformed key is refused naming the keys it takes. Face refs are the ones ``cadgen fea faces``
        prints. A document of several parts is solved as one bonded assembly:
        ``parts`` gives each its material, ``connections`` overrides which
        touching pairs are bonded (``cadgen fea parts`` lists them).
    occurrence: solve only this part occurrence (its name or ref, ``arm``), alone, even when the
        document has several parts: the study's faces must all be on it and
        ``parts``/``connections`` are not used. Omitted, a document of several
        parts is solved as one assembly.
    mesh_size: target element size in mm, overriding the study's mesh.size_mm.
        Omitted, a fortieth of the part's bounding diagonal.
    vtu: also write a ``.vtu`` of the result for ParaView.
    verbose: show every progress line and the timings on stderr. Without it, a solve that runs long
        still says how far it has got on stderr, about every 10 s.
    """
    from cadgen._internal.fea.run import solve_study

    return solve_study(target, out, study=study, occurrence=occurrence, mesh_size=mesh_size, vtu=vtu, verbose=verbose)
