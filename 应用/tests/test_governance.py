"""Protect fixed exogenous comparisons and causal metering behavior."""
# AI辅助：OpenAI Codex；精确型号/发布日期不可得，提交前据真实记录补齐。
import json
from pathlib import Path
import sys
import tempfile
import unittest
from unittest.mock import patch
import xml.etree.ElementTree as ET

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from b_twin.runner import prepare_run
from b_twin.governance import feedback_rate, phase_state, UPDATE, YELLOW
from b_twin.storage import ledger
from b_twin.governance_report import agent_costs
import pandas as pd


class GovernanceContracts(unittest.TestCase):
    def test_all_policy_arms_keep_network_supply_agents_and_facilities_identical(self):
        with tempfile.TemporaryDirectory() as temp, patch("b_twin.runner.APP", Path(temp)):
            runs = [prepare_run("peak", "through", 20261017, policy, "time15", 1800, .5, 900, 900, policy)[0]
                for policy in ["free", "fixed", "feedback"]]
            for name in ["network.net.xml", "demand.rou.xml", "vehicles.csv", "core_vehicles.csv",
                         "background_vehicles.csv", "boundary_supply.json", "meter_plan.json"]:
                self.assertTrue(all((p / name).read_bytes() == (runs[0] / name).read_bytes() for p in runs), name)
            plan = json.loads((runs[0] / "meter_plan.json").read_text(encoding="utf-8"))
            network = ET.parse(runs[0] / "network.net.xml").getroot()
            for spec in plan["facilities"]:
                links = [c for c in network.findall("connection") if c.get("tl") == spec["node"]]
                self.assertTrue(all(c.get("from") == spec["stop_edge"] and c.get("to") == spec["next_edge"] for c in links))
                self.assertNotIn(spec["sensor_edge"], {c.get("from") for c in links})
            cfg = ET.parse(runs[0] / "run.sumocfg").getroot()
            self.assertEqual(cfg.find("processing/time-to-teleport").get("value"), "-1")

    def test_feedback_is_bounded_reacts_to_occupancy_and_prioritizes_queue_storage(self):
        low, _ = feedback_rate(300, 5, .1, 1200)
        high, _ = feedback_rate(300, 35, .1, 1200)
        override, reason = feedback_rate(300, 35, .81, 1200)
        self.assertGreater(low, 300)
        self.assertEqual(high, 120)
        self.assertEqual(override, 1200)
        self.assertEqual(reason, "queue_override")
        self.assertEqual(feedback_rate(1100, 0, 0, 1200)[0], 1200)
        with self.assertRaises(ValueError):
            feedback_rate(300, float("nan"), 0, 1200)

    def test_each_meter_cycle_contains_yellow_before_red(self):
        self.assertEqual(phase_state(0, 7), "G")
        self.assertEqual(phase_state(6.5, 7), "G")
        self.assertEqual(phase_state(7, 7), "y")
        self.assertEqual(phase_state(7 + YELLOW - .5, 7), "y")
        self.assertEqual(phase_state(7 + YELLOW, 7), "r")
        self.assertEqual(phase_state(UPDATE, 7), "G")

    def test_storage_counts_lateral_flows_and_half_open_boundaries(self):
        events = [{"time": 299, "sign": 1, "kind": "upstream"},
            {"time": 300, "sign": 1, "kind": "lateral_in"},
            {"time": 500, "sign": -1, "kind": "lateral_out"},
            {"time": 1200, "sign": -1, "kind": "downstream"}]
        r = ledger(events, 300, 1200)
        self.assertEqual(r["storage_begin"], 1)
        self.assertEqual(r["storage_end"], 1)
        self.assertEqual(r["lateral_in"], 1)
        self.assertEqual(r["lateral_out"], 1)
        self.assertEqual(r["downstream"], 0)
        self.assertEqual(r["balance_residual"], 0)

    def test_fixed_window_cost_keeps_unspawned_agents_and_warmup_remainder(self):
        with tempfile.TemporaryDirectory() as temp:
            p = Path(temp)
            (p / "run.json").write_text(json.dumps({"evaluation_seconds": 900, "simulation_end_seconds": 1500}), encoding="utf-8")
            pd.DataFrame([{"vehicle_id": "a", "depart": 200, "population": "core"},
                {"vehicle_id": "b", "depart": 400, "population": "background"}]).to_csv(p / "vehicles.csv", index=False)
            (p / "tripinfo.xml").write_text('<tripinfos><tripinfo id="a" arrival="350"/><tripinfo id="b" arrival="-1"/></tripinfos>', encoding="utf-8")
            result = agent_costs(p, p)
            self.assertAlmostEqual(result["evaluation_experienced_vehicle_hours"], (50 + 800) / 3600)
            self.assertAlmostEqual(result["whole_run_mean_experienced_lower_bound_s"], (150 + 1100) / 2)


if __name__ == "__main__":
    unittest.main()
