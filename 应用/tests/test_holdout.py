"""Contracts preventing future/revised data leakage in independent validation."""
import unittest

import pandas as pd

from b_twin.holdout import causal_counts, latest_revisions, joint_check


class HoldoutContracts(unittest.TestCase):
    def stream(self):
        return pd.DataFrame([
            {'detector_id': 'S', 'observation_start': '2026-10-17T08:00:00+08:00',
             'observation_end': '2026-10-17T08:00:30+08:00', 'available_at': '2026-10-17T08:06:00+08:00',
             'quality': 'valid', 'count': 0., 'source_member': 'a', 'availability_source': 'test'},
            {'detector_id': 'S', 'observation_start': '2026-10-17T08:00:00+08:00',
             'observation_end': '2026-10-17T08:00:30+08:00', 'available_at': '2026-10-17T08:08:00+08:00',
             'quality': 'invalid_or_incomplete', 'count': None, 'source_member': 'b', 'availability_source': 'test'}])

    def test_later_invalid_revision_revokes_valid_record(self):
        mapping = pd.DataFrame([{'detector_id': 'S', 'requires_review': False}])
        result = causal_counts(self.stream(), '2026-10-17T08:09:00+08:00', mapping)
        self.assertEqual(result.iloc[0].valid_periods, 0)
        self.assertTrue(pd.isna(result.iloc[0]['count']))

    def test_future_correction_is_not_visible_early(self):
        result = latest_revisions(self.stream(), '2026-10-17T08:07:00+08:00')
        self.assertEqual(result.iloc[0].quality, 'valid')
        self.assertEqual(result.iloc[0]['count'], 0)

    def test_before_publication_is_missing_not_zero(self):
        mapping = pd.DataFrame([{'detector_id': 'S', 'requires_review': False}])
        result = causal_counts(self.stream(), '2026-10-17T08:05:00+08:00', mapping)
        self.assertFalse(result.iloc[0].eligible)
        self.assertTrue(pd.isna(result.iloc[0].flow_vph_estimated))

    def metrics(self):
        return {'station_windows': 20, 'observed_slow_station_windows': 5, 'reproduced_slow_station_windows': 5,
                'false_slow_station_windows': 0, 'speed_missing_simulated_windows': 0,
                'flow_wmape_pct': 1., 'speed_mae_kmh': 1., 'occupancy_mae_percentage_points': 1.}

    def test_small_flow_error_cannot_hide_missed_congestion(self):
        metrics = self.metrics()
        metrics['reproduced_slow_station_windows'] = 0
        self.assertFalse(joint_check(metrics, 20)['passed_error_checks'])

    def test_good_errors_with_insufficient_coverage_cannot_pass(self):
        self.assertFalse(joint_check(self.metrics(), 50)['passed_error_checks'])


if __name__ == '__main__':
    unittest.main()
