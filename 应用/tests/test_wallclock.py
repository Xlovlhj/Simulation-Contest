"""Operational timing, asynchronous transport and future-batch contracts."""
# AI-assisted: OpenAI Codex; actual usage requires human review.
from datetime import datetime
from pathlib import Path
import gzip
import hashlib
import sys
import tempfile
import unittest
from unittest.mock import patch

import pandas as pd
import requests

sys.path.insert(0,str(Path(__file__).resolve().parents[1]))
from b_twin.wallclock import PollGate, step_delay, snapshot_rows, acquire, make_batch
from b_twin.live import TZ
from b_twin.paths import INPUTS, REFINED
from b_twin.live_accounting import reconcile
import xml.etree.ElementTree as ET
import sumolib


def payload(duplicate=False):
    lane='<lane><lane_id>1</lane_id><valid>Y</valid><volume>3</volume><speed>20</speed><occupancy>5</occupancy></lane>'
    return ('<raw_speed_volume_list><date>2026-10-18</date><periods><period>'
        '<period_from>01:00:00</period_from><period_to>01:00:30</period_to><detectors>'
        '<detector><detector_id>A</detector_id><lanes>'+lane+(lane if duplicate else '')+
        '</lanes></detector></detectors></period></periods></raw_speed_volume_list>').encode()


class WallclockContracts(unittest.TestCase):
    def test_cutoff_loaded_not_yet_attempted_is_included_in_conservation(self):
        trips=[ET.Element('tripinfo',id=str(i),depart='-1' if i>=5 else '0',arrival='10' if i<3 else '-1') for i in range(8)]
        result=reconcile([str(i) for i in range(8)],trips,running=2,pending=1)
        self.assertEqual(result['loaded_not_yet_attempted_at_cutoff'],2)
        self.assertEqual(result['unfinished_total_at_cutoff'],5)
        self.assertEqual(result['conservation_residual'],0)

    def test_request_singleflight_and_no_catchup_burst(self):
        gate=PollGate(60)
        self.assertTrue(gate.reserve(0))
        self.assertFalse(gate.reserve(200))
        gate.finish()
        self.assertTrue(gate.reserve(200))
        gate.finish()
        self.assertFalse(gate.reserve(200.1))
        self.assertFalse(gate.reserve(259.9))
        self.assertTrue(gate.reserve(260))

    def test_step_wait_prevents_simulation_running_ahead_of_wallclock(self):
        self.assertAlmostEqual(step_delay(12.,12.1),.4)
        self.assertEqual(step_delay(12.,14.),0.)
        for elapsed in [0.,.1,.49,.5,1.,100.]:
            wait=step_delay(0.,elapsed)
            self.assertGreaterEqual(elapsed+wait,.5)

    def test_duplicate_lanes_are_invalid_without_turning_missing_into_zero(self):
        received=datetime(2026,10,18,1,1,tzinfo=TZ)
        good=snapshot_rows(payload(),{'A':['1']},received)[0]
        bad=snapshot_rows(payload(True),{'A':['1']},received)[0]
        self.assertEqual(good['count'],3.)
        self.assertEqual(good['available_at'],received.isoformat())
        self.assertEqual(bad['quality'],'invalid_or_incomplete')
        self.assertIsNone(bad['count'])

    def test_http_timeout_retains_failure_and_success_archives_exact_payload(self):
        with tempfile.TemporaryDirectory() as temp:
            out=Path(temp);(out/'raw').mkdir()
            with patch('b_twin.wallclock.requests.get',side_effect=requests.Timeout()):
                record,rows=acquire(out,{'A':['1']},0)
            self.assertFalse(record['success']);self.assertEqual(rows,[])
            with patch('b_twin.wallclock.requests.get') as get:
                get.return_value.content=payload()
                record,rows=acquire(out,{'A':['1']},1)
            self.assertTrue(record['success']);self.assertEqual(len(rows),1)
            raw=gzip.decompress((out/record['file']).read_bytes())
            self.assertEqual(raw,payload())
            self.assertEqual(hashlib.sha256(raw).hexdigest(),record['sha256_uncompressed'])

    def test_new_batch_is_future_only_unique_and_preserves_previous_agents(self):
        routes=pd.read_csv(INPUTS/'ordinary_inferred_routes.csv')
        fleet=pd.read_csv(INPUTS/'vehicle_class_priors.csv')
        fleet=fleet[fleet.case.eq('ordinary')]
        net=sumolib.net.readNet(str(REFINED/'corridor.net.xml'))
        now=pd.Timestamp('2026-10-18T01:00:00+08:00')
        rates=routes.estimated_vph.to_numpy(dtype=float)
        first,_=make_batch(routes,rates,fleet,net,0,300,now,0)
        frozen=first.copy(deep=True)
        second,_=make_batch(routes,rates,fleet,net,300,600,now+pd.Timedelta(seconds=300),1)
        self.assertTrue(second.depart.ge(300.5).all() and second.depart.lt(600).all())
        self.assertFalse(set(first.vehicle_id)&set(second.vehicle_id))
        pd.testing.assert_frame_equal(first,frozen)


if __name__=='__main__':
    unittest.main()
