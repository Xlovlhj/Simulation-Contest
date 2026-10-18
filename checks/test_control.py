"""控制接口、约束和解析热平衡核查；AI辅助见records。"""
import unittest

import numpy as np
import pandas as pd

from bev.control import FeedbackController, Rule, control_protocol, scenario_parameters
from bev.model import InitialState, InfeasibleSimulation, load_parameters, simulate


class ControlChecks(unittest.TestCase):
    def test_empty_controller_preserves_shared_plant_prediction(self):
        cycle = pd.DataFrame({"time_s":[0.,20.,40.], "speed_m_s":[10.,20.,10.], "ambient_c":25.})
        original, first = simulate(cycle)
        controlled, second = simulate(cycle, controller=lambda state:{})
        for column in ["soc","battery_w","battery_c","cabin_c","current_a","voltage_v"]:
            np.testing.assert_array_equal(original[column], controlled[column])
        self.assertEqual(first["net_energy_kwh"], second["net_energy_kwh"])

    def test_control_and_measured_auxiliaries_cannot_be_double_counted(self):
        cycle = pd.DataFrame({"time_s":[0.,10.], "speed_m_s":10., "ambient_c":25., "ac_w":300.})
        with self.assertRaises(ValueError):
            simulate(cycle, controller=lambda state:{})

    def test_invalid_actuator_requests_are_rejected(self):
        cycle = pd.DataFrame({"time_s":[0.,10.], "speed_m_s":10., "ambient_c":25.})
        for action in [{"regen_fraction":1.1}, {"cooler_on":"yes"}, {"ac_w":5000.}]:
            with self.subTest(action=action), self.assertRaises(InfeasibleSimulation):
                simulate(cycle, controller=lambda state:action)
        with self.assertRaises(InfeasibleSimulation):
            simulate(cycle, {**load_parameters(),"compressor_total_max_w":100.}, controller=lambda state:{"ac_w":200.})

    def test_feedforward_balances_cabin_heat_in_hot_and_cold_idle(self):
        cfg = control_protocol()
        for ambient in [-10.,40.]:
            with self.subTest(ambient=ambient):
                p = scenario_parameters(load_parameters(),ambient,cfg)
                controller = FeedbackController(p,Rule(**cfg["baseline"]),cfg)
                cycle = pd.DataFrame({"time_s":[0.,60.],"speed_m_s":0.,"ambient_c":ambient})
                trace,out = simulate(cycle,p,InitialState(battery_c=ambient,cabin_c=24.),controller=controller)
                self.assertTrue(np.allclose(trace.cabin_c,24.,atol=1e-10))
                expected_heat = p["cabin_environment_w_k"]*(ambient-24)+p["solar_heat_w"]+p["occupant_heat_w"]
                if expected_heat>0:
                    self.assertAlmostEqual(trace.ac_w.iloc[0],expected_heat/p["cooling_cop"])
                else:
                    self.assertAlmostEqual(trace.heater_w.iloc[0],-expected_heat/p["heater_efficiency"])
                self.assertLess(abs(out["dc_balance_residual_kwh"]),1e-10)

    def test_controller_cannot_override_soc_or_cold_regen_protection(self):
        p = {**load_parameters(),"low_voltage_w":0.}
        action = lambda state:{"regen_fraction":1.,"cooler_on":False,"ac_w":0.,"heater_w":0.}
        for soc,temp in [(.99,25.),(.8,-10.)]:
            cycle = pd.DataFrame({"time_s":[0.,20.],"speed_m_s":[20.,0.],"ambient_c":temp})
            _,out = simulate(cycle,p,InitialState(soc=soc,battery_c=temp,cabin_c=24.),controller=action)
            self.assertEqual(out["regen_dc_energy_kwh"],0.)

    def test_economy_rule_coasts_without_opposite_season_device(self):
        cfg = control_protocol()
        rule = Rule(.95,40.,2.,1.5)
        for ambient in [-10.,25.]:
            p = scenario_parameters(load_parameters(),ambient,cfg)
            controller = FeedbackController(p,rule,cfg)
            action = controller({"time_s":0.,"remaining_s":1800.,"battery_c":ambient,"cabin_c":24.,"ambient_c":ambient})
            if ambient<24:
                self.assertEqual(action["ac_w"],0.)
            else:
                self.assertEqual(action["heater_w"],0.)


if __name__=="__main__":
    unittest.main()
