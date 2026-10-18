"""Verify paired inputs, missing-data inversion and vehicle conservation."""
# AI辅助：OpenAI Codex；精确型号/版本、发布日期不可得，提交前按实际记录补齐并人工复核。
from pathlib import Path
import json
import sys
import tempfile
import unittest
from unittest.mock import patch
import xml.etree.ElementTree as ET

import numpy as np
import pandas as pd

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from b_twin.demand import invert_quarter, fixed_vehicles
from b_twin.evaluation import vehicle_accounting, trip_accounting
from b_twin.runner import prepare_run


class DemandContracts(unittest.TestCase):
    def test_missing_count_retains_prior_instead_of_becoming_zero(self):
        routes = pd.DataFrame([{"route_id": "r0", "edges": "e0 e1"}])
        mapping = pd.DataFrame([{"detector_id": "A", "sumo_edge": "e0", "requires_review": False}])
        counts = pd.DataFrame([{"detector_id": "A", "window": "2026-10-15T08:00:00", "eligible": False,
            "flow_vph_estimated": 0., "conditional_flow_ci95_low": 0., "conditional_flow_ci95_high": 0.}])
        rates, used, info = invert_quarter(routes, counts, mapping, counts.iloc[0].window, np.array([100.]))
        self.assertEqual(rates[0], 100.)
        self.assertEqual(info["constraints"], 0)
        self.assertFalse(used.iloc[0].constraint_used)
        counts["eligible"] = True  # A genuine observed zero IS evidence.
        rates, _, info = invert_quarter(routes, counts, mapping, counts.iloc[0].window, np.array([100.]))
        self.assertEqual(info["constraints"], 1)
        self.assertLess(rates[0], 1.)
        mapping["requires_review"] = True
        rates, _, info = invert_quarter(routes, counts, mapping, counts.iloc[0].window, np.array([100.]))
        self.assertEqual(info["constraints"], 0)
        self.assertEqual(rates[0], 100.)

    def test_fixed_departures_conserve_counts_and_respect_bounds(self):
        class Edge:
            def allows(self, _): return True
        class Net:
            def getEdge(self, _): return Edge()
        routes = pd.DataFrame([{"route_id": "r0", "direction": "north", "edges": "e0 e1"}])
        segments = pd.DataFrame([{"route_id": "r0", "begin": begin, "end": begin + 900,
            "estimated_vph": 41.7, "phase": "evaluation", "source_window": str(begin)} for begin in [300, 1200, 2100]])
        fleet = pd.DataFrame([{"direction": "north", "sumo_vclass": "passenger", "proportion": 1.,
            "vtype_id": "type1", "length_m_assumed": 5., "provenance": "test"}])
        vehicles, _ = fixed_vehicles(routes, segments, fleet, Net(), 123, "ordinary")
        again, _ = fixed_vehicles(routes, segments, fleet, Net(), 123, "ordinary")
        pd.testing.assert_frame_equal(vehicles, again)
        self.assertLess(abs(len(vehicles) - 41.7 * 2700 / 3600), 1.)
        self.assertTrue(vehicles.depart.between(300, 3000, inclusive="left").all())
        self.assertTrue(vehicles.speed_factor_assumed.between(.7, 1.3).all())
        self.assertTrue(vehicles.vehicle_id.is_unique)

    def test_network_extension_keeps_identical_vehicle_manifest(self):
        with tempfile.TemporaryDirectory() as directory, patch("b_twin.runner.APP", Path(directory)):
            a, ma = prepare_run("peak", "original", 20261017, "pair_a", "time15", 900)
            b, mb = prepare_run("peak", "extended", 20261017, "pair_b", "time15", 900)
            self.assertEqual((a / "vehicles.csv").read_bytes(), (b / "vehicles.csv").read_bytes())
            self.assertEqual(ma["vehicle_manifest_sha256"], mb["vehicle_manifest_sha256"])
            self.assertEqual(ma["planned_vehicles"], mb["planned_vehicles"])
            self.assertEqual(mb["extended_route_count"], 6)
            ra, rb = ET.parse(a / "demand.rou.xml").getroot(), ET.parse(b / "demand.rou.xml").getroot()
            self.assertEqual([v.attrib for v in ra.findall("vehicle")], [v.attrib for v in rb.findall("vehicle")])
            self.assertEqual([v.attrib for v in ra.findall("vType")], [v.attrib for v in rb.findall("vType")])
            self.assertFalse(ra.findall("flow"))
            manifest = json.loads((a / "demand_manifest.json").read_text(encoding="utf-8"))
            self.assertTrue(all(f["rank"] < f["routes"] for f in manifest["quarter_fits"]))

    def test_evaluation_end_is_not_replaced_by_clearance_end(self):
        xml = '''<summary>
          <step time="300" loaded="10" inserted="8" arrived="3" running="5" waiting="2" halting="1" discarded="0"/>
          <step time="1199.5" loaded="10" inserted="8" arrived="4" running="4" waiting="2" halting="2" discarded="0"/>
          <step time="2099.5" loaded="10" inserted="10" arrived="10" running="0" waiting="0" halting="0" discarded="0"/>
        </summary>'''
        with tempfile.TemporaryDirectory() as directory:
            p = Path(directory) / "summary.xml"
            p.write_text(xml, encoding="utf-8")
            metrics = vehicle_accounting(p, 1200, .5, 10)
            self.assertEqual(metrics["evaluation_end"]["pending"], 2)
            self.assertEqual(metrics["after_clearance"]["pending"], 0)
            self.assertEqual(metrics["evaluation_end"]["arrived"], 4)
            self.assertEqual(metrics["conservation_residual"], 0)
            with self.assertRaises(ValueError):
                vehicle_accounting(p, 1200, .5, 11)
            p.write_text(xml.replace('time="2099.5" loaded="10" inserted="10" arrived="10" running="0" waiting="0"',
                'time="2099.5" loaded="10" inserted="9" arrived="9" running="0" waiting="0"'), encoding="utf-8")
            metrics = vehicle_accounting(p, 1200, .5, 10)
            self.assertEqual(metrics["after_clearance"]["loaded_not_yet_attempted"], 1)
            self.assertTrue(metrics["all_departures_accounted_for"])

    def test_unfinished_and_undeparted_agents_are_included_in_trip_cost(self):
        with tempfile.TemporaryDirectory() as directory:
            p = Path(directory)
            pd.DataFrame([{"vehicle_id": v, "depart": d, "origin_edge": "e0"}
                for v, d in [("a", 100), ("b", 300), ("c", 400)]]).to_csv(p / "vehicles.csv", index=False)
            (p / "tripinfo.xml").write_text('''<tripinfos>
              <tripinfo id="a" depart="200" arrival="1000" duration="800" departDelay="100" waitingTime="0" timeLoss="0"/>
              <tripinfo id="b" depart="400" arrival="-1" duration="1400" departDelay="100" waitingTime="0" timeLoss="0"/>
              <tripinfo id="c" depart="-1" arrival="-1" duration="0" departDelay="1400" waitingTime="0" timeLoss="0"/>
            </tripinfos>''', encoding="utf-8")
            result = trip_accounting(p, 1200, 1800)
            self.assertEqual(result["arrived_before_horizon"], 1)
            self.assertEqual(result["pending_at_horizon"], 1)
            self.assertEqual(result["running_at_horizon"], 1)
            self.assertEqual(result["unfinished_after_clearance"], 2)
            self.assertAlmostEqual(result["mean_experienced_time_lower_bound_s"], (900 + 1500 + 1400) / 3)
            self.assertNotIn("mean_experienced_time_s_all_agents", result)


if __name__ == "__main__":
    unittest.main()
