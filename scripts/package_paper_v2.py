#!/usr/bin/env python3
"""Package the V2 paper's public evidence, preserving the V1 archive.

AI-assisted: OpenAI Codex, 2026-10-18. Does not collect data or run SUMO.
"""
from __future__ import annotations
import argparse
import hashlib
import json
from pathlib import Path, PurePosixPath
import zipfile

ROOT = Path(__file__).resolve().parents[1]
ARCHIVE = ROOT / 'submission/B_support_v2.zip'
MANIFEST = ROOT / 'submission/support_manifest_v2.json'
BAD = {'.venv', '.cache', '.git', '.aws', '.codex', '.agents', '__pycache__'}


def digest(data):
    return hashlib.sha256(data).hexdigest()


def safe(name):
    p = PurePosixPath(name)
    if p.is_absolute() or '..' in p.parts or BAD.intersection(p.parts) or '\\' in name or ':' in name or str(p) != name or name.endswith('.local.json'):
        raise ValueError('不允许的成员路径：' + name)
    return name


def selection():
    paths = set()
    for relative in ('project_v2.py', 'REVISION_V2.md', 'PROJECT_PLAN.md', 'CHANGELOG.md',
                     'requirements-project.txt', 'requirements-sumo.txt',
                     'submission/README_CURRENT_VERSION.md', 'submission/SUBMISSION_CHECKLIST.md',
                     'submission/V2_AI_USAGE.md', 'manuscript/v2/SUPPORT_README.md',
                     'research/inverse_engine_geometry_review.md', 'outputs/validation/summary.json',
                     'outputs/validation/ETA_cc5eba1e5891e909/model.json'):
        paths.add(ROOT / relative)
    for relative, pattern in (('scripts', '*.py'), ('tests', '*.py'), ('config', '*.json'),
                              ('data/raw/baidu', '*.json*'), ('data/processed', '*'),
                              ('manuscript/v2', '*'), ('outputs/revision_v2', '*'),
                              ('research/parameter_audit', '*')):
        for p in (ROOT / relative).rglob(pattern):
            if p.is_file() and not p.is_symlink() and p.suffix in {'.py', '.json', '.jsonl', '.md', '.csv', '.html', '.svg', '.txt'}:
                if '.local.json' not in p.name and not BAD.intersection(p.parts):
                    paths.add(p)
    for folder in ('outputs/identification_v2/calibration', 'outputs/governance_v2'):
        p = ROOT / folder
        paths.update(f for f in p.iterdir() if f.is_file() and f.suffix in {'.json', '.jsonl', '.md', '.csv', '.svg'})
        # Per-run small records suffice for tabular auditing. Full XML stays in the workspace.
        for name in ('metrics.json', 'run_manifest.json', 'parameters.json', 'protocol.json', 'event.json', 'controller_log.json', 'source_queue_trace.json'):
            paths.update((p / 'runs').glob('*/' + name))
    paper = ROOT / 'outputs/paper_v2'
    paths.update(paper / n for n in ('paper.pdf', 'paper.docx', 'ai_usage.pdf', 'ai_usage.docx', 'build_manifest.json'))
    paths.update(f for f in (paper / 'figures').iterdir() if f.is_file())
    # Exact source traces used by Figure 4; other full-run XML is intentionally omitted.
    for strategy in ('A', 'B'):
        paths.add(ROOT / f'outputs/governance_v2/runs/headway_30_step_q10_S3_{strategy}/fcd.xml')
    for p in (ROOT / 'outputs/quality').glob('v2*'):
        if p.is_file() and p.suffix in {'.json', '.md'}:
            paths.add(p)
    paths.add(ROOT / 'outputs/quality/v2_reproduction/audit.json')
    for p in (ROOT / 'outputs/quality').glob('paper_v2*'):
        if p.is_file() and p.suffix in {'.json', '.md'} and 'package' not in p.name:
            paths.add(p)
    for p in paths:
        if not p.is_file() or p.is_symlink() or not p.resolve().is_relative_to(ROOT):
            raise ValueError('缺少或非法来源：' + str(p.relative_to(ROOT)))
        safe(str(p.relative_to(ROOT)))
    return sorted(paths)


