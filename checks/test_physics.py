"""具有独立物理预期的核查；AI辅助，见records/ai_usage.md。"""
import unittest

import numpy as np
import pandas as pd

from bev.model import InfeasibleSimulation, InitialState, battery_current, load_curves, load_parameters, simulate


class PhysicsChecks(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.parameters = load_parameters()
        cls.curves = load_curves()

    def simple_cycle(self, time, speed, ambient=25):
        return pd.DataFrame({"time_s": time, "speed_m_s": speed, "ambient_c": ambient})

    def test_battery_port_power_and_charge_direction(self):
        for power in (50000, -20000, 0):
            current, voltage = battery_current(power, 375, 0.1)
            self.assertAlmostEqual(voltage*current, power, places=8)
            self.assertAlmostEqual(375*current, power+0.1*current**2, places=8)
            self.assertEqual(np.sign(current), np.sign(power))

    def test_power_above_rint_capability_is_rejected(self):
        with self.assertRaises(InfeasibleSimulation):
            battery_current(400000, 375, 0.1)

    def test_initial_soc_at_full_charge_can_discharge(self):
        trace, out = simulate(self.simple_cycle([0, 60], [0, 0]), initial_state=InitialState(soc=1), curves=self.curves)
        self.assertLess(out["soc_final"], 1)
        self.assertTrue((trace.current_a>0).all())

    def test_ocv_offset_changes_electrical_state_without_inventing_load(self):
        cycle = self.simple_cycle([0, 20], [15, 15])
        _, original = simulate(cycle, curves=self.curves)
        _, shifted = simulate(cycle, {**self.parameters, "ocv_offset_v": -10}, curves=self.curves)
        self.assertAlmostEqual(original["net_energy_kwh"], shifted["net_energy_kwh"])
        self.assertLess(shifted["soc_final"], original["soc_final"])

    def test_stationary_neutral_system_has_no_spurious_energy(self):
        p = {**self.parameters, "low_voltage_w": 0, "occupant_heat_w": 0, "cabin_target_c": 25}
        trace, out = simulate(self.simple_cycle([0, 60], [0, 0]), p, curves=self.curves)
        self.assertAlmostEqual(out["net_energy_kwh"], 0)
        self.assertAlmostEqual(out["soc_final"], 0.8)
        self.assertTrue(np.allclose(trace.battery_c, 25))
        self.assertTrue(np.allclose(trace.cabin_c, 25))

    def test_constant_cruise_matches_closed_form_energy(self):
        p = {**self.parameters, "low_voltage_w": 0, "occupant_heat_w": 0, "cabin_target_c": 25}
        speed, duration = 15, 60
        rolling = p["mass_kg"]*p["gravity_m_s2"]*p["rolling_coefficient"]
        air = 0.5*p["air_density_kg_m3"]*p["drag_coefficient"]*p["frontal_area_m2"]*speed**2
        shaft = (rolling+air)*speed/p["gear_efficiency"]
        omega = speed*p["gear_ratio"]/p["wheel_radius_m"]
        torque = shaft/omega
        power = (shaft+300+0.222*torque**2+0.004*omega**2)/p["inverter_efficiency"]
        _, out = simulate(self.simple_cycle([0, duration], [speed, speed]), p, curves=self.curves)
        self.assertAlmostEqual(out["net_energy_kwh"], power*duration/3.6e6, places=10)
        self.assertAlmostEqual(out["distance_km"], speed*duration/1000)

    def test_lossless_acceleration_deceleration_returns_energy(self):
        p = {**self.parameters, "drag_coefficient": 0, "rolling_coefficient": 0, "gear_efficiency": 1,
             "inverter_efficiency": 1, "resistance_ref_ohm": 0, "low_voltage_w": 0,
             "occupant_heat_w": 0, "cabin_target_c": 25, "regen_fraction": 1}
        # 该解析理想实验还须固定OCV，避免变OCV下显式积分误差破坏精确电荷往返预期。
        curves = {**self.curves, "ocv_cell_v": [3.9]*len(self.curves["ocv_soc"]),
                  "motor_loss": {"constant_w": 0, "torque_coefficient": 0, "speed_coefficient": 0}}
        trace, out = simulate(self.simple_cycle([0, 10, 20], [0, 10, 0]), p, curves=curves)
        kinetic_kwh = 0.5*p["rotating_mass_factor"]*p["mass_kg"]*10**2/3.6e6
        self.assertAlmostEqual(out["traction_dc_energy_kwh"], kinetic_kwh, places=10)
        self.assertAlmostEqual(out["regen_dc_energy_kwh"], kinetic_kwh, places=10)
        self.assertAlmostEqual(out["net_energy_kwh"], 0, places=10)
        self.assertAlmostEqual(out["soc_final"], 0.8, places=10)
        self.assertTrue(np.allclose(trace.friction_brake_w, 0))

    def test_braking_recovery_does_not_exceed_kinetic_energy(self):
        trace, out = simulate(self.simple_cycle([0, 20], [20, 0]), curves=self.curves)
        kinetic_kwh = 0.5*self.parameters["rotating_mass_factor"]*self.parameters["mass_kg"]*20**2/3.6e6
        self.assertGreater(out["regen_dc_energy_kwh"], 0)
        self.assertLess(out["regen_dc_energy_kwh"], kinetic_kwh)
        self.assertTrue((trace.friction_brake_w >= 0).all())
        self.assertLess(out["max_motor_balance_residual_w"], 1e-7)

    def test_hot_idle_battery_relaxes_towards_ambient(self):
        p = {**self.parameters, "low_voltage_w": 0, "occupant_heat_w": 0,
             "cabin_target_c": 25, "cooling_on_c": 50, "cooling_off_c": 45}
        _, out = simulate(self.simple_cycle([0, 120], [0, 0]), p, InitialState(battery_c=35), curves=self.curves)
        self.assertGreater(out["battery_final_c"], 25)
        self.assertLess(out["battery_final_c"], 35)
        self.assertLess(out["max_battery_thermal_residual_w"], 1e-6)

    def test_drive_hardware_limit_cannot_be_silently_clipped(self):
        with self.assertRaises(InfeasibleSimulation):
            simulate(self.simple_cycle([0, 1], [0, 100]), curves=self.curves)

    def test_observation_columns_cannot_change_forward_prediction(self):
        cycle = self.simple_cycle([0, 30, 60], [10, 15, 10])
        _, first = simulate(cycle, curves=self.curves)
        cycle["soc"] = [0.2, 0.99, 0.1]
        cycle["current_a"] = [99999, -99999, 0]
        cycle["voltage_v"] = [1, 2, 3]
        _, second = simulate(cycle, curves=self.curves)
        self.assertEqual(first["soc_final"], second["soc_final"])
        self.assertEqual(first["net_energy_kwh"], second["net_energy_kwh"])

    def test_internal_resistance_changes_soc_and_loss_not_fixed_terminal_load(self):
        p = {**self.parameters, "occupant_heat_w": 0, "cabin_target_c": 25}
        cycle = self.simple_cycle([0, 600], [20, 20])
        _, lower = simulate(cycle, {**p, "resistance_ref_ohm": 0.05}, curves=self.curves)
        _, higher = simulate(cycle, {**p, "resistance_ref_ohm": 0.2}, curves=self.curves)
        self.assertAlmostEqual(lower["net_energy_kwh"], higher["net_energy_kwh"], places=10)
        self.assertGreater(higher["energy_kwh"]["joule_heat_w"], lower["energy_kwh"]["joule_heat_w"])
        self.assertGreater(higher["ocv_electric_energy_kwh"], lower["ocv_electric_energy_kwh"])
        self.assertLess(higher["soc_final"], lower["soc_final"])


if __name__ == "__main__":
    unittest.main()
