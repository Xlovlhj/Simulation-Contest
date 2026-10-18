"""Check time alignment and official-feed state behavior with known values."""
# AI辅助：OpenAI Codex；精确版本/型号及发布日期未提供，提交前按实际使用记录补齐并人工核查。
import sys
from pathlib import Path
from datetime import datetime, timezone, timedelta
import tempfile
import unittest

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from b_twin.evaluation import aggregate_e1
from b_twin.live import parse_snapshot, merge_state


class ObservationContracts(unittest.TestCase):
    def test_e1_excludes_warmup_and_weights_lanes_by_vehicles(self):
        # Warmup has deliberately huge counts and must not affect the estimate.
        xml = ['<detector><interval id="A__0" begin="0" end="300" nVehContrib="9999" speed="99"/>']
        for begin in [300, 600, 900]:
            xml.append(f'<interval id="A__0" begin="{begin}" end="{begin + 300}" nVehContrib="10" speed="10"/>')
            xml.append(f'<interval id="A__1" begin="{begin}" end="{begin + 300}" nVehContrib="30" speed="20"/>')
        # A clearance-stage spike must also be excluded.
        xml.append('<interval id="A__0" begin="1200" end="1500" nVehContrib="9999" speed="99"/>')
        xml.append('</detector>')
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / 'e1.xml'
            path.write_text(''.join(xml), encoding='utf-8')
            _, measured = aggregate_e1(path, '2026-10-15T08:00:00', 900)
        self.assertEqual(len(measured), 1)
        self.assertEqual(measured.iloc[0].window, '2026-10-15T08:00:00')
        self.assertEqual(measured.iloc[0].simulated_count, 120)
        self.assertEqual(measured.iloc[0].simulated_flow_vph, 480)
        self.assertAlmostEqual(measured.iloc[0].simulated_speed_kmh, 63)

    def test_partial_simulated_window_is_rejected(self):
        xml = '<detector><interval id="A__0" begin="300" end="600" nVehContrib="1" speed="10"/></detector>'
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / 'e1.xml'
            path.write_text(xml, encoding='utf-8')
            with self.assertRaises(ValueError):
                aggregate_e1(path, '2026-10-15T08:00:00', 900)

    def test_live_counts_units_invalid_lanes_and_idempotence(self):
        xml = '''<raw_speed_volume_list><date>2026-10-17</date><periods><period>
          <period_from>14:32:00</period_from><period_to>14:32:30</period_to><detectors><detector>
          <detector_id>A</detector_id><lanes>
          <lane><lane_id>one</lane_id><volume>2</volume><speed>30</speed><occupancy>5</occupancy><valid>Y</valid></lane>
          <lane><lane_id>two</lane_id><volume>3</volume><speed>50</speed><occupancy>8</occupancy><valid>Y</valid></lane>
          </lanes></detector></detectors></period></periods></raw_speed_volume_list>'''
        now = datetime(2026, 10, 17, 14, 40, tzinfo=timezone(timedelta(hours=8)))
        rows = parse_snapshot(xml.encode(), {'A': ['one', 'two']}, now)
        self.assertEqual(rows[0]['observed_count_30s'], 5)
        self.assertEqual(rows[0]['sample_rate_vph'], 600)
        self.assertEqual(rows[0]['speed_kmh'], 42)
        self.assertEqual(rows[0]['age_seconds_at_receipt'], 450)
        state, updates, _ = merge_state({}, rows)
        self.assertEqual(updates, 1)
        again, updates, _ = merge_state(state, rows)
        self.assertEqual(updates, 0)
        self.assertEqual(again, state)
        invalid = parse_snapshot(xml.replace('<valid>Y</valid>', '<valid>N</valid>', 1).encode(), {'A': ['one', 'two']}, now)
        self.assertIsNone(invalid[0]['observed_count_30s'])
        self.assertIsNone(invalid[0]['sample_rate_vph'])
        unchanged, updates, _ = merge_state(state, invalid)
        self.assertEqual(updates, 0)
        self.assertEqual(unchanged, state)

    def test_older_or_future_observation_does_not_replace_valid_state(self):
        existing = {'A': {'detector_id': 'A', 'observation_end': '2026-10-17T14:32:30+08:00', 'quality': 'valid'}}
        older = {'detector_id': 'A', 'observation_end': '2026-10-17T14:31:30+08:00', 'quality': 'valid'}
        future = {'detector_id': 'A', 'observation_end': '2026-10-17T16:00:00+08:00', 'quality': 'future_timestamp'}
        state, updates, _ = merge_state(existing, [older, future])
        self.assertEqual(updates, 0)
        self.assertEqual(state, existing)


if __name__ == '__main__':
    unittest.main()
