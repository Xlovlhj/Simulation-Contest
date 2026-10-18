"""整理最终技术稿与待提交目录，不签署或上传。AI辅助：Codex/OpenAI，发布版本未核实。"""
from __future__ import annotations

import argparse
from datetime import datetime, timedelta, timezone
import json
from pathlib import Path
import re
import shutil
import sys

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / 'src'))
from bev.io import read_json, write_json, sha256, verify_sources
from bev.paper import verify_evidence

DELIVERY = ROOT / '交付材料'
FINAL = DELIVERY / '论文/C题论文_最终稿.pdf'
PENDING = DELIVERY / '待提交'


def materialize():
    build = read_json(ROOT / 'records/final_paper_build.json')
    for name, digest in {**build['source_hashes'], **build['output_hashes']}.items():
        assert sha256(ROOT / name) == digest, name
    source = ROOT / build['paper']['path']
    shutil.copy2(source, FINAL)
    shutil.copy2(source.with_suffix('.html'), FINAL.with_suffix('.html'))
    PENDING.mkdir(parents=True, exist_ok=True)
    shutil.copy2(FINAL, PENDING / FINAL.name)
    (PENDING / '提交前清单.md').write_text(
        '# 提交前清单\n\n'
        '本目录已整理最终技术稿与支撑包；队伍号留白，人工核验尚未完成。\n\n'
        '1. 完成真实独立工作、人工修改与核验，保存记录，并更新论文声明及AI详情。\n'
        '2. 将论文改名为 C＋真实队伍号＋.pdf，支撑包改名为 C＋真实队伍号＋fj.zip。\n'
        '3. 单独填写和签署官方承诺书，以 C＋真实队伍号＋cns.pdf/jpg/png 上传。\n'
        '4. 论文、承诺书、支撑材料在北京时间2026-10-20 20:00前分别提交。\n'
        '5. 上传后点击提交，确认显示“已提交”。未执行代签、上传或对外发送。\n\n'
        'AI工具使用详情仍为工作稿，不可直接当作完整披露提交。\n'
        '正式视频按用户要求暂缓，另于2026-10-21 20:00前提交；15秒样片不是正式视频。\n'
        '给同学复现请发上一级目录的 C题_同学复现包.zip。\n'
        'SHA256SUMS.txt用于核对传输一致性；文件改动后需重新生成论文和支撑包。\n',
        encoding='utf-8')
    unsigned = DELIVERY / '待填写'
    unsigned.mkdir(exist_ok=True)
    template = next((ROOT / '赛题资料').rglob('*承诺书.doc'))
    shutil.copy2(template, unsigned / template.name)
    result = {'date_beijing': datetime.now(timezone(timedelta(hours=8))).isoformat(timespec='seconds'),
              'paper_finalized_with_existing_evidence': True, 'new_scientific_experiments': False,
              'paper_path': FINAL.relative_to(ROOT).as_posix(), 'paper_sha256': sha256(FINAL),
              'paper_pages': build['paper']['pages'], 'paper_bytes': FINAL.stat().st_size,
              'team_number': None, 'manual_review': 'incomplete_user_confirmed',
              'ai_disclosure': 'truthful_working_document_incomplete',
              'signed_commitment': False, 'video': 'deferred_by_user', 'uploaded': False,
              'competition_submission_ready': False,
              'scope': 'Final technical content and submission preparation; missing target-car measurements are explicit limitations.'}
    write_json(ROOT / 'records/final_submission.json', result)
    return result


