"""连续状态、停车接口、观测偏差与比较条件的物理核查。"""
import unittest

import numpy as np
import pandas as pd

from bev.control import FeedbackController, Rule, control_protocol, scenario_parameters
from bev.durability import DriveAndRecoveryController, compare_checkpoint, repeat_cycle
from bev.model import InitialState, load_parameters, simulate


class DurabilityChecks(unittest.TestCase):
    def test_repeated_cycle_conserves_duration_distance_and_endpoints(self):
        cycle=pd.DataFrame({"time_s":[10.,12.,15.],"speed_m_s":[0.,4.,0.],"ambient_c":25.})
        result=repeat_cycle(cycle,3)
        self.assertEqual(len(result),7)
        self.assertEqual(result.time_s.iloc[-1],15.)
        self.assertTrue((np.diff(result.time_s)>0).all())
        distance=lambda f: np.sum(np.diff(f.time_s)*(f.speed_m_s.to_numpy()[:-1]+f.speed_m_s.to_numpy()[1:])/2)
        self.assertAlmostEqual(distance(result),3*distance(cycle))
        with self.assertRaises(ValueError):
            repeat_cycle(cycle.assign(speed_m_s=[1.,4.,0.]),3)

    def test_segmented_state_continuation_matches_continuous_plant(self):
        cycle=pd.DataFrame({"time_s":[0.,20.,40.],"speed_m_s":0.,"ambient_c":40.})
        p=load_parameters()
        action=lambda _: {"cooler_on":False,"ac_w":100.,"heater_w":0.}
        initial=InitialState(.94,40.,24.)
        whole,_=simulate(cycle,p,initial,controller=action)
        first,_=simulate(cycle.iloc[:2],p,initial,controller=action)
        final=first.iloc[-1]
        second,_=simulate(cycle.iloc[1:],p,InitialState(final.soc,final.battery_c,final.cabin_c),controller=action)
        joined=pd.concat([first,second],ignore_index=True)
        np.testing.assert_array_equal(whole[["soc","battery_c","cabin_c","battery_w"]],joined[["soc","battery_c","cabin_c","battery_w"]])

    def test_parking_horizon_does_not_delay_driving_terminal_return(self):
        cfg=control_protocol()
        p=scenario_parameters(load_parameters(),40.,cfg)
        wrapper=DriveAndRecoveryController(p,Rule(**cfg["baseline"]),1800.)
        received=[]
        wrapper.drive=lambda o: received.append(o) or {}
        wrapper({"time_s":1600.,"remaining_s":7400.})
        self.assertEqual(received[0]["remaining_s"],200.)

    def test_switch_dwell_survives_parking_phase_handoff(self):
        cfg=control_protocol()
        p=scenario_parameters(load_parameters(),25.,cfg)
        wrapper=DriveAndRecoveryController(p,Rule(**cfg["baseline"]),100.)
        wrapper.drive.cooler_on=True
        wrapper.drive.last_switch=99.
        wrapper.drive.switch_times=[99.]
        observation={"time_s":100.,"battery_c":25.,"cabin_c":24.,"ambient_c":25.}
        self.assertTrue(wrapper(observation)["cooler_on"])
        self.assertFalse(wrapper({**observation,"time_s":130.})["cooler_on"])
        self.assertEqual(wrapper.switch_times,[99.,130.])

    def test_cabin_sensor_bias_matches_analytic_equilibrium(self):
        cfg=control_protocol()
        for ambient,bias in [(40.,.1),(-10.,-.1)]:
            p=scenario_parameters(load_parameters(),ambient,cfg)
            controller=FeedbackController(p,Rule(**cfg["baseline"]),cfg,sensor_bias_c=bias)
            cycle=pd.DataFrame({"time_s":[0.,1800.],"speed_m_s":0.,"ambient_c":ambient})
            # 强制全段采用终点目标24℃，使解析解的条件明确。
            trace,_=simulate(cycle,p,InitialState(.94,ambient,24.),controller=lambda o:controller({**o,"remaining_s":0.}))
            gain=cfg["cabin_feedback_w_k"]/(cfg["cabin_feedback_w_k"]+p["cabin_environment_w_k"])
            self.assertAlmostEqual(trace.cabin_c.iloc[-1]-24.,-gain*bias,places=7)

    def test_equal_thermal_ends_cannot_override_failed_constraints(self):
        point={"completed":True,"parking_s":0.,"feasible":True,"ambient_c":25.,
               "battery_final_c":25.,"cabin_final_c":24.,"energy_kwh":2.,"soc_final":.8,"soc_outside_training_fraction":.2}
        compared=compare_checkpoint(load_parameters(),point,{**point,"energy_kwh":1.8,"feasible":False})
        self.assertTrue(compared["near_equal_thermal_end"])
        self.assertFalse(compared["valid_close_end_comparison"])
