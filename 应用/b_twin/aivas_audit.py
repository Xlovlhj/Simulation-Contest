"""Reparse all captured AIVAS receipts, hashes and deduplicated observations.

AI-assisted: OpenAI Codex; actual use and human review must be disclosed.
"""
from __future__ import annotations
from datetime import datetime
import importlib.util
import json
from pathlib import Path
import tempfile
import pandas as pd
from .paths import DATA, APP
from .runner import digest


def audit(directory):
    source = DATA / 'scripts/collect_aivas.py'
    spec = importlib.util.spec_from_file_location('_aivas_collector_audit', source)
    collector = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(collector)
    protocol = json.loads((directory / 'protocol.json').read_text(encoding='utf-8'))
    assert protocol['status'] == 'completed'
    assert digest(source) == protocol['source_script_sha256']
    records = [json.loads(line) for line in (directory / 'collection.jsonl').read_text(encoding='utf-8').splitlines()]
    observations = []
    for r in records:
        assert r['camera'] in protocol['cameras']
        if not r['success']:
            continue
        raw = directory / r['source_file']
        assert digest(raw) == r['source_sha256']
        received = datetime.fromisoformat(r['received_at'])
        assert datetime.fromisoformat(r['requested_at']) <= received
        rows = collector.normalized(json.loads(raw.read_bytes()), r['camera'], received, r['source_file'], r['source_sha256'])
        assert len(rows) == r['rows']
        assert sum(x['quality'].startswith('valid_') for x in rows) == r['eligible_rows']
        for row in rows:
            if row['quality'].startswith('valid_'):
                assert row['source_age_seconds'] >= 0
            if row['observed_count'] == 0 or not row['quality'].startswith('valid_'):
                assert row['speed_for_evaluation_kmh'] is None
        observations.extend(rows)
    unique = {}
    for row in observations:
        key = row['camera'], row['segment'], row['observation_end']
        old = unique.get(key)
        if old is None or (not old['quality'].startswith('valid_') and row['quality'].startswith('valid_')):
            unique[key] = row
    with tempfile.TemporaryDirectory(prefix='b-aivas-audit-') as temp:
        for name, rows in [('samples_all_receipts.csv', observations), ('unique_observations.csv', unique.values())]:
            recalculated = Path(temp) / name
            collector.write_table(recalculated, rows)
            a = pd.read_csv(recalculated, dtype={'segment': str})
            b = pd.read_csv(directory / name, dtype={'segment': str})
            pd.testing.assert_frame_equal(a, b, check_dtype=False, rtol=1e-10, atol=1e-9)
    frame = pd.DataFrame(unique.values())
    valid = frame[frame.quality.str.startswith('valid_')]
    exposures = valid.groupby(['camera', 'segment']).observed_seconds.sum().reset_index(name='observed_sample_seconds')
    exposures.to_csv(directory / 'audited_sample_exposures.csv', index=False, encoding='utf-8-sig')
    request_times = [datetime.fromisoformat(r['requested_at']) for r in records]
    receipts = [datetime.fromisoformat(r['received_at']) for r in records if r.get('received_at')]
    result = {'request_records': len(records), 'successful_responses': sum(r['success'] for r in records),
        'raw_receipt_rows_reparsed': len(observations), 'deduplicated_segment_periods': len(unique),
        'quality_counts': {str(k): int(v) for k, v in frame.quality.value_counts().items()},
        'first_requested_at': min(request_times).isoformat(), 'last_receipt_at': max(receipts).isoformat(),
        'request_to_last_receipt_seconds': (max(receipts) - min(request_times)).total_seconds(),
        'declared_collection_window_minutes': protocol['duration_minutes'],
        'all_payload_sha256_verified': True, 'all_receipt_rows_recomputed': True, 'deduplication_recomputed': True,
        'zero_count_speeds_excluded': True, 'source_future_times_excluded': True,
        'unique_table_sha256': digest(directory / 'unique_observations.csv'),
        'scope': 'Intermittent 130-second samples during a declared one-hour window; not one hour of continuous traffic observations or model-accuracy evidence'}
    (directory / 'post_collection_audit.json').write_text(json.dumps(result, ensure_ascii=False, indent=2), encoding='utf-8')
    return result


if __name__ == '__main__':
    import argparse
    p = argparse.ArgumentParser()
    p.add_argument('directory', type=Path)
    args = p.parse_args()
    print(json.dumps(audit(args.directory), ensure_ascii=False, indent=2))