def verify(archive=ARCHIVE, root=None):
    with zipfile.ZipFile(archive) as z:
        names = z.namelist()
        if len(names) != len(set(names)) or z.testzip() is not None:
            raise ValueError('ZIP成员重复或CRC失败')
        for name in names:
            safe(name)
        manifest = json.loads(z.read('SUPPORT_MANIFEST.json'))
        items = manifest['files']
        if len(items) != manifest['file_count'] or set(names) != {i['path'] for i in items} | {'SUPPORT_MANIFEST.json'}:
            raise ValueError('ZIP成员与清单不一致')
        for item in items:
            data = z.read(item['path'])
            if len(data) != item['bytes'] or digest(data) != item['sha256']:
                raise ValueError('ZIP摘要不一致：' + item['path'])
            if root is not None and (root / item['path']).read_bytes() != data:
                raise ValueError('ZIP与当前来源不一致：' + item['path'])
        if archive.stat().st_size > 20_000_000:
            raise ValueError('ZIP超过20MB')
    return {'status': 'passed', 'files': len(items), 'archive_bytes': archive.stat().st_size, 'archive_sha256': digest(archive.read_bytes())}


def verify_external():
    external = json.loads(MANIFEST.read_text(encoding='utf-8'))
    with zipfile.ZipFile(ARCHIVE) as z:
        internal = json.loads(z.read('SUPPORT_MANIFEST.json'))
    if any(external.get(k) != v for k, v in internal.items()):
        raise ValueError('包外逐文件清单与包内清单不同')
    if external.get('archive_bytes') != ARCHIVE.stat().st_size or external.get('archive_sha256') != digest(ARCHIVE.read_bytes()):
        raise ValueError('包外整包摘要不同')


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--check', action='store_true')
    args = parser.parse_args()
    if args.check:
        # In an extracted package verify the exact selected members, no original environment required.
        if (ROOT / 'SUPPORT_MANIFEST.json').exists():
            manifest = json.loads((ROOT / 'SUPPORT_MANIFEST.json').read_text())
            for item in manifest['files']:
                p = ROOT / safe(item['path'])
                if not p.is_file() or p.is_symlink() or not p.resolve().is_relative_to(ROOT) or p.stat().st_size != item['bytes'] or digest(p.read_bytes()) != item['sha256']:
                    raise ValueError('解压文件核验失败：' + item['path'])
            print(json.dumps({'status': 'passed', 'files': len(manifest['files'])}))
        else:
            result = verify(root=ROOT)
            verify_external()
            print(json.dumps(result))
        return
    paths = selection()
    # Read local secrets only for exact matching; never emit their values.
    secret_path = ROOT / 'config/baidu.local.json'
    secrets = []
    if secret_path.exists():
        def values(obj):
            if isinstance(obj, dict):
                for k, v in obj.items():
                    if str(k).lower() in {'ak', 'sk', 'secret_key', 'access_key'} and isinstance(v, str) and len(v) >= 12:
                        secrets.append(v.encode())
                    elif isinstance(v, (dict, list)):
                        values(v)
            elif isinstance(obj, list):
                for v in obj:
                    values(v)
        values(json.loads(secret_path.read_text()))
    files = []
    for p in paths:
        raw = p.read_bytes()
        if any(s in raw for s in secrets):
            raise ValueError('来源检出私密值，拒绝打包：' + str(p.relative_to(ROOT)))
        files.append({'path': str(p.relative_to(ROOT)), 'bytes': len(raw), 'sha256': digest(raw)})
    manifest = {'version': 'V2', 'file_count': len(files), 'files': files,
                'limitations': '不含环境、全部大型XML、55.9MB环评PDF及视频；保留图4必要FCD。复算须准备SUMO1.28.0，使用独立输出目录。V1视频未更新。'}
    with zipfile.ZipFile(ARCHIVE, 'w', compression=zipfile.ZIP_DEFLATED, compresslevel=9) as z:
        for p in paths:
            z.write(p, str(p.relative_to(ROOT)))
        z.writestr('SUPPORT_MANIFEST.json', json.dumps(manifest, ensure_ascii=False, indent=2) + '\n')
    result = verify(root=ROOT)
    # The summary's numeric `files` must not overwrite the manifest's file list.
    MANIFEST.write_text(json.dumps({**manifest, **{k: v for k, v in result.items() if k != 'files'}}, ensure_ascii=False, indent=2) + '\n', encoding='utf-8')
    verify_external()
    (ROOT / 'outputs/quality/paper_v2_package_check.json').write_text(json.dumps(result, indent=2) + '\n')
    print(json.dumps(result, ensure_ascii=False))


if __name__ == '__main__':
    main()
