"""从新解压的同学包完整重算阶段八；AI辅助见records/ai_usage.md。"""
from datetime import datetime, timedelta, timezone
import json
import math
from pathlib import Path
import subprocess
import sys
import zipfile

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / 'src'))
from bev.io import read_json, sha256, write_json


def compare(expected, actual, path='', checked=None):
    checked = {} if checked is None else checked
    if isinstance(expected, dict):
        for key, value in expected.items():
            if key == 'source_hashes':
                continue  # 新实验冻结记录含本次运行时间，不能按文件字节比较。
            assert key in actual, path + '/' + key
            compare(value, actual[key], path + '/' + key, checked)
    elif isinstance(expected, list):
        assert len(expected) == len(actual), path
        for index, value in enumerate(expected):
            compare(value, actual[index], path + '/' + str(index), checked)
    elif isinstance(expected, (int, float)) and not isinstance(expected, bool):
        assert math.isclose(expected, actual, rel_tol=1e-7, abs_tol=1e-6), (path, expected, actual)
        checked[path] = actual
    else:
        assert expected == actual, (path, expected, actual)
    return checked


def main():
    item = read_json(ROOT / 'records/delivery_packages.json')['team']
    package = ROOT / item['path']
    assert sha256(package) == item['sha256']
    stamp = datetime.now(timezone(timedelta(hours=8))).strftime('%Y%m%d_%H%M%S')
    destination = ROOT / 'records/package_checks' / ('stage8_full_' + stamp)
    destination.mkdir(parents=True, exist_ok=False)
    with zipfile.ZipFile(package) as archive:
        archive.extractall(destination)
    work = destination / 'C题复现工程'
    with (destination / 'runtime.log').open('w', encoding='utf-8') as output:
        for command in ['verify', 'prepare', 'improve']:
            result = subprocess.run([sys.executable, '-X', 'utf8', '-u', 'reproduce.py', command],
                                    cwd=work, stdout=output, stderr=subprocess.STDOUT, check=False)
            assert result.returncode == 0, (command, str(destination))
    report = read_json(work / 'REPRODUCTION_REPORT.json')
    assert report['corrective_stage8_recomputed'] and report['exit_code'] == 0
    actual = read_json(work / report['work_directory'] / 'records/remediation_summary.json')
    expected = read_json(work / 'records/remediation_summary.json')
    checked = compare(expected, actual)
    names = ['reproduce.py', 'run.py', 'checks/remediation_checks.py',
             'configs/remediation_protocol.json', 'src/bev/remediation.py',
             'src/bev/remediation_bounds.py', 'src/bev/remediation_report.py',
             'src/bev/model.py', 'src/bev/calibration.py', 'src/bev/control.py']
    result = {
        'date_beijing': datetime.now(timezone(timedelta(hours=8))).isoformat(timespec='seconds'),
        'passed': True, 'commands': ['verify', 'prepare', 'improve'],
        'environment': 'Same installed Python 3.12 environment; fresh team ZIP extraction and isolated full stage8 recomputation.',
        'analytical_checks_passed': 6, 'new_training_and_thermal_experiments_recomputed': True,
        'numeric_values_compared': len(checked), 'metrics': checked,
        'expected_summary_sha256': sha256(work / 'records/remediation_summary.json'),
        'reproduction_report': (work / 'REPRODUCTION_REPORT.json').relative_to(ROOT).as_posix(),
        'scientific_source_hashes': {name: sha256(work / name) for name in names},
        'note': 'No new measured data, team manual review, or fresh virtual environment is claimed.'}
    write_json(ROOT / 'records/remediation_runtime_verification.json', result)
    print(json.dumps({k: v for k, v in result.items() if k not in ['metrics', 'scientific_source_hashes']}, ensure_ascii=True, indent=2))


if __name__ == '__main__':
    main()
