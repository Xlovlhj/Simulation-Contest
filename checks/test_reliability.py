"""重抽样中心、跨通道时间依赖、总量一致性与激励的解析检查。"""
import unittest

import numpy as np
from scipy.optimize import least_squares

from bev.reliability import block_residual_matrix, centered_residual, column_information, require_training_only, roadload_design
from bev.model import load_parameters


class ReliabilityChecks(unittest.TestCase):
    def test_block_signs_preserve_channel_and_aggregate_energy_dependence(self):
        parts=[{"trip_id":575,"channel":"soc","time_s":[5.,15.]},
               {"trip_id":575,"channel":"voltage","time_s":[5.,15.]},
               {"trip_id":575,"channel":"power_window","time_s":[5.,15.]},
               {"trip_id":575,"channel":"net_energy","time_s":[10.],"contribution_time_s":[5.,15.],"contribution_residual":[2.,3.]},
               {"trip_id":596,"channel":"soc","time_s":[5.]}]
        residual=np.array([1.,4.,10.,40.,2.,3.,5.,7.])
        matrix,keys=block_residual_matrix(residual,parts,10.)
        signs=np.array([1. if key==(575,0) else -1. for key in keys])
        actual=matrix@signs
        np.testing.assert_array_equal(actual,[1.,-4.,10.,-40.,2.,-3.,-1.,-7.])
        np.testing.assert_array_equal(matrix@np.ones(len(keys)),residual)
        self.assertEqual(actual[6],actual[4]+actual[5])

    def test_invalid_aggregate_contributions_are_rejected(self):
        with self.assertRaises(ValueError):
            block_residual_matrix(np.array([3.]),[{"trip_id":575,"channel":"net_energy","time_s":[5.],"contribution_time_s":[5.],"contribution_residual":[2.]}],10.)

    def test_centering_recovers_known_linear_wild_solution(self):
        design=np.array([[1.,0.],[1.,1.],[1.,2.],[1.,3.]])
        observed=np.array([1.1,2.8,5.2,6.9])
        center=np.linalg.lstsq(design,observed,rcond=None)[0]
        residual=design@center-observed
        perturb=residual*np.array([1.,1.,-1.,-1.])
        fit=least_squares(lambda x:centered_residual(design@x-observed,residual,perturb),center)
        expected=center-np.linalg.pinv(design)@perturb
        np.testing.assert_allclose(fit.x,expected,atol=1e-8)

    def test_zero_noise_recovers_nonlinear_center_and_constraints_limit_distribution(self):
        prediction=lambda x:np.array([x[0]**2,2*x[0]**2])
        center=np.array([.7])
        observed=np.array([.1,.9])
        residual=prediction(center)-observed
        fit=least_squares(lambda x:centered_residual(prediction(x)-observed,residual,np.zeros(2)),[.5],bounds=([.2],[1.]))
        np.testing.assert_allclose(fit.x,center,atol=1e-8)
        bounded=least_squares(lambda x:centered_residual(prediction(x)-observed,residual,np.ones(2)*10),[.5],bounds=([.2],[1.]))
        self.assertAlmostEqual(bounded.x[0],.2,places=7)

    def test_constant_speed_cannot_separate_two_road_load_coefficients(self):
        p=load_parameters()
        constant=column_information(roadload_design(np.full(20,10.),np.ones(20),p))
        varied=column_information(roadload_design(np.linspace(3,30,20),np.ones(20),p))
        self.assertEqual(constant["rank"],1)
        self.assertIsNone(constant["normalized_condition"])
        self.assertEqual(varied["rank"],2)
        self.assertLess(varied["column_cosine"],.99)

    def test_evaluation_trip_cannot_enter_reliability_fit(self):
        cfg={"fit_trip_ids":[575,596],"validation_trip_ids":[588],"test_trip_ids":[600]}
        require_training_only(cfg,{"fit_trip_ids":[575,596]})
        with self.assertRaises(ValueError):
            require_training_only(cfg,{"fit_trip_ids":[575,600]})
        with self.assertRaises(ValueError):
            require_training_only({**cfg,"validation_trip_ids":[596]},{"fit_trip_ids":[575,596]})
