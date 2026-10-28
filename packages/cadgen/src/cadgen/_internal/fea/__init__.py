"""The finite element analysis behind ``cadgen fea``; the public verbs live in :mod:`cadgen.fea`.

:mod:`.study` parses and checks the study file before any heavy import; :mod:`.materials` is the material table;
:mod:`.mesh` turns a STEP occurrence (or an assembly's glued parts) into tetrahedra through netgen, every B-rep face
kept under its cadgen ordinal; :mod:`.static` solves it through CalculiX (:mod:`.solve`, :mod:`.calculix`) and reads
the answer; :mod:`.checks` says what an engineer would of it; :mod:`.outputs` writes the result GLB and VTU;
:mod:`.run` is the orchestration the verbs call.

Everything numeric imports lazily, inside the functions that need it, so that ``cadgen fea --help`` and the public
namespace stay off the CAD kernel and off the solver stack (the ``fea`` extra may not be installed). Units throughout:
mm, N, MPa (N/mm^2), tonne/mm^3 for density.
"""
