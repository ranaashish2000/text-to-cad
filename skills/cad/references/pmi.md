# PMI: datums, dimensions, tolerances, notes and views

Read this file when the user asks for datums, toleranced dimensions, basic dimensions,
flatness, position, a surface profile or a general profile ("unless otherwise noted, all
surfaces"; GD&T, PMI, model-based definition), for notes on a part (general notes, a flag
note on faces, a project's standard notes), for named views of a part (a front, a
section, a detail, each showing its own requirements), for which faces of a part have no
requirement, or for a STEP a supplier or an inspection tool can read requirements from.

A part's requirements are written in its model, on the faces they control, and the
document keeps them. The functions are `cadgen.ap242`'s, each named after the AP242
entity it writes (`from cadgen.ap242 import annotate, datum, ...`):

- **`annotate(part, *annotations)`** gives one part its annotations and returns
  the part.
- **Datums** name the faces the others are measured from: `datum("A", face)`.
- **Dimensions** are measured on the geometry, never typed: `dimensional_size` (a
  diameter) and `dimensional_location` (a distance), `basic=True` for a theoretically
  exact one.
- **Geometric tolerances**: `flatness_tolerance`, `position_tolerance` and
  `surface_profile_tolerance`, and `general_profile` for every face nothing else
  controls.
- **Notes**: `note`, a general note or a flag note on faces and annotations,
  numbered per part.
- **Views**: `view`, a named view of the part (a camera, a section, the
  requirements it shows); the iso view shows them all.

The model's document keeps them as the kernel's own GD&T, notes and views, linked to
their faces, and a STEP export writes the requirements as AP242 semantic PMI:
requirements a program reads off the faces, not drawn callouts. Notes and views stay in
the document: no STEP carries them. A GLB, STL or 3MF carries none. The viewer draws
datums, dimensions (limits stacked), flatness, position, surface profiles and flag notes
on the model and lists every annotation and note in its PMI section, one it does not
draw with why; `cadgen snapshot` draws them as the viewer does, and `--view` draws a
named view.

## A model-based definition

A part a shop can make and inspect with no drawing has a requirement on every face. Build
it in this order, in the part's one `annotate` call, and take every value from the
user (ask for what they have not given; never pick a tolerance):

1. **Datums**, primary first: the faces the part is located and measured from.
2. **The general profile first**: `general_profile("general", value, datums=[...])`,
   Note 1, the user's "unless otherwise noted" profile. It holds every face nothing
   else controls, so most faces need nothing more.
3. **Then each feature's own requirements**, where the general profile is not enough: a
   size (`dimensional_size`, `dimensional_location` with `plus_minus_tolerance` or
   `limits`), a location (`position_tolerance` and the basic dimensions to its true
   position), a form (`flatness_tolerance`), a tighter profile
   (`surface_profile_tolerance`). A size, a location or a profile
   takes its faces out of the general profile.
4. **The datum features' own controls**: the general profile leaves out the datum
   features it names. Give each one its own: a flatness on the primary, a profile on
   the others to the datums before them.
