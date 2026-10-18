"""防泄漏及已知参数合成恢复；合成实验不等于实车验证。AI辅助见记录。"""
import copy
import unittest

import numpy as np
import pandas as pd
from scipy.optimize import least_squares

from bev.calibration import TripData, forward, prepare_trip, protocol, residual_parts
from bev.cycles import load_cltc
from bev.model import InitialState, load_parameters


class IdentificationChecks(unittest.TestCase):
    def test_public_cltc_sequence_matches_two_sources(self):
        cycle, metadata = load_cltc()
        self.assertEqual(len(cycle), 1801)
        self.assertEqual(cycle.time_s.iloc[-1], 1800)
        self.assertLess(metadata["cross_source_max_difference_m_s"], 1e-7)
        self.assertFalse(metadata["attachment3_is_identical"])
        self.assertAlmostEqual(metadata["distance_km"], 14.47975, places=6)

    def test_future_observations_are_not_forward_inputs(self):
        trip = prepare_trip(575)
        _, _, first = forward(trip, load_parameters(), 1)
        corrupted = copy.deepcopy(trip)
        for name in ["soc", "discharge_current_a", "voltage_v", "battery_power_w"]:
            corrupted.observation[name] = -9876.0
        _, _, second = forward(corrupted, load_parameters(), 1)
        for key in first:
            np.testing.assert_array_equal(first[key], second[key])

    def test_three_parameters_recovered_from_excited_synthetic_cycle(self):
        p = load_parameters()
        names = ["drag_coefficient", "rolling_coefficient", "resistance_ref_ohm"]
        truth = np.array([.34, .008, .075])
        p.update(dict(zip(names, truth)))
        t = np.arange(0., 301., 1.)
        # 多速度、加减速提供空气/滚阻与内阻不同的激励。
        v = 16+7*np.sin(t/40)+2*np.sin(t/11)
        cycle = pd.DataFrame({"time_s": t, "speed_m_s": v, "ambient_c": 25.})
        obs = pd.DataFrame({"time_s": t[::3]})
        trip = TripData(-1, obs, cycle, InitialState(soc=.65), {})
        _, _, target = forward(trip, p, 1)
        def residual(x):
            _, _, prediction = forward(trip, {**p, **dict(zip(names, x))}, 1)
            return np.r_[(prediction["soc"]-target["soc"])/.001,
                         (prediction["voltage_v"]-target["voltage_v"])/.5]
        fit = least_squares(residual, [.24, .013, .11], bounds=([.12,.003,.015],[.6,.03,.3]),
                            x_scale="jac", max_nfev=35, ftol=1e-9, gtol=1e-9, xtol=1e-9)
        np.testing.assert_allclose(fit.x, truth, rtol=.005, atol=1e-5)

    def test_net_energy_residual_contributions_sum_to_aggregate(self):
        cfg = protocol()
        parts, metadata = residual_parts(prepare_trip(596), load_parameters(), cfg)
        self.assertAlmostEqual(sum(metadata[-1]["contribution_residual"]), parts[-1][0], places=10)


if __name__ == "__main__":
    unittest.main()
