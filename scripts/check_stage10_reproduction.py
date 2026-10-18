"""新解压同学包，从外部原输入完整重算阶段十。"""
from datetime import datetime
from pathlib import Path
import subprocess
import sys
import tempfile
import zipfile

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT/'src'))
from bev.io import read_json, write_json, sha256

item = read_json(ROOT/'records/delivery_packages.json')['team']
archive = ROOT/item['path']
assert sha256(archive) == item['sha256']
parent = ROOT/'records/package_checks'
parent.mkdir(exist_ok=True)
destination = Path(tempfile.mkdtemp(prefix='stage10_', dir=parent))
with zipfile.ZipFile(archive) as zipped:
    zipped.extractall(destination)
work = destination/'C题复现工程'
with (destination/'runtime.log').open('w', encoding='utf-8') as output:
    for args in [['reproduce.py', 'verify'], ['reproduce.py', 'prepare'], ['scripts/recompute_extension.py']]:
        result = subprocess.run([sys.executable, '-X', 'utf8', '-u', *args], cwd=work, stdout=output, stderr=subprocess.STDOUT)
        assert result.returncode == 0, (args, destination)
report = read_json(work/'records/extension_runtime_verification.json')
assert report['passed'] and report['numeric_values_compared'] == 1791
result = {'passed': True, 'time_local': datetime.now().isoformat(),
          'package_sha256_at_runtime': item['sha256'], 'analytical_checks': 7,
          'numeric_values_compared': report['numeric_values_compared'], 'full_recompute': True,
          'environment': 'Same installed Python environment; fresh team ZIP extraction and isolated full recompute.',
          'work_directory': destination.relative_to(ROOT).as_posix(),
          'scientific_fingerprint': report['fingerprint'], 'expected_hashes': report['expected_hashes'],
          'reproduction_entry_sha256': sha256(work/'scripts/recompute_extension.py'),
          'paper_builder_sha256': sha256(work/'src/bev/delivery.py')}
write_json(ROOT/'records/extension_package_runtime_verification.json', result)
print({k: v for k, v in result.items() if k not in ['scientific_fingerprint', 'expected_hashes']})
