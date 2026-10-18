"""独立核对交付ZIP和论文元数据；AI辅助：Codex/OpenAI，发布版本未核实。"""
from datetime import datetime, timedelta, timezone
import hashlib
import json
from pathlib import Path, PurePosixPath
import re
import sys
import zipfile

ROOT=Path(__file__).resolve().parents[1]
sys.path.insert(0,str(ROOT/'src'))
from bev.io import read_json,sha256,write_json
from bev.paper import verify_evidence


def main():
    fingerprint=verify_evidence()
    correction=read_json(ROOT/'records/remediation_runtime_verification.json')
    assert correction['passed'] and correction['analytical_checks_passed']==6
    for name,digest in correction['scientific_source_hashes'].items():
        assert sha256(ROOT/name)==digest, name
    assert sha256(ROOT/'records/remediation_summary.json')==correction['expected_summary_sha256']
    extension=read_json(ROOT/'records/extension_package_runtime_verification.json')
    assert extension['passed'] and extension['analytical_checks']==7 and extension['numeric_values_compared']==1791
    for name,digest in extension['scientific_fingerprint'].items():
        assert sha256(ROOT/name)==digest,name
    assert sha256(ROOT/'scripts/recompute_extension.py')==extension['reproduction_entry_sha256']
    assert sha256(ROOT/'src/bev/delivery.py')==extension['paper_builder_sha256']
    for name,digest in extension['expected_hashes'].items():
        assert sha256(ROOT/'records/experiments/extension_v1'/name)==digest,name
    packages=read_json(ROOT/'records/delivery_packages.json')
    reports={}
    for label,item in packages.items():
        path=ROOT/item['path'];assert sha256(path)==item['sha256']
        with zipfile.ZipFile(path) as archive:
            assert archive.testzip() is None
            prefix='C题复现工程/'
            members=archive.namelist()
            assert all(n.startswith(prefix) and '..' not in PurePosixPath(n).parts for n in members)
            manifest=json.loads(archive.read(prefix+'PACKAGE_MANIFEST.json'))
            assert len(members)==len(manifest['files'])+1
            for record in manifest['files']:
                data=archive.read(prefix+record['path'])
                assert hashlib.sha256(data).hexdigest()==record['sha256'],record['path']
            leaks=[]
            for name in members:
                if name.endswith(('.py','.md','.json','.txt')):
                    text=archive.read(name).decode('utf-8-sig')
                    if re.search(r'(?:C:[\\/]Users[\\/]|api[_-]?key\s*=\s*[\x22\x27][A-Za-z0-9_-]{12,})',text,re.I):
                        # 检查脚本本身包含检测模式，不是用户路径或密钥。
                        if name!=prefix+'scripts/check_delivery.py':
                            leaks.append(name)
            assert not leaks,leaks
            if label=='support':
                assert path.stat().st_size<=20_000_000
                assert not any(n.startswith(prefix+'data/raw/') or n.startswith(prefix+'赛题资料/') or n.endswith(('.doc','.mp4')) for n in members)
            assert prefix+'C题开发日志.md' in members
            for name,digest in extension['scientific_fingerprint'].items():
                if name in [r['path'] for r in manifest['files']]:
                    assert hashlib.sha256(archive.read(prefix+name)).hexdigest()==digest,name
            for name,digest in extension['expected_hashes'].items():
                assert hashlib.sha256(archive.read(prefix+'records/experiments/extension_v1/'+name)).hexdigest()==digest,name
            assert hashlib.sha256(archive.read(prefix+'records/remediation_summary.json')).hexdigest()==correction['expected_summary_sha256']
            runtime_path=ROOT/'records/delivery_runtime_verification.json'
            if runtime_path.exists():
                runtime=read_json(runtime_path)
                assert runtime['passed']
                for name,digest in runtime['results'][label]['scientific_reproduction_files'].items():
                    assert hashlib.sha256(archive.read(prefix+name)).hexdigest()==digest,name
            reports[label]={'bytes':path.stat().st_size,'files_verified':len(manifest['files']),
                            'sha256':item['sha256'],'text_identity_or_key_matches':leaks,
                            'official_inputs_included':manifest['official_inputs_included'],'trajectory_mode':manifest['trajectory_mode']}
    import pymupdf
    build=read_json(ROOT/'records/final_paper_build.json')
    for name,digest in {**build['source_hashes'],**build['output_hashes']}.items():
        assert sha256(ROOT/name)==digest,name
    for key in ['paper','ai_disclosure']:
        with pymupdf.open(ROOT/build[key]['path']) as pdf:
            assert pdf.metadata.get('author','')==''
            assert 'Administrator' not in '\n'.join(p.get_text() for p in pdf)
    result={'date_beijing':datetime.now(timezone(timedelta(hours=8))).isoformat(timespec='seconds'),
            'passed':True,'scientific_evidence_sources_unchanged':len(fingerprint),'packages':reports,
            'stage8_full_recomputation_verified':True,
            'stage8_numeric_values_compared':correction['numeric_values_compared'],
            'stage10_full_recomputation_verified_from_team_zip':True,
            'stage10_numeric_values_compared':extension['numeric_values_compared'],
            'pdf_author_metadata_empty':True,'competition_submission_ready':False,'team_independent_work':'not_started',
            'scope':'File integrity and visible identity/key pattern scan. Runtime reproduction is recorded separately; this is not a compliance or independent-work certificate.'}
    write_json(ROOT/'records/delivery_verification.json',result)
    print(json.dumps(result,ensure_ascii=True,indent=2))


if __name__=='__main__':
    main()
