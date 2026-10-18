"""从外部输入隔离完整重算阶段十，不复用阶段十数值结果。"""
from datetime import datetime
import json
import math
import os
from pathlib import Path
import shutil
import subprocess
import sys
import tempfile

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT/'src'))
from bev.io import read_json, write_json, sha256


def compare(expected, actual, path='', values=None):
    values = {} if values is None else values
    if isinstance(expected, dict):
        for key, value in expected.items():
            if key in ['runtime_s', 'time_utc', 'source_hashes']:
                continue
            compare(value, actual[key], path+'/'+key, values)
    elif isinstance(expected, list):
        assert len(expected) == len(actual), path
        for k, v in enumerate(expected):
            compare(v, actual[k], path+'/'+str(k), values)
    elif isinstance(expected, (float, int)) and not isinstance(expected, bool):
        assert math.isclose(expected, actual, rel_tol=1e-6, abs_tol=1e-6), (path, expected, actual)
        values[path] = actual
    else:
        assert expected == actual, (path, expected, actual)
    return values


def main():
    parent = ROOT/'records/extension_checks'
    parent.mkdir(exist_ok=True)
    target = Path(tempfile.mkdtemp(prefix='full_', dir=parent))
    for folder in ['src', 'configs', 'data', '赛题资料', 'checks']:
        shutil.copytree(ROOT/folder, target/folder, ignore=shutil.ignore_patterns('__pycache__'))
    for folder in ['records/literature', 'records/experiments/remediation_v1', 'results/tables', 'results/figures']:
        shutil.copytree(ROOT/folder, target/folder)
    for source in (ROOT/'records').glob('*.json'):
        shutil.copy2(source, target/'records'/source.name)
    exp = 'records/experiments/extension_v1'
    shutil.copytree(ROOT/exp/'inputs', target/exp/'inputs')
    environment = dict(os.environ, PYTHONPATH='src')
    with (target/'runtime.log').open('w', encoding='utf-8') as output:
        for args in [['checks/extension_checks.py'], ['-m', 'bev.extension', 'all']]:
            result = subprocess.run([sys.executable, '-X', 'utf8', '-u', *args], cwd=target,
                                    env=environment, stdout=output, stderr=subprocess.STDOUT)
            if result.returncode:
                raise RuntimeError(f'完整重算未通过，记录在{target}')
    values = {}
    for name in ['road_results.json', 'temperature_results.json', 'sensor_results.json', 'voltage_results.json']:
        compare(read_json(ROOT/exp/name), read_json(target/exp/name), name, values)
    frozen = read_json(ROOT/exp/'protocol_freeze.json')
    for name, digest in frozen['fingerprint'].items():
        assert sha256(ROOT/name) == digest, name
    report = {'passed': True, 'time_local': datetime.now().isoformat(), 'analytical_checks': 7,
              'full_recompute': True, 'numeric_values_compared': len(values),
              'environment': 'Same installed Python environment; fresh isolated directory, no new virtual environment.',
              'work_directory': target.relative_to(ROOT).as_posix(),
              'fingerprint': frozen['fingerprint'],
              'expected_hashes': {name: sha256(ROOT/exp/name) for name in ['road_results.json', 'temperature_results.json', 'sensor_results.json', 'voltage_results.json']}}
    write_json(ROOT/'records/extension_runtime_verification.json', report)
    print(json.dumps({k: v for k, v in report.items() if k not in ['fingerprint', 'expected_hashes']}, ensure_ascii=False, indent=2))


if __name__ == '__main__':
    main()
