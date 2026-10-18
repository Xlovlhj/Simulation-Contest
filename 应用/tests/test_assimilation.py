"""Availability, missing data and immutable future-input contracts."""
# AI-assisted: OpenAI Codex; actual usage requires human review before submission.
from pathlib import Path
import sys
import unittest

import numpy as np
import pandas as pd

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from b_twin.assimilation import PROTOCOL, counts_at, update_rates, stamp


class CausalUpdates(unittest.TestCase):
    def setUp(self):
        self.mapping = pd.DataFrame([{"detector_id":"A", "sumo_edge":"e", "requires_review":False}])
        self.routes = pd.DataFrame([{"route_id":"r", "edges":"e", "estimated_vph":100., "source_mainline":True}])
        self.now = stamp("2026-10-15T08:00:00")

    def records(self, count=1, n=30, delay=360):
        end=self.now-pd.Timedelta(seconds=360)
        return pd.DataFrame([{"detector_id":"A",
            "observation_start":(end-pd.Timedelta(seconds=(n-i)*30)).isoformat(),
            "observation_end":(end-pd.Timedelta(seconds=(n-i-1)*30)).isoformat(),
            "available_at":(end-pd.Timedelta(seconds=(n-i-1)*30)+pd.Timedelta(seconds=delay)).isoformat(),
            "quality":"valid", "count":count, "availability_source":"test"} for i in range(n)])

    def test_future_receipt_and_future_observation_never_enter_fit(self):
        data=self.records()
        reference=counts_at(data,self.now,self.mapping)
        future=data.iloc[[-1]].copy()
        future['count']=100000
        future['available_at']=(self.now+pd.Timedelta(seconds=1)).isoformat()
        altered=counts_at(pd.concat([data,future],ignore_index=True),self.now,self.mapping)
        pd.testing.assert_frame_equal(reference,altered)
        data['observation_end']=(self.now+pd.Timedelta(seconds=1)).isoformat()
        self.assertFalse(counts_at(data,self.now,self.mapping).iloc[0].eligible)

    def test_duplicate_payload_cannot_inflate_counts_or_coverage(self):
        data=self.records()
        a=counts_at(data,self.now,self.mapping)
        b=counts_at(pd.concat([data,data],ignore_index=True),self.now,self.mapping)
        pd.testing.assert_frame_equal(a,b)
        self.assertEqual(b.iloc[0]['count'],30)
        self.assertEqual(b.iloc[0].coverage,1.)

    def test_missing_and_invalid_hold_last_plan_but_real_zero_is_evidence(self):
        for data in [self.records(n=2),self.records().assign(quality='invalid_or_incomplete')]:
            counts=counts_at(data,self.now,self.mapping)
            rate,_,info=update_rates(self.routes,counts,self.mapping,np.array([77.]))
            self.assertEqual(rate[0],77.)
            self.assertEqual(info['constraints'],0)
        counts=counts_at(self.records(count=0),self.now,self.mapping)
        rate,_,info=update_rates(self.routes,counts,self.mapping,np.array([100.]))
        self.assertEqual(info['constraints'],1)
        self.assertEqual(rate[0],75.)

    def test_staleness_and_mapping_flags_exclude_otherwise_complete_counts(self):
        counts=counts_at(self.records(),self.now+pd.Timedelta(seconds=300),self.mapping)
        self.assertEqual(counts.iloc[0].exclusion,'stale')
        rate,_,info=update_rates(self.routes,counts,self.mapping,np.array([77.]))
        self.assertEqual(rate[0],77.)
        counts=counts_at(self.records(),self.now,self.mapping)
        mapping=self.mapping.assign(requires_review=True)
        rate,_,info=update_rates(self.routes,counts,mapping,np.array([77.]))
        self.assertEqual(info['constraints'],0)
        self.assertEqual(rate[0],77.)

    def test_later_correction_is_used_only_after_its_availability(self):
        data=self.records()
        revised=data.iloc[[-1]].copy()
        revised['count']=10
        revised['available_at']=(self.now+pd.Timedelta(seconds=30)).isoformat()
        data=pd.concat([data,revised],ignore_index=True)
        self.assertEqual(counts_at(data,self.now,self.mapping).iloc[0]['count'],30)
        self.assertEqual(counts_at(data,self.now+pd.Timedelta(seconds=30),self.mapping).iloc[0]['count'],39)

    def test_large_inversion_jump_is_limited_without_forcing_speed(self):
        counts=counts_at(self.records(count=100),self.now,self.mapping)
        rate,_,info=update_rates(self.routes,counts,self.mapping,np.array([100.]))
        self.assertEqual(rate[0],125.)
        self.assertEqual(info['clipped_routes'],1)


if __name__=='__main__':
    unittest.main()
