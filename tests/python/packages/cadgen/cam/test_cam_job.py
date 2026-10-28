"""The job: a closed vocabulary read into frozen dataclasses, every refusal one sentence naming what to change."""

from __future__ import annotations

import json
import tempfile
import unittest
from pathlib import Path

from tests.python.support.cam_fixtures import mill_job

from cadgen._internal.cam.job import CamJobError, parse_job


def refusal(case: unittest.TestCase, job) -> str:
    with case.assertRaises(CamJobError) as caught:
        parse_job(job)
    return str(caught.exception)


class Reads(unittest.TestCase):
    def test_a_job_reads_with_its_defaults_and_says_what_it_assumed(self):
        job = parse_job(mill_job([{"op": "face", "tool": "T1"}, {"op": "drill", "tool": "T2", "faces": ["face 9"]}]))
        self.assertEqual(("linuxcnc", "G54"), (job.machine.post, job.machine.work_offset))
        self.assertEqual((2.0, 2.0, 1.0), job.stock.margin_mm)
        (setup,) = job.setups
        self.assertEqual(("+Z", "stock-top-center", 5.0, 2.0),
                         (setup.orientation, setup.origin, setup.clearance_mm, setup.retract_mm))
        self.assertEqual([0, 1], [op.index for op in setup.operations])
        self.assertEqual(0.4, setup.operations[0].params["stepover"])
        self.assertEqual(1.0, setup.operations[1].params["through_mm"])
        self.assertEqual({"machine.rapid_mm_min"}, {entry["key"] for entry in job.assumed})

    def test_an_operation_index_runs_across_setups(self):
        job = parse_job(mill_job([], setups=[
            {"id": "top", "operations": [{"op": "face", "tool": "T1"}]},
            {"id": "bottom", "orientation": "-Z", "operations": [{"op": "face", "tool": "T1"},
                                                                  {"op": "profile", "tool": "T1"}]}]))
        self.assertEqual([[0], [1, 2]], [[op.index for op in s.operations] for s in job.setups])

    def test_a_file_and_inline_json_read_as_the_dict_does(self):
        raw = mill_job([{"op": "face", "tool": "T1"}])
        with tempfile.TemporaryDirectory() as folder:
            path = Path(folder) / "bracket.cam.json"
            path.write_text(json.dumps(raw), encoding="utf-8")
            from_file = parse_job(str(path))
        self.assertEqual(parse_job(raw).setups, from_file.setups)
        self.assertEqual("bracket.cam", from_file.name)
        self.assertEqual(parse_job(raw).setups, parse_job(json.dumps(raw)).setups)

    def test_a_tool_left_to_the_catalogue_is_listed_once_it_is_used(self):
        job = parse_job(mill_job([{"op": "face", "tool": "T1"}],
                                 tools=[{"id": "T1", "type": "flat", "diameter_mm": 6}]))
        self.assertEqual({"T1.flute_length_mm", "T1.stickout_mm", "T1.flutes"},
                         {e["key"] for e in job.assumed} - {"machine.rapid_mm_min"})
        self.assertEqual((18.0, 23.0), (job.tools["T1"].flute_length_mm, job.tools["T1"].stickout_mm))


class Refuses(unittest.TestCase):
    def test_an_unknown_key_names_the_allowed_ones(self):
        job = mill_job([{"op": "face", "tool": "T1", "depth": 3}])
        self.assertIn("unknown key 'depth'", refusal(self, job))
        self.assertIn("stepdown_mm, stepover", refusal(self, job))

    def test_an_operation_cadgen_does_not_plan_is_refused(self):
        message = refusal(self, mill_job([{"op": "waterline", "tool": "T1"}]))
        self.assertIn("face, profile, pocket, adaptive, drill", message)

    def test_the_post_is_never_guessed(self):
        message = refusal(self, mill_job([{"op": "face", "tool": "T1"}], machine={"max_rpm": 24000}))
        self.assertIn("machine.post is required", message)
        self.assertIn("grbl, linuxcnc", message)
        self.assertIn("haas", refusal(self, mill_job([], machine={"post": "haas"})).split(";")[0])

    def test_a_machine_kind_is_no_key_of_a_machine(self):
        # It changed nothing in a plan, so it is gone: refused as any key the machine never had is.
        self.assertEqual("machine: unknown key 'kind'; the keys are post, name, travel_mm, min_rpm, max_rpm, "
                         "max_feed_mm_min, rapid_mm_min, work_offset",
                         refusal(self, mill_job([], machine={"kind": "mill", "post": "fanuc"})))

    def test_a_tolerance_finer_than_freecad_follows_is_refused(self):
        job = mill_job([{"op": "face", "tool": "T1"}])
        job["tolerance_mm"] = 0.001
        self.assertIn("tolerance_mm must be at least 0.01, not 0.001", refusal(self, job))

    def test_a_pocket_needs_its_floors_and_a_drill_its_holes(self):
        self.assertIn("the floors to clear down to", refusal(self, mill_job([{"op": "pocket", "tool": "T1"}])))
        self.assertIn("the hole walls to drill", refusal(self, mill_job([{"op": "drill", "tool": "T2"}])))

    def test_each_operation_takes_its_own_tools(self):
        self.assertIn("drill takes a drill tool", refusal(self, mill_job([{"op": "drill", "tool": "T1",
                                                                            "faces": ["face 3"]}])))
        self.assertIn("flat or ball or bull", refusal(self, mill_job([{"op": "face", "tool": "T2"}])))

    def test_a_tool_the_job_does_not_list(self):
        self.assertIn("T1, T2", refusal(self, mill_job([{"op": "face", "tool": "T9"}])))

    def test_a_material_off_the_table_needs_its_numbers(self):
        message = refusal(self, mill_job([{"op": "face", "tool": "T1"}], material="unobtainium"))
        self.assertIn("surface_m_min and chipload_mm", message)
        job = mill_job([{"op": "face", "tool": "T1"}])
        job["material"] = {"name": "unobtainium", "surface_m_min": 90, "chipload_mm": 0.02}
        self.assertFalse(parse_job(job).material.table)

    def test_a_stepdown_past_the_flutes_and_a_stepover_past_the_tool(self):
        self.assertIn("at most 20", refusal(self, mill_job([{"op": "face", "tool": "T1", "stepdown_mm": 25}])))
        self.assertIn("at most 1", refusal(self, mill_job([{"op": "face", "tool": "T1", "stepover": 1.5}])))

    def test_not_json_and_not_a_number(self):
        self.assertIn("not JSON", refusal(self, "{machine"))
        self.assertIn("NaN is not a number", refusal(self, '{"machine": NaN}'))

    def test_a_setup_id_names_a_file(self):
        message = refusal(self, mill_job([], setups=[{"id": "top side", "operations": [{"op": "face", "tool": "T1"}]}]))
        self.assertIn("letters, digits, - and _", message)


if __name__ == "__main__":
    unittest.main()
