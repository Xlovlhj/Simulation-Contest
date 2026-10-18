"""Emergency comparisons must retain physical access, demand and safe signals."""
# AI辅助：OpenAI Codex；提交前据真实使用记录补齐并人工复核。
import json
from pathlib import Path
import sys
import tempfile
import unittest
from unittest.mock import Mock, patch
import xml.etree.ElementTree as ET
import pandas as pd

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from b_twin.emergency import prepare_emergency, event_active, EmergencyController, VEHICLE, DISPATCH
from b_twin.governance_report import agent_costs


class EmergencyContracts(unittest.TestCase):
    def test_rescue_arms_keep_event_route_all_agents_and_network_identical(self):
        with tempfile.TemporaryDirectory() as temp, patch("b_twin.runner.APP", Path(temp)):
            runs = [prepare_emergency(20261017, p, p)[0] for p in ["baseline", "yield", "combined"]]
            for name in ["network.net.xml", "demand.rou.xml", "vehicles.csv", "emergency_scenario.json",
                    "meter_plan.json", "boundary_supply.json"]:
                self.assertTrue(all((p / name).read_bytes() == (runs[0] / name).read_bytes() for p in runs), name)
            root = ET.parse(runs[0] / "demand.rou.xml").getroot()
            task = next(v for v in root.findall("vehicle") if v.get("id") == VEHICLE)
            typ = next(v for v in root.findall("vType") if v.get("id") == task.get("type"))
            self.assertEqual(typ.get("vClass"), "passenger")
            self.assertEqual(float(task.get("depart")), DISPATCH)
            self.assertFalse(any("bluelight" in str(t.attrib) for t in root.iter()))

    def test_incident_is_half_open_and_restores_at_end(self):
        self.assertFalse(event_active(2099.5, 2100, 3000))
        self.assertTrue(event_active(2100, 2100, 3000))
        self.assertTrue(event_active(2999.5, 2100, 3000))
        self.assertFalse(event_active(3000, 2100, 3000))

    def test_post_evaluation_rescue_control_keeps_ordinary_unfinished_cost(self):
        with tempfile.TemporaryDirectory() as temp:
            p = Path(temp)
            (p / "run.json").write_text(json.dumps({"evaluation_seconds": 900, "simulation_end_seconds": 1500}), encoding="utf-8")
            pd.DataFrame([{"vehicle_id": "warmup", "depart": 200, "population": "core"},
                {"vehicle_id": "pending", "depart": 400, "population": "background"}]).to_csv(p / "vehicles.csv", index=False)
            (p / "tripinfo.xml").write_text('<tripinfos><tripinfo id="warmup" arrival="350"/><tripinfo id="pending" arrival="-1"/></tripinfos>', encoding="utf-8")
            short = agent_costs(p, p)
            full = agent_costs(p, p, 1500)
            self.assertAlmostEqual(full["evaluation_experienced_vehicle_hours"], (50 + 1100) / 3600)
            self.assertAlmostEqual(full["background_evaluation_vehicle_hours"] - short["background_evaluation_vehicle_hours"], 300 / 3600)
            with self.assertRaises(ValueError):
                agent_costs(p, p, 1600)

    def test_signal_assistance_only_extends_existing_green_once(self):
        # A narrow mock tests the safety-relevant controller contract.
        c = Mock()
        c.simulation.getArrivedIDList.return_value = []
        c.simulation.getDepartedIDList.return_value = []
        c.vehicle.getRoadID.return_value = ":internal"
        c.vehicle.getNextTLS.return_value = [("tls", 0, 25, "r")]
        c.trafficlight.getPhase.return_value = 1
        c.trafficlight.getNextSwitch.return_value = 2420
        c.trafficlight.getRedYellowGreenState.return_value = "Gr"
        obj = EmergencyController.__new__(EmergencyController)
        obj.c, obj.policy, obj.departed, obj.arrived = c, "combined", 2400.5, None
        obj.restricted, obj.next_action = False, 2402
        obj.extended, obj.seen_phase = set(), {}
        obj.yield_requests = obj.green_extensions = 0
        obj.scenario = {"with_event": False, "incident": {"begin_seconds": 2100, "end_seconds": 3000},
            "yield": {"interval_seconds": 2}, "signal": {"lookahead_m_assumed": 120, "maximum_extension_s_assumed": 10}}
        obj.record = Mock()
        obj.trace_writer = Mock()
        c.vehicle.getPosition.return_value = (0, 0)
        obj.step(2402)
        c.trafficlight.setPhaseDuration.assert_not_called()
        c.vehicle.getNextTLS.return_value = [("tls", 0, 25, "G")]
        c.trafficlight.getPhase.return_value = 2
        obj.step(2404)
        c.trafficlight.setPhaseDuration.assert_called_once_with("tls", 26)
        obj.step(2406)
        self.assertEqual(c.trafficlight.setPhaseDuration.call_count, 1)
        c.trafficlight.setRedYellowGreenState.assert_not_called()
        c.vehicle.setSpeedMode.assert_not_called()
        c.vehicle.moveTo.assert_not_called()


if __name__ == "__main__":
    unittest.main()
