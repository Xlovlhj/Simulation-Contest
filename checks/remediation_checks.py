"""阶段八解析检查，单独运行，不改变历史34项核查。AI辅助：Codex。"""
import unittest
import numpy as np
import pandas as pd
from bev.calibration import CalibrationProblem, forward, prepare_trip, protocol
from bev.control import Rule,control_protocol,scenario_parameters
from bev.model import InitialState,load_curves,load_parameters,simulate
from bev.remediation import CabinBiasObserver,CorrectedProblem,ImprovedController,corrected_forward,scaled_curves


class RemediationChecks(unittest.TestCase):
    def test_unit_ocv_scale_preserves_original_predictions_and_residual(self):
        trip=prepare_trip(575);p=load_parameters()
        a,_,pa=forward(trip,p,1.)
        b,_,pb=corrected_forward(trip,{**p,'ocv_scale':1.},1.)
        for key in ['soc','voltage_v','battery_c','battery_w']:
            np.testing.assert_allclose(a[key],b[key],atol=1e-10,rtol=1e-12)
        old=CalibrationProblem(protocol());new=CorrectedProblem()
        vector=np.array([.28,.01,.1,80.,-8.])
        np.testing.assert_allclose(old.residual(vector),new.residual(np.r_[vector,1.]),atol=1e-9)

    def test_ocv_correction_preserves_anchor_and_monotonicity(self):
        source=load_curves();anchor=np.interp(.95,source['ocv_soc'],source['ocv_cell_v'])
        for scale in [.1,.3,1.,2.]:
            c=scaled_curves(scale)
            self.assertTrue((np.diff(c['ocv_cell_v'])>0).all())
            self.assertAlmostEqual(np.interp(.95,c['ocv_soc'],c['ocv_cell_v']),anchor)
        with self.assertRaises(ValueError):scaled_curves(-1.)

    def test_observer_learns_bias_without_true_initial_temperature(self):
        p=load_parameters();observer=CabinBiasObserver(p)
        true_temperature=26.;bias=1.
        action={'ac_w':0.,'heater_w':0.}
        observer.previous_action=action
        for i in range(3600):
            now=i*.5
            observer.update(now,true_temperature+bias,25.)
            true_temperature+=(p['cabin_environment_w_k']*(25-true_temperature)+p['occupant_heat_w'])*.5/p['cabin_heat_capacity_j_k']
        self.assertAlmostEqual(observer.state[1],bias,delta=.02)
        self.assertAlmostEqual(observer.state[0],true_temperature,delta=.02)

    def test_augmented_observability_requires_heat_exchange(self):
        for a in [.99,1.]:
            h=np.array([[1.,1.]]);f=np.diag([a,1.])
            self.assertEqual(np.linalg.matrix_rank(np.vstack([h,h@f])),2 if a!=1 else 1)

    def test_controller_gets_no_hidden_sensor_bias_or_future_speed(self):
        p=scenario_parameters(load_parameters(),40.,control_protocol())
        controller=ImprovedController(p,Rule(**control_protocol()['baseline']),1800.)
        reading={'time_s':0.,'battery_c':41.,'cabin_c':25.,'ambient_c':40.,'remaining_s':1800.,'dt_s':.5}
        action=controller(reading)
        self.assertEqual(controller.observer.state[1],0.)
        self.assertEqual(controller.observer.state[0],25.)
        self.assertIn('cooler_on',action)

    def test_bias_and_unknown_heat_are_aliased_at_equilibrium(self):
        # y=T+b=(Ta+q/K)+b：未知热量K*b与温度偏差不能无条件区分。
        p=load_parameters();k=p['cabin_environment_w_k'];q=p['occupant_heat_w']
        self.assertAlmostEqual(25+q/k+1.,25+(q+k)/k)


if __name__=='__main__':unittest.main(verbosity=2)