def audit():
    import pymupdf
    from docx import Document
    evidence = verify_evidence()
    originals = verify_sources()
    build = read_json(ROOT / 'records/final_paper_build.json')
    for name, digest in {**build['source_hashes'], **build['output_hashes']}.items():
        assert sha256(ROOT / name) == digest, name
    assert sha256(FINAL) == build['paper']['sha256']
    md = (ROOT / 'docs/论文/C题论文_技术定稿.md').read_text(encoding='utf-8')
    abstract, body = md.split('<!-- PAPER_ABSTRACT_END -->')
    assert not re.search(r'\$|!\[|^\|', abstract, re.M)
    assert all(w in abstract for w in ['14.0542', '6.03', '8.30', '11.83', '9.85', '0.70', '59/60', '0/60'])
    assert all(w in body for w in ['0.225357', '104/200', '回顾', '未优于', '人工核验尚未完成'])
    pre_refs, refs = body.split('### 8.1 参考文献')
    refs, declaration = refs.split('### 8.2 AI工具使用声明')
    reference_ids = [int(x) for x in re.findall(r'^\[(\d+)\]', refs, re.M)]
    assert reference_ids == list(range(1, 10))
    order = []
    for match in re.finditer(r'\[(\d+)(?:[—–-](\d+))?\]', pre_refs):
        start, end = int(match[1]), int(match[2] or match[1])
        for value in range(start, end + 1):
            if value not in order:
                order.append(value)
    assert order == reference_ids, order
    numbered_tables = [int(x) for x in re.findall(r'^\*\*表(\d+) ', md, re.M)]
    assert numbered_tables == list(range(1, 16)), numbered_tables
    equations = [int(x) for x in re.findall(r'\\tag\{(\d+)\}', md)]
    assert equations == list(range(1, 13))
    with pymupdf.open(FINAL) as pdf:
        texts = [p.get_text() for p in pdf]
        combined = '\n'.join(texts)
        assert len(pdf) == build['paper']['pages']
        assert '关键词' in texts[0] and '目录' not in texts[0] and '目录' in texts[1]
        assert '1 问题重述' in texts[2]
        assert pdf.metadata.get('author', '') == '' and not pdf.embfile_count()
        assert not re.search(r'Administrator|C:\\Users|@@|【待补充】|CSF26\d+', combined)
        assert all(len(t.strip()) > 100 for t in texts)
        assert all(not list(p.annots() or []) for p in pdf)
        appendix_start = next(i + 1 for i, t in enumerate(texts) if i > 1 and '附录A 支撑材料文件清单与复现入口' in t)
        page_summary = []
        for i, page in enumerate(pdf):
            footer = page.get_text(clip=pymupdf.Rect(0, page.rect.height-55, page.rect.width, page.rect.height)).strip()
            assert footer == str(i + 1)
            assert page.get_text(clip=pymupdf.Rect(0, 0, page.rect.width, 35)).strip() == ''
            for rect in page.get_text('blocks'):
                assert 0 <= rect[0] <= rect[2] <= page.rect.width + .1
                assert 0 <= rect[1] <= rect[3] <= page.rect.height + .1
            page_summary.append({'page': i+1, 'text_characters': len(texts[i]), 'images': len(page.get_images()), 'footer': footer})
        dest = ROOT / 'results/final_checks'
        dest.mkdir(parents=True, exist_ok=True)
        for number in [1, 2, 5, 13, 14, 18, 19, 20]:
            if number <= len(pdf):
                pdf[number-1].get_pixmap(matrix=pymupdf.Matrix(1.3, 1.3)).save(dest / f'paper_page_{number:02d}.png')
    ai_path = ROOT / build['ai_disclosure']['path']
    with pymupdf.open(ai_path) as pdf:
        assert pdf.metadata.get('author', '') == ''
        assert '人工核验尚未完成' in '\n'.join(p.get_text() for p in pdf)
        for i, page in enumerate(pdf):
            page.get_pixmap(matrix=pymupdf.Matrix(1.0, 1.0)).save(dest / f'ai_page_{i+1:02d}.png')
    props = Document(DELIVERY / 'AI工具使用详情.docx').core_properties
    assert not props.author and not props.last_modified_by
    result = {'date_beijing': datetime.now(timezone(timedelta(hours=8))).isoformat(timespec='seconds'),
              'paper_sha256': sha256(FINAL), 'paper_pages': len(texts), 'paper_bytes': FINAL.stat().st_size,
              'paper_under_20_MB': FINAL.stat().st_size <= 20_000_000,
              'official_inputs_unchanged': len(originals), 'original_scientific_sources_unchanged': len(evidence),
              'current_build_sources_checked': len(build['source_hashes']), 'tables': 15, 'numbered_equations': 12,
              'reference_first_appearance_order': order, 'abstract_no_math_figures_or_tables': True,
              'appendix_start_page': appendix_start, 'appendix_pages': len(texts)-appendix_start+1,
              'pdf_metadata_and_visible_identity_pattern_check': True, 'continuous_centered_pagination': True,
              'pdf_first_page_abstract_second_page_contents': True,
              'word_author_properties_empty': True, 'page_summary': page_summary,
              'visual_review': 'pending', 'passed_machine_checks': True,
              'scope': 'PDF structure, identity patterns, current source/result consistency and file integrity; not team manual review or real-vehicle validation.'}
    write_json(ROOT / 'records/final_submission_audit.json', result)
    return result


def assemble():
    packages = read_json(ROOT / 'records/delivery_packages.json')
    for item in packages.values():
        assert sha256(ROOT / item['path']) == item['sha256']
    source = ROOT / packages['support']['path']
    target = PENDING / source.name
    shutil.copy2(source, target)
    assert source.stat().st_size <= 20_000_000
    assert sha256(FINAL) == sha256(PENDING / FINAL.name)
    paths = [PENDING / FINAL.name, target, PENDING / '提交前清单.md']
    (PENDING / 'SHA256SUMS.txt').write_text(''.join(f'{sha256(p)}  {p.name}\n' for p in paths), encoding='utf-8')
    result = {'files': [{'path': p.relative_to(ROOT).as_posix(), 'bytes': p.stat().st_size, 'sha256': sha256(p)} for p in paths],
              'paper_and_support_ready_for_manual_review': True, 'competition_submission_ready': False,
              'pending': ['actual team independent work and human review', 'complete truthful AI disclosure',
                          'team-number file naming', 'separately signed commitment', 'video deferred', 'user upload']}
    write_json(ROOT / 'records/submission_directory.json', result)
    return result


if __name__ == '__main__':
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('action', choices=['materialize', 'audit', 'assemble'])
    args = parser.parse_args()
    print(json.dumps(globals()[args.action](), ensure_ascii=True, indent=2))
