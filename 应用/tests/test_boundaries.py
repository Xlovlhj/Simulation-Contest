"""Guard against artificial sinks and mismatched external scenario inputs."""
# AI辅助：OpenAI Codex；精确版本/型号及发布日期不可得，提交前据实际记录补齐并人工复核。
from pathlib import Path
import json
import sys
import tempfile
import unittest
from unittest.mock import patch
import xml.etree.ElementTree as ET

import pandas as pd

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from b_twin.boundary_supply import service_cycle
from b_twin.runner import prepare_run
from b_twin.topology import nearest_bearing, angle_difference
from b_twin.evaluation import primary_metrics
from b_twin.stage03 import exit_arrivals


class BoundaryContracts(unittest.TestCase):
    def test_directed_line_comparison_distinguishes_opposite_carriageway(self):
        _, north = nearest_bearing((1, 5), [(0, 0), (0, 10)])
        _, south = nearest_bearing((1, 5), [(0, 10), (0, 0)])
        self.assertAlmostEqual(angle_difference(north, south), 180)
        self.assertAlmostEqual(angle_difference(359, 1), 2)

    def test_terminal_routes_continue_and_explicit_side_exits_do_not_change(self):
        with tempfile.TemporaryDirectory() as directory, patch("b_twin.runner.APP", Path(directory)):
            old, _ = prepare_run("peak", "feeders", 20261017, "old", "time15", 900, .5, 0)
            new, meta = prepare_run("peak", "through", 20261017, "new", "time15", 900, .5, 0)
            self.assertEqual((old / "core_vehicles.csv").read_bytes(), (new / "core_vehicles.csv").read_bytes())
            old_routes = {e.get("id"): e.get("edges").split() for e in ET.parse(old / "demand.rou.xml").getroot().findall("route")}
            new_routes = {e.get("id"): e.get("edges").split() for e in ET.parse(new / "demand.rou.xml").getroot().findall("route")}
            formerly_terminal = [rid for rid, edges in old_routes.items() if edges[-1] == "1095134817"]
            self.assertEqual(len(formerly_terminal), 6)
            self.assertEqual(meta["extended_route_count"], 12)
            for rid in formerly_terminal:
                self.assertEqual(new_routes[rid][-1], "45740459")
                self.assertIn("249233908", new_routes[rid])
            for rid, edges in old_routes.items():
                if edges[-1] in {"102255082", "45740606", "150771646#0"}:
                    self.assertEqual(new_routes[rid], edges)
            core = pd.read_csv(new / "core_vehicles.csv")
            self.assertGreater(core.route_id.isin(formerly_terminal).sum(), 0)

    def test_supply_hypotheses_keep_all_core_and_background_agents_identical(self):
        with tempfile.TemporaryDirectory() as directory, patch("b_twin.runner.APP", Path(directory)):
            a, ma = prepare_run("peak", "through", 20261017, "free", "time15", 900, .5, 900, 0)
            b, mb = prepare_run("peak", "through", 20261017, "restricted", "time15", 900, .5, 900, 900)
            for name in ["vehicles.csv", "core_vehicles.csv", "background_vehicles.csv", "demand.rou.xml"]:
                self.assertEqual((a / name).read_bytes(), (b / name).read_bytes())
            self.assertNotEqual(ma["network_sha256"], mb["network_sha256"])
            fleet = pd.read_csv(a / "vehicles.csv")
            background = fleet[fleet.population.eq("background")]
            self.assertEqual(len(background), 900)
            self.assertTrue(background.depart.between(300, 3900, inclusive="left").all())
            self.assertEqual(len(fleet), ma["planned_vehicles"])
            plan = json.loads((b / "boundary_supply.json").read_text(encoding="utf-8"))
            self.assertIn("NOT_actual", plan["provenance"])
            self.assertAlmostEqual(plan["green_seconds"] + plan["yellow_seconds"] + plan["red_seconds"], 60)
            for detector in ET.parse(b / "detectors.add.xml").getroot():
                if detector.get("id").startswith("boundary_out__"):
                    self.assertLess(float(detector.get("pos")), 131.3 - fleet.length_m_assumed.max())

    def test_terminal_discharge_uses_arrivals_and_keeps_half_open_time_windows(self):
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory)
            routes = ET.Element("routes")
            ET.SubElement(routes, "route", id="through", edges="upstream 45740459")
            ET.SubElement(routes, "route", id="side", edges="upstream side_exit")
            trips = ET.Element("tripinfos")
            for i, (arrival, rid) in enumerate([(299.5, "through"), (300, "through"),
                    (1199.5, "through"), (1200, "through"), (600, "side"), (-1, "through")]):
                ET.SubElement(routes, "vehicle", id=str(i), route=rid)
                ET.SubElement(trips, "tripinfo", id=str(i), arrival=str(arrival))
            ET.ElementTree(routes).write(path / "demand.rou.xml")
            ET.ElementTree(trips).write(path / "tripinfo.xml")
            # An E1 at the sink may miss all rear crossings; do not use it as discharge.
            (path / "boundary_out.xml").write_text('<detector/>', encoding="utf-8")
            result = exit_arrivals(path, "2026-10-15T08:00:00", 900)
            self.assertEqual(result.iloc[0].simulated_exit_arrivals, 2)
            self.assertEqual(result.iloc[0].simulated_exit_flow_vph, 8)

    def test_virtual_supply_cycle_bounds_and_effective_rate(self):
        plan = service_cycle(900, 2)
        nominal_effective = ((plan["green_seconds"] - plan["startup_loss_seconds_assumed"])
            / plan["cycle_seconds"] * plan["saturation_vph_per_lane_assumed"] * plan["lanes"])
        self.assertAlmostEqual(nominal_effective, 900)
        for invalid in [-1, 0, 3600]:
            with self.assertRaises(ValueError):
                service_cycle(invalid, 2)

    def test_missing_simulated_speed_does_not_remove_an_observed_congested_window(self):
        data = pd.DataFrame([{"detector_id": "A", "used_in_primary_metrics": True,
            "flow_absolute_error": 100, "flow_vph_estimated": 100,
            "speed_kmh_observed": 20., "simulated_speed_kmh": float("nan"),
            "speed_absolute_error_kmh": float("nan"), "simulated_occupancy_pct": 0., "occupancy_pct_observed": 25.}])
        result = primary_metrics(data)
        self.assertEqual(result["observed_slow_station_windows"], 1)
        self.assertEqual(result["missed_slow_station_windows"], 1)
        self.assertEqual(result["speed_missing_simulated_windows"], 1)


if __name__ == "__main__":
    unittest.main()
