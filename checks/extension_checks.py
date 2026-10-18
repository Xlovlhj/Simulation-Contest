"""阶段十解析与耦合核查，独立于原34项检查。"""
import unittest

import numpy as np
import pandas as pd

from bev.model import battery_current, simulate as original, InitialState, load_parameters
from bev.voltage_rc import rc_step, simulate
from bev.extension import thermal_prediction, DirectReading


class ExtensionChecks(unittest.TestCase):
    def test_rc_exact_current_and_state(self):
        i, ocv, r0, rp, tau, vp, dt = 40., 390., .05, .03, 20., 1.8, .5
        a = -np.expm1(-dt/tau)/(dt/tau)
        voltage = ocv-r0*i-(rp*i+(vp-rp*i)*a)
        out = rc_step(i*voltage, ocv, r0, rp, tau, vp, dt)
        self.assertAlmostEqual(out['current_a'], i, places=10)
        self.assertAlmostEqual(out['next_v'], rp*i+(vp-rp*i)*np.exp(-dt/tau), places=12)

    def test_rc_heat_storage_balance_both_directions(self):
        for power in [-20000., 0., 50000.]:
            out = rc_step(power, 390., .06, .04, 80., -3., .5)
            self.assertGreaterEqual(out['heat_w'], 0)
            self.assertAlmostEqual(390*out['current_a']-power-out['heat_w']-out['storage_w'], 0., places=7)
            if power != 0:
                self.assertEqual(np.sign(power), np.sign(out['current_a']))

    def test_zero_current_relaxes_and_releases_storage_as_heat(self):
        out = rc_step(0., 390., .06, .04, 80., 4., 2.)
        self.assertAlmostEqual(out['current_a'], 0.)
        self.assertLess(abs(out['next_v']), 4.)
        self.assertAlmostEqual(out['heat_w']+out['storage_w'], 0., places=9)

    def test_small_rc_matches_Rint(self):
        old = battery_current(20000., 390., .06)
        new = rc_step(20000., 390., .06, 1e-10, 10., 0., .5)
        self.assertAlmostEqual(old[0], new['current_a'], places=8)
        self.assertAlmostEqual(old[1], new['voltage_v'], places=8)

    def test_forward_limit_and_no_measurement_leak(self):
        cycle = pd.DataFrame({'time_s': [0., 10., 20., 40.], 'speed_m_s': [0., 5., 5., 0.], 'ambient_c': 25.})
        p = load_parameters()
        p.update(polarization_resistance_ohm=1e-10, polarization_tau_s=10.)
        a, _ = original(cycle, p, InitialState())
        b, s = simulate(cycle.assign(soc=-999., current_a=-999., voltage_v=-999.), p, InitialState())
        self.assertLess(np.max(abs(a.voltage_v-b.voltage_v)), 1e-7)
        self.assertLess(s['max_battery_electrical_residual_w'], 1e-7)
        self.assertLess(s['max_battery_thermal_residual_w'], 1e-5)

    def test_external_thermal_closed_form(self):
        t = np.arange(101.)
        result = thermal_prediction(t, np.full(len(t), 2.), 40., .003, .002)
        expected = 40+.003*4/.002*(1-np.exp(-.002*t))
        self.assertLess(np.max(abs(result-expected)), 1e-11)

    def test_direct_reading_does_not_invent_bias_estimate(self):
        self.assertEqual(DirectReading().update(0., 25.1, -10.), 25.1)


if __name__ == '__main__':
    unittest.main(verbosity=2)