5. **Notes**, in the user's words: the shop's instructions, general or flagged on
   faces ([Notes](#notes)).
6. **Views**: a section through each feature a reader must see cut, and an iso view
   showing everything ([Views](#views)).
7. **Run the script, and read the coverage warnings** ([Coverage](#coverage)): fix each
   in the model and run it again, until its run lists no face without a requirement and
   no conflict.

```python
from cadgen.ap242 import (annotate, datum, dimensional_location, dimensional_size, flatness_tolerance,
                          general_profile, limits, note, position_tolerance, section, surface_profile_tolerance,
                          view)
from lib.notes import NOTES   # the project's notes, in its own module (Notes, below)

# The values are examples: take the user's, or ask for them.
return annotate(
    body,
    datum("A", bottom), datum("B", front), datum("C", left),                   # 1
    general_profile("general", 0.5, datums=["A", "B", "C"]),                    # 2: Note 1
    dimensional_size("hole-diameter", hole, tolerance=limits(10.0, 10.1)),      # 3
    position_tolerance("hole-position", hole, 0.1, datums=["A", "B", "C"]),
    dimensional_location("hole-from-B", front, hole, basic=True),
    dimensional_location("hole-from-C", left, hole, basic=True),
    flatness_tolerance("A-flatness", bottom, 0.05),                             # 4
    surface_profile_tolerance("B-profile", front, 0.1, datums=["A"]),
    surface_profile_tolerance("C-profile", left, 0.1, datums=["A", "B"]),
    note("mask", NOTES["primer-mask"], faces=[hole],                            # 5: Note 2
         annotations=["hole-diameter"]),
    view("A-A", camera="front", shows=["hole-diameter", "hole-position", "mask"],
         section=section("y", 0, invert=True)),                                 # 6
    view("iso", camera="iso"),
)
```

Here the general profile holds the top and the back and right sides; every other face
has its own requirement, so the run warns of nothing.

## Annotate a part

```python
# src/plate.py
import build123d as bd
from cadgen import model
from cadgen.ap242 import (annotate, datum, dimensional_location, dimensional_size, flatness_tolerance,
                          plus_minus_tolerance, position_tolerance)


@model
def plate():
    body = bd.Box(100, 60, 10) - bd.Cylinder(5, 10)
    body.label = "plate"
    bottom, top = body.faces().sort_by(bd.Axis.Z)[0], body.faces().sort_by(bd.Axis.Z)[-1]
    front, left = body.faces().sort_by(bd.Axis.Y)[0], body.faces().sort_by(bd.Axis.X)[0]
    hole = body.faces().filter_by(bd.GeomType.CYLINDER)[0]
    # The tolerance values are examples: take the user's, or ask for them.
    return annotate(
        body,
        datum("A", bottom),
        datum("B", front),
        datum("C", left),
        dimensional_location("thickness", bottom, top, tolerance=plus_minus_tolerance(0.2)),
        dimensional_size("hole-diameter", hole, tolerance=plus_minus_tolerance(0.1, 0)),
        flatness_tolerance("top-flatness", top, 0.05),
        position_tolerance("hole-position", hole, 0.1, datums=["A", "B", "C"]),
        dimensional_location("hole-from-B", front, hole, basic=True),  # its true position: 30 from B
        dimensional_location("hole-from-C", left, hole, basic=True),   # and 50 from C
    )


if __name__ == "__main__":
    plate()
```

Its run warns that the four sides have no requirement: nothing here holds them.
[A model-based definition](#a-model-based-definition) gives every face one.

- **Annotate the shape that is returned**, after its last boolean: a `Face` given as a
  ref is numbered against that shape when `annotate` is called. Annotations given to
  a shape a later operation replaces fail the run, naming them: no file would carry
  them. Move the `annotate` call after the operation.
- **One part at a time.** In an assembly, annotate each part before it goes into the
  compound; a part placed twice carries its PMI once. An annotated assembly is refused.
- **Ids are yours**: unique on the part and kept across edits. A datum's id is its
  letter, one capital other than `I`, `O` and `Q` (ASME Y14.5 sets them aside). The
  build report, the viewer and the STEP check name annotations by id.
- **A ref** is a build123d `Face` of the annotated shape, or a ref in the engine's
  grammar: an element name, a canonical `owner/element` ref, or `face N`
  ([references](inspection-and-validation.md#references)). A selection the code makes
  again on every build, or an element name, follows the face through edits; `face N`
  does not.
- **Nominals are measured**: a cylinder's diameter, or a distance between two parallel
  planes, from a plane to a hole's axis parallel to it, or between two parallel axes.
- **Tolerances are explicit.** `plus_minus_tolerance(upper, lower=None)` takes signed
  deviations, `lower` defaulting to `-upper`; `limits(lower, upper)` takes the
  least and the greatest size instead (`limits(10.0, 10.05)` is 10.00–10.05). A
  dimension given no `tolerance=` holds none: nothing is assumed.
- **A basic dimension** (`dimensional_location(..., basic=True)`) has no tolerance: it
  locates what a geometric tolerance controls, such as a position's true position. A
  tolerance on one is refused; a diameter is never basic (`dimensional_size` takes no
  `basic`).
- **A datum frame** is ordered, primary first, three datums at most, and a datum holds
  one place in every frame that names it.
- **A surface profile**
  (`surface_profile_tolerance("sides", [front, left, hole], 0.5, datums=["A"])`) is one
  requirement on a face or on a list of faces, of any kind: they lie within a zone of
  that width about their true profile. It takes up to three datums, or none. An empty
  list is refused, and a face the list names twice leaves the profile `invalid`.
- **A general profile** (`general_profile("general", 0.5, datums=["A", "B", "C"])`)
  is ASME Y14.5's "unless otherwise noted, all surfaces profile 0.5 to A|B|C": a surface
  profile of every face of the part that nothing else controls. You name no faces: each
  build computes them from that build's annotations and geometry, so after an edit it
  covers the new faces. A face is otherwise controlled when it has a size or location
  requirement of its own: a face of a `plus_minus_tolerance` or `limits` dimension, or
  the face of a position or of a `surface_profile_tolerance`. A flatness alone, a datum,
  or the start of a basic dimension leaves a face to the general profile. The datum features it names are
  controlled on their own and left out of it (no face is profiled relative to itself):
  give each one its own control, such as datum A's flatness. It is not XCAF's
  `All_Over` modifier (every surface, beside any other requirement). One per part. A
  general profile with no face left to cover is fine: it is `ok` and written to no STEP.
- **Anything else is not written**, never approximated: no function writes another
  characteristic or dimension type, and fits, material modifiers, reference dimensions
  and assembly-level PMI are refused (`PMIUnsupported`).
- **`presentation=place(anchor, offset)`** says where a callout's label is drawn,
  in millimetres in the part's frame. It is not a requirement, and no export writes
  it. Dragging a label in the viewer asks for this change.

## Notes

```python
# lib/notes.py, a module of the project's own code: its standard notes by name, in the
# user's words. This text is an example, as the tolerance values are: cadgen ships no
# note text, so never write notes for the user; ask for theirs.
NOTES = {"primer-mask": "Mask these features before coating with primer"}
```

```python
from lib.notes import NOTES

return annotate(
    body,
    # ... the requirements, as above ...
    general_profile("general", 0.5, datums=["A", "B", "C"]),   # Note 1
    # Note 2, a flag note: on the hole's face and its diameter.
    note("mask", NOTES["primer-mask"], faces=[hole], annotations=["hole-diameter"]),
)
```

- **A general note** names no face and no annotation: `note(id, text)`.
- **A flag note** names the faces it applies to (`faces=`, a face or a list, of any
  kind) and the annotations of the same `annotate` call it applies to (`annotations=`,
  by id). An id the call does not have, a note's id, or an empty text is refused.
- **Numbers are the part's**: the general profile is Note 1 where the part has one,
  then each note in the order the code gives them. Reorder the calls to renumber.
- **Ids are shared**: a note's id is unique among the part's annotations.
- **The project's notes** are its own: keep them in a module of its own code that
  the models import, and write each note from it. The text is the user's: ask for it.
- **Notes are the document's**: no STEP carries them, and no STEP is refused for one.
  A flag note on faces is drawn as its flag and number on its first face; a general
  note and the general profile (Note 1) are the viewer's notes alone, which
  `cadgen snapshot` lists among what it leaves out.

## Views

A part's named views walk a reader through its requirements one view at a time:

```python
return annotate(
    body,
    # ... the requirements and notes, as above ...
    view("A-A", camera="front", shows=["hole-diameter", "hole-position"],
         section=section("y", 0, invert=True),  # through the hole's axis
         place={"hole-diameter": place((5, 0, 0), (15, 0, 10))}),
    view("iso", camera="iso"),    # shows every annotation and note
)
```

- **A camera** is a preset's name, the same as `cadgen snapshot --camera`'s: `iso`,
  `front`, `back`, `left`, `right`, `top`, `bottom`. Or your own,
  `camera(direction, up)`: `direction` points from the part toward the eye, as a
  preset's does (`front` is `(0, -1, 0)`). Every view is orthographic as
  `cadgen snapshot` draws it; the viewer keeps the person's projection (its
  Projection menu).
- **A section** is the viewer's Clip: `section(axis, at, invert=False)` cuts
  across `"x"`, `"y"` or `"z"` at `at` millimetres and keeps the lower side
  (`invert=True` the upper). Keep the side away from the camera, so it looks at the
  cut: the `front` camera is at -y, so a section across `y` keeps the upper side
  (`invert=True`); a `back` camera's keeps the lower.
- **What it shows**: `shows=` names the ids of the same `annotate` call, annotations
  and notes. Leave it out to show everything, as an iso view does.
- **Placements per view**: `place={id: place(anchor, offset)}` draws a shown
  callout there in this view, and as its own `presentation=` says elsewhere.
- Everything is in the part's frame. A view's name is unique among the part's
  views; an id the call does not have, or a placement of one the view does not
  show, is refused.
- **Views are the document's**, kept as the kernel's own views: no STEP carries them,
  and no STEP is refused for one. A view is no annotation: the build report does not
  count it. The viewer applies a view from its PMI section, and `cadgen snapshot
  --view A-A` draws it ([snapshots](snapshot-review.md)); a view whose section the
  part's placement turns off the world's axes is refused there, not cut along
  another.

## After an edit

Every build resolves each ref again. One that still names its face follows it, its
nominal measured anew and its tolerance as written. Otherwise its status says why:

- `missing`: it names no face (the face was removed, or is made another way);
- `ambiguous`: it names several faces;
- `invalid`: it names a face the annotation cannot stand on (a diameter on a plane).

Such an annotation stays in the document with its values, linked to nothing, and is
listed in the run's report, its `pmi` (`{"annotations", "problems"}`, with `coverage`
where there is any). A STEP is refused rather than written
without it, naming each one and its ref. Fix the ref in the code, or remove the annotation. A flag
note whose face is gone keeps its number and its text and says `missing` too; a STEP
is not refused for it. A tolerance whose frame names a datum that is not `ok` takes
that datum's status: fix the datum, and the tolerance with it.

A ref written as text names whatever face holds that name on this run, measured as it
is now: a hole's name can pass to a flat side of the cut that replaced it. Each run is
of itself alone and says nothing of the last, so after an edit that changes how a face
is made, read the served PMI's nominals and statuses, and point a ref at the face meant.

## Coverage

Every face of a part is to have a requirement. After each run of a part with PMI,
read its coverage warnings and fix them in the model:

```text
warning: pmi: PART: N faces have no requirement: REF, ...
warning: pmi: PART: IDS: PROBLEM
```

- **Faces with no requirement.** A face is covered by an `ok` toleranced dimension
  (`plus_minus_tolerance` or `limits`) or geometric tolerance on it, or by the general profile.
  A basic dimension, a datum and a note cover nothing: a basic dimension only locates
  what a position or a profile controls. One `general_profile` usually covers most
  of them: add it first, with the user's value and datums (ask for them;
  [A model-based definition](#a-model-based-definition)).
- **Then the datum features.** The general profile leaves out the datum features it
  names, so they stay listed until each has a control of its own, even where a basic
  dimension starts from it: a flatness on the primary datum, and the user's
  requirements for the others.
- **Conflicts**, each naming its ids and what to change:
  - `unused_datum`: a datum no tolerance's frame names;
  - `unlinked_note`: a flag note whose every face and annotation is broken (one that
    still holds keeps it linked);
  - `outside_limits`: the measured size is outside its own limits: model it within
    them, or correct the limits;
  - `apart`: two dimensions of one type on the same faces whose zones do not meet;
  - `form_over_profile`: a flatness wider than a surface profile on its face (its own
    or the general one), which already holds the face's form tighter.
- **It warns and never fails**: the run succeeds and a STEP is written all the same. The
  run's report has it in its `pmi`, `coverage` for each part with something to say
  (`plate.build().models[0]["pmi"]`), and the document's structure has `pmi_coverage`,
  `{"part", "uncovered", "conflicts"}` for every part with PMI.

## Read and check

The engine's `read` (`cadgen.engine.pmi`) reads the PMI a file holds: the STEP a model
was exported to (what a supplier reads), an `.xbf`, or a vendor's STEP.

```bash
cadgen export "$ID" STEP/plate.step
```

```python
from cadgen.engine.pmi import read

for entry in read("STEP/plate.step"):   # or an .xbf
    print(entry["id"], entry["kind"], entry["status"], entry["problem"])
```

Each entry has `id`, `kind` (`datum`, `dimension`, `geometric_tolerance`, `note`),
`status` (`ok`, `missing`, `ambiguous`, `invalid`), `problem`, `refs` (each with its
canonical ref and status), and one of `datum`, `dimension`, `geometric_tolerance` or
`note` holding its values. A note's is `{"number", "text", "general", "annotations"}`,
its `refs` its faces. The document's structure lists each part's views as `pmi_views`
(`{"part", "name", "label", "camera", "section", "shows", "place"}`, the camera's
`direction` and `up` unit vectors and its `preset` the name or null), and each part's
coverage as `pmi_coverage` ([Coverage](#coverage)). A general profile says
`"general": true`, and its `refs` are the faces it covers on this build, each with
`written` null; `otherwise_controlled(entries)` (`cadgen.engine.pmi`) gives the faces the
rest control.
PMI read from another system's STEP that this does not model whole (another
characteristic, a fit, modifiers, limits beside any of these, a link to an edge or a
point) is listed as `kind: "unsupported"` with its `xcaf_type` and a `reason`, never
dropped.
A nominal the file holds says `"source": "file"`; a dimension the file gives limits
has none (`nominal` is null), since the file states limits, not a nominal.

## Export

A STEP of the model writes the PMI: `cadgen export "$ID" STEP/plate.step`, `export(plate(),
...)` in an `exports.py`, or the viewer's Export ([exports](exports.md)). A STEP that
would drop an annotation the code made (one linked to no geometry, [After an
edit](#after-an-edit)) is refused, naming each. An `.xbf` exported from the model is the
document made again from what the engine holds, which carries none of its PMI yet, so
`cadgen export "$ID" plate.xbf` is refused for a part with PMI, naming what it would
lose; the engine keeps the document itself, PMI and all. No export poses a model or
takes a part out of it: a posed file or a part on its own is a model of its own
([exports](exports.md#posed-files-and-parts-on-their-own)).

A vendor's STEP converts as ever (`cadgen export vendor/motor.step vendor/motor.xbf`):
the `.xbf` keeps the file's GD&T as read, and an export of either writes it. What an
export of a vendor's document cannot carry whole is said in its log, `warning: pmi:
...`, never refused.

Every STEP written of the model -- a declared `@step`, `cadgen export`, `export()`, the
viewer's Export -- is read back with a reader of its own, without the kernel, and every
annotation matched to the file: values, units, datum order, zone, a basic dimension's
mark, and its faces by name and by geometry (a plane's normal and offset, a cylinder's
axis and radius). The result is the file's `pmiVerification` (the build report's
`files` row, the export's answer). A STEP whose check finds any problem -- a record that
does not match, or a file it could not check -- is refused, no file written, whoever
asked for it: a build, an export or the viewer's Export, whose toast says why. Each part
is checked against its own records, so parts may reuse face names; the kernel's writer
writes one datum of a letter per file, so give each part's datums letters of their own,
and each part its own name. It checks the file, not a supplier's importer.

A STEP carries no annotation ids, no drawn callouts, no notes and no views: notes and
views stay in the document, and the check neither expects nor counts
them. A general profile is written as a surface profile on exactly the faces it
covers, so a CAM or inspection program reads the faces themselves; the file does not
say "general".

## Review

`cadgen snapshot` draws a document's PMI as the viewer shows it, a broken one in red
or amber at the place the code gave it; `pmi=off` draws the model alone, and
`--view NAME` a named view ([snapshots](snapshot-review.md)). In the viewer, picking a callout or its row lights
its faces and shows its values: a toleranced dimension's limits, a tolerance's datums
in order, and what uses each datum.
