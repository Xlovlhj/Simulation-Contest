#!/usr/bin/env python3
# AI-assisted: OpenAI Codex, 2026-10-18. Exact model snapshot not supplied.
"""Package an explicit allowlist of anonymous reproducible support material."""
from __future__ import annotations
import hashlib
import json
from pathlib import Path
import sys
import zipfile

ROOT=Path(__file__).resolve().parents[1]
MAX_BYTES=20*1024*1024


def selected_files():
    files=[]
    for name in ('project.py','README.md','PROJECT_PLAN.md','CHANGELOG.md','requirements-sumo.txt','requirements-project.txt'):
        p=ROOT/name
        if p.is_file():files.append(p)
    for directory in ('scripts','src','tests','config','data/processed/baidu','data/processed/final',
                      'manuscript','outputs/validation','outputs/final_experiments','outputs/app'):
        for p in (ROOT/directory).rglob('*'):
            if not p.is_file() or p.is_symlink():continue
            if p.name.endswith('.local.json') or '__pycache__' in p.parts:continue
            if p.suffix.lower() not in {'.py','.html','.json','.csv','.md','.svg','.png','.jpg','.docx','.pdf','.txt'}:continue
            if directory=='outputs/final_experiments' and p.parent!=ROOT/directory:continue
            # Immutable runtime data are included below; omit legacy reports.
            if directory=='data/processed/baidu' and 'replay' in p.parts:continue
            files.append(p)
    for name in ('outputs/paper/ai_usage.pdf','outputs/paper/build_manifest.json',
                 'outputs/quality/reproduction_audit.json','outputs/quality/document_privacy_audit.json',
                 'outputs/quality/fairness_audit.json','outputs/quality/fairness_audit.csv',
                 'outputs/quality/fairness_audit.md','outputs/quality/app_smoke_audit.json',
                 'submission/SUBMISSION_CHECKLIST.md'):
        p=ROOT/name
        if not p.is_file():raise ValueError('Required support document missing: '+name)
        files.append(p)
    for prefix in ('collection_*','survey_*'):
        for directory in (ROOT/'data/raw/baidu').glob(prefix):
            for p in directory.rglob('*'):
                if p.is_file() and not p.is_symlink() and p.suffix in {'.json','.jsonl'}:files.append(p)
    return sorted(set(files))


def main():
    output=ROOT/'submission';output.mkdir(exist_ok=True)
    target=output/'B_support.zip'
    files=selected_files()
    if not (ROOT/'outputs/final_experiments/summary.json').exists() or not (ROOT/'outputs/validation/summary.json').exists():
        raise ValueError('Final experiments and validation must exist before packaging')
    values=[]
    private=ROOT/'config/baidu.local.json'
    if private.exists():
        config=json.loads(private.read_text(encoding='utf-8'))
        values=[v.encode() for v in config.values() if isinstance(v,str) and len(v)>16]
    manifest=[]
    for p in files:
        raw=p.read_bytes()
        if any(secret in raw for secret in values):raise ValueError('Private value detected in '+str(p.relative_to(ROOT)))
        name=p.relative_to(ROOT).as_posix()
        if any(x in p.parts for x in ('.venv','.cache','.git','.aws','.codex','.agents')):raise ValueError('Excluded directory in allowlist')
        manifest.append({'path':name,'bytes':len(raw),'sha256':hashlib.sha256(raw).hexdigest()})
    content={'purpose':'anonymous_support_material_for_team_review','file_count':len(files),'files':manifest,
        'exclusions':['credentials','virtual environment','cache','pledge/signature','video','large regenerable SUMO XML'],
        'reproduction':'python3 project.py reproduce; requires local SUMO environment described in README.md'}
    with zipfile.ZipFile(target,'w',compression=zipfile.ZIP_DEFLATED,compresslevel=9) as z:
        for p in files:z.write(p,p.relative_to(ROOT).as_posix())
        z.writestr('SUPPORT_MANIFEST.json',json.dumps(content,ensure_ascii=False,indent=2)+'\n')
    if target.stat().st_size>MAX_BYTES:raise ValueError('Support archive exceeds 20 MB; refine allowlist')
    with zipfile.ZipFile(target) as z:
        bad=z.testzip()
        if bad:raise ValueError('Archive CRC failed: '+bad)
    (output/'support_manifest.json').write_text(json.dumps({**content,'archive_bytes':target.stat().st_size,
        'archive_sha256':hashlib.sha256(target.read_bytes()).hexdigest()},ensure_ascii=False,indent=2)+'\n',encoding='utf-8')
    print(json.dumps({'archive':str(target.relative_to(ROOT)),'files':len(files),'bytes':target.stat().st_size,'credential_scan':'passed'},ensure_ascii=False))
    return 0


if __name__=='__main__':raise SystemExit(main())
