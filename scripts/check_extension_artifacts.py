"""核对本轮输入、冻结、数值证据和最终排版；不重新拟合。"""
from pathlib import Path
import re
import sys

import pandas as pd
import pymupdf

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT/'src'))
from bev.extension import begin
from bev.io import read_json, sha256, write_json, verify_sources
from bev.paper import verify_evidence

begin()
runtime = read_json(ROOT/'records/extension_runtime_verification.json')
assert runtime['passed'] and runtime['numeric_values_compared'] == 1791
for name, digest in runtime['fingerprint'].items():
    assert sha256(ROOT/name) == digest, name
exp = ROOT/'records/experiments/extension_v1'
for name, digest in runtime['expected_hashes'].items():
    assert sha256(exp/name) == digest, name
s = read_json(ROOT/'records/extension_summary.json')
for name, digest in s['source_hashes'].items():
    assert sha256(ROOT/name) == digest, name
assert s['guarded_practical_pass'] == 59 and s['practical_and_positive_ideal_adjusted_pairs'] == 20
assert s['sensor_failures'][0]['ambient_c'] == -10
data = pd.read_csv(exp/'voltage_comparison.csv')
for row in s['voltage_metrics']:
    actual = data[data.trip_id.eq(row['trip_id']) & data.variant.eq(row['variant'])].iloc[0]
    for key in ['energy_ape_pct', 'soc_rmse_pp', 'voltage_rmse_v']:
        assert abs(actual[key]-row[key]) < 1e-10
build = read_json(ROOT/'records/final_paper_build.json')
for name, digest in {**build['source_hashes'], **build['output_hashes']}.items():
    assert sha256(ROOT/name) == digest, name
preview = ROOT/'records/extension_artifact_checks'
preview.mkdir(exist_ok=True)
with pymupdf.open(ROOT/build['paper']['path']) as doc:
    text = '\n'.join(p.get_text() for p in doc)
    for value in ['6.033', '8.303', '35.88%', '0.543', '59/60', '20/30', 'ODC-ODbL']:
        assert value in text, value
    assert '目录' in doc[1].get_text() and doc[2].get_text().startswith('1 问题重述')
    for number in [1, 12, 13, 17, 18, 19, 20]:
        doc[number-1].get_pixmap(matrix=pymupdf.Matrix(1.3, 1.3)).save(preview/f'paper_{number}.png')
    paper_pages = len(doc)
with pymupdf.open(ROOT/build['ai_disclosure']['path']) as doc:
    assert 'A-07' in ''.join(p.get_text() for p in doc)
    for k, page in enumerate(doc):
        page.get_pixmap(matrix=pymupdf.Matrix(1., 1.)).save(preview/f'ai_{k+1}.png')
    ai_pages = len(doc)
result = {'passed': True, 'official_sources_unchanged': len(verify_sources()),
          'old_evidence_sources_unchanged': len(verify_evidence()), 'new_numeric_recompute_matches': 1791,
          'paper_pages': paper_pages, 'ai_pages': ai_pages, 'pdf_new_values_checked': True,
          'source_output_hashes_checked': True, 'visual_review': 'pending',
          'paper_sha256': build['paper']['sha256'], 'ai_pdf_sha256': build['ai_disclosure']['sha256']}
write_json(ROOT/'records/extension_artifact_verification.json', result)
print(result)
