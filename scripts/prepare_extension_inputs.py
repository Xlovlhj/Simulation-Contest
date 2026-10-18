"""复制已取得的外部输入，保留外部对象身份与许可。"""
from pathlib import Path
import shutil
import sys

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT/'src'))
from bev.io import sha256, write_json, read_json

cache = ROOT/'records/external_research_20261018'
dest = ROOT/'records/experiments/extension_v1/inputs'
dest.mkdir(parents=True, exist_ok=True)
if not cache.exists():
    provenance = read_json(dest/'provenance.json')
    assert sha256(dest/'epa_2026_electric_roadload.csv') == provenance['EPA']['extract_sha256']
    assert sha256(dest/'oxford_ExampleDC_C1.mat') == provenance['Oxford']['mat_sha256']
    print('Packaged external inputs verified; original research cache is optional.')
    raise SystemExit(0)
for name in ['epa_2026_electric_roadload.csv', 'oxford_ExampleDC_C1.mat', 'oxford_readme.txt']:
    target = dest/name
    if target.exists() and sha256(target) != sha256(cache/name):
        raise ValueError('不覆盖不同输入')
    shutil.copyfile(cache/name, target)
write_json(dest/'provenance.json', {
    'EPA': {'source': 'https://www.epa.gov/system/files/documents/2026-01/26-testcar-2026-01-21.xlsx',
            'raw_xlsx_sha256': sha256(cache/'epa_2026_testcar.xlsx'),
            'extract': '458 Test Fuel Type Description=Electricity rows; identity, test weight and Target A/B/C columns; no statistical sampling.',
            'extract_sha256': sha256(dest/'epa_2026_electric_roadload.csv'),
            'publisher': 'US Environmental Protection Agency', 'retrieved': '2026-10-18'},
    'Oxford': {'doi': '10.5287/bodleian:KO2kdmYGg', 'publisher': 'University of Oxford',
               'authors': 'Christoph R. Birkl; David A. Howey',
               'citation': 'Oxford Battery Degradation Dataset 1 (2017); Birkl, Diagnosis and Prognosis of Degradation in Lithium-Ion Batteries, PhD thesis, Oxford, 2017.',
               'license': 'ODC-ODbL 1.0; contents DbCL 1.0; original README retained',
               'mat_sha256': sha256(dest/'oxford_ExampleDC_C1.mat'),
               'object': 'Kokam SLPB533459H4 740mAh cell, 40C chamber; not vehicle455 or its pack'}})
print(dest)
