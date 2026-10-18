#!/usr/bin/env python3
"""Check the actual V2 DOCX/PDF, hashes and original-file preservation.

AI-assisted: OpenAI Codex, 2026-10-18. This checks technical consistency;
it does not stand in for the team's scientific review or official submission.
No private values are written to output. All output remains in the workspace.
"""
from __future__ import annotations

import csv
from datetime import datetime, timezone
import hashlib
import json
from pathlib import Path
import re
import subprocess
import sys
import xml.etree.ElementTree as ET
import zipfile

ROOT = Path(__file__).resolve().parents[1]
OUT = ROOT / "outputs/paper_v2"
REPORT = ROOT / "outputs/quality/paper_v2_technical_check.json"
W = "{http://schemas.openxmlformats.org/wordprocessingml/2006/main}"
DC = "{http://purl.org/dc/elements/1.1/}"
CP = "{http://schemas.openxmlformats.org/package/2006/metadata/core-properties}"
XHTML = "{http://www.w3.org/1999/xhtml}"
CHECKS = []
LIMITATIONS = [
    "PDF使用本机替代字体；DOCX中指定字体名的检查不证明PDF已嵌入规定的宋体/黑体。正式提交需规定字体环境重导出。",
    "匿名检查覆盖作者/最后修改者等元数据、页眉和已知本地用户名路径；无法凭自动检查排除未知个人身份线索。",
    "本检查验证给定数值、格式、文件一致性与实际私密值泄露；不替代科学审稿或队员独立核心工作。",
]


def sha(path):
    return hashlib.sha256(Path(path).read_bytes()).hexdigest()


def rel(path):
    return str(Path(path).relative_to(ROOT))


def check(name, passed, **details):
    CHECKS.append({"name": name, "passed": bool(passed), **details})


def run(command):
    result = subprocess.run(command, capture_output=True, text=True, check=True)
    return result.stdout


def norm(text):
    return re.sub(r"\s+", "", text)


def attr(element, key, default=None):
    return default if element is None else element.get(W + key, default)


def check_hashes(manifest):
    for name, expected in manifest["source_sha256"].items():
        path = (ROOT / name).resolve()
        exists = path.is_relative_to(ROOT) and path.is_file()
        check("build_source_hash", exists and sha(path) == expected, path=name)
    for name, entry in manifest["artifacts"].items():
        path = (OUT / name).resolve()
        exists = path.is_relative_to(OUT) and path.is_file()
        check("build_artifact_hash_and_size", exists and sha(path) == entry["sha256"] and path.stat().st_size == entry["bytes"], path=name)
    fm = json.loads((OUT / "figures/manifest.json").read_text(encoding="utf-8"))
    for item in fm["figures"]:
        check("figure_image_hash", sha(ROOT / item["figure"]) == item["figure_sha256"], path=item["figure"])
        for name, expected in item["source_sha256"].items():
            check("figure_source_hash", sha(ROOT / name) == expected, path=name)


def read_pdf(path):
    text = run(["pdftotext", "-layout", str(path), "-"])
    pages = text.rstrip("\f\n").split("\f")
    info = run(["pdfinfo", str(path)])
    info_fields = dict(re.findall(r"^([^:\n]+):\s*(.*)$", info, re.M))
    check("pdf_size_below_20mb", path.stat().st_size < 20_000_000, path=rel(path), bytes=path.stat().st_size)
    check("pdf_author_metadata_blank", not info_fields.get("Author", "").strip(), path=rel(path))
    check("pdf_info_page_count", int(info_fields.get("Pages", -1)) == len(pages), path=rel(path), pages=len(pages))
    check("pdf_no_local_user_path", "/home/xqw" not in text and "/home/xqw" not in info, path=rel(path))
    # Verify actual footer coordinates, not merely the existence of PAGE in DOCX.
    bbox = ET.fromstring(run(["pdftotext", "-bbox", str(path), "-"]))
    bbox_pages = list(bbox.iter(XHTML + "page"))
    check("pdf_bbox_page_count", len(bbox_pages) == len(pages), path=rel(path))
    for index, (page, page_text) in enumerate(zip(bbox_pages, pages), 1):
        width, height = float(page.get("width")), float(page.get("height"))
        candidates = [w for w in page.iter(XHTML + "word") if w.text == str(index) and float(w.get("yMin")) > height * .85]
        centered = [w for w in candidates if abs((float(w.get("xMin")) + float(w.get("xMax"))) / 2 - width / 2) < width * .08]
        last_line = [line.strip() for line in page_text.splitlines() if line.strip()][-1]
        check("pdf_continuous_centered_footer", len(centered) == 1 and last_line == str(index), path=rel(path), page=index)
    return {"text": text, "pages": pages, "info": info}


def docx_checks(path, is_main=True):
    with zipfile.ZipFile(path) as z:
        members = {name: z.read(name) for name in z.namelist()}
    core = ET.fromstring(members["docProps/core.xml"])
    creator = core.findtext(DC + "creator") or ""
    last = core.findtext(CP + "lastModifiedBy") or ""
    check("docx_anonymous_author_metadata", creator.strip() == "" and last.strip() == "", path=rel(path))
    main = ET.fromstring(members["word/document.xml"])
    headers = [name for name in members if re.fullmatch(r"word/header\d+\.xml", name)]
    header_content = []
    for name in headers:
        xml = ET.fromstring(members[name])
        header_content.extend(t.text for t in xml.iter(W + "t") if t.text and t.text.strip())
        header_content.extend(["drawing"] if list(xml.iter(W + "drawing")) else [])
    header_refs = list(main.iter(W + "headerReference"))
    check("docx_no_header", not header_content and not header_refs, path=rel(path))
    footers = [name for name in members if re.fullmatch(r"word/footer\d+\.xml", name)]
    page_fields = 0
    centered_fields = 0
    for name in footers:
        xml = ET.fromstring(members[name])
        for p in xml.iter(W + "p"):
            simple = [n.get(W + "instr", "").strip() for n in p.iter(W + "fldSimple")]
            complex_fields = [(n.text or "").strip() for n in p.iter(W + "instrText")]
            count = sum(value == "PAGE" for value in simple + complex_fields)
            page_fields += count
            if attr(p.find(W + "pPr/" + W + "jc"), "val") == "center":
                centered_fields += count
    check("docx_centered_page_field", page_fields >= 1 and centered_fields == page_fields, path=rel(path))
    styles = ET.fromstring(members["word/styles.xml"])
    by_id = {s.get(W + "styleId"): s for s in styles.findall(W + "style")}
    normal = by_id["Normal"]
    normal_font = normal.find(W + "rPr/" + W + "rFonts")
    normal_size = attr(normal.find(W + "rPr/" + W + "sz"), "val")
    normal_line = normal.find(W + "pPr/" + W + "spacing")
    check("docx_normal_songti_12pt_single", attr(normal_font, "eastAsia") == "宋体" and normal_size == "24" and attr(normal_line, "line") == "240" and attr(normal_line, "lineRule") == "auto", path=rel(path))
    fonts_checked = 0
    font_errors = []
    spacing_errors = []
    title_checked = False
    for pi, paragraph in enumerate(main.iter(W + "p")):
        ppr = paragraph.find(W + "pPr")
        style_id = attr(paragraph.find(W + "pPr/" + W + "pStyle"), "val", "Normal")
        style = by_id.get(style_id, normal)
        line = paragraph.find(W + "pPr/" + W + "spacing")
        if line is None or attr(line,"line") is None:
            line = style.find(W + "pPr/" + W + "spacing")
        if line is None or attr(line,"line") is None:
            line = normal_line
        if attr(line, "line") != "240" or attr(line, "lineRule", "auto") != "auto":
            spacing_errors.append(pi)
        full_text = "".join(t.text or "" for t in paragraph.iter(W + "t"))
        is_title = is_main and full_text == "观测约束下的街区交通数字孪生标定与治理适用边界研究"
        expected_font = "黑体" if is_title or style_id == "Heading1" else "宋体"
        expected_size = "32" if is_title else "28" if style_id == "Heading1" else "24"
        if is_title:
            title_checked = True
            check("docx_title_centered", attr(paragraph.find(W + "pPr/" + W + "jc"), "val") == "center", path=rel(path))
        if style_id == "Heading1":
            check("docx_level1_heading_centered", attr(paragraph.find(W + "pPr/" + W + "jc"), "val") == "center", path=rel(path), paragraph=pi)
        for run_xml in paragraph.iter(W + "r"):
            rt = "".join(t.text or "" for t in run_xml.iter(W + "t"))
            if not re.search(r"[\u3400-\u9fff]", rt):
                continue
            rf = run_xml.find(W + "rPr/" + W + "rFonts")
            font_name = attr(rf, "eastAsia", attr(normal_font, "eastAsia"))
            size = attr(run_xml.find(W + "rPr/" + W + "sz"), "val", normal_size)
            fonts_checked += 1
            if font_name != expected_font or size != expected_size:
                font_errors.append({"paragraph": pi, "specified_font": font_name, "size_half_points": size, "expected_font": expected_font, "expected_half_points": expected_size})
    check("docx_visible_chinese_font_sizes", not font_errors and fonts_checked > 0, path=rel(path), runs_checked=fonts_checked, mismatches=font_errors)
    check("docx_all_paragraphs_single_spacing", not spacing_errors, path=rel(path), paragraphs_checked=len(list(main.iter(W + "p"))), mismatched_paragraphs=spacing_errors)
    if is_main:
        check("docx_title_present", title_checked, path=rel(path))
        image_hashes = {hashlib.sha256(data).hexdigest() for name,data in members.items() if name.startswith("word/media/")}
        expected = [OUT / "figures" / (s + ".png") for s in ("network_v2", "calibration_v2", "governance_v2", "timeseries_v2")]
        check("docx_four_exact_source_figures_embedded", len(image_hashes) == 4 and all(sha(p) in image_hashes for p in expected), path=rel(path), embedded_unique_images=len(image_hashes))
    return members


def original_files():
    rows = list(csv.DictReader((ROOT / "competition/file_manifest.csv").open(encoding="utf-8-sig", newline="")))
    for row in rows:
        path = ROOT / row["current_path"]
        check("original_manifest_file_unchanged", path.is_file() and path.stat().st_size == int(row["bytes"]) and sha(path) == row["sha256"], path=row["current_path"])
    check("original_manifest_entry_count", len(rows) == 34, entries=len(rows), competition_original_entries=sum(r["current_path"].startswith("competition/") for r in rows))


def secret_scan(documents, pdf_results):
    private = json.loads((ROOT / "config/baidu.local.json").read_text(encoding="utf-8"))
    secrets = [private.get(key) for key in ("ak", "sk")]
    check("secret_scan_credentials_loaded", len(secrets) == 2 and all(isinstance(v,str) and len(v) >= 8 for v in secrets), count=sum(isinstance(v,str) and len(v)>=8 for v in secrets))
    secrets = [v for v in secrets if isinstance(v,str) and len(v)>=8]
    scanned = 0
    leaks = []
    for path,members in documents.items():
        for name,data in members.items():
            if not (name.endswith(".xml") or name.endswith(".rels")):
                continue
            scanned += 1
            decoded = data.decode("utf-8", errors="replace")
            flattened = "".join(ET.fromstring(data).itertext())
            if any(value in decoded or value in norm(flattened) for value in secrets):
                leaks.append(rel(path) + ":" + name)
    for path,result in pdf_results.items():
        scanned += 1
        content = result["text"] + result["info"]
        if any(value in content or value in norm(content) for value in secrets):
            leaks.append(rel(path))
    check("private_ak_sk_absent_from_decoded_documents", not leaks, decoded_parts_checked=scanned, affected_paths=leaks, matched_private_values_recorded=False)


def content_checks(paper, manifest):
    pages, text = paper["pages"], paper["text"]
    compact = norm(text).replace(",", "").replace("，", "")
    expected_numbers = ("305", "308", "221", "44", "34113", "745", "438.67", "452.67", "567", "206.29", "200.46", "278.665", "209.930", "68.735", "1437", "1496")
    for value in expected_numbers:
        check("key_v2_number_in_pdf", value in compact, expected_number=value)
    for value in ("8.77%", "3340"):
        check("legacy_v1_benefit_number_absent", value not in compact, obsolete_number=value)
    check("legacy_v1_task_benefit_pair_absent", not re.search(r"292.{0,12}(?:232)", compact))
    check("abstract_is_first_page", "摘要" in norm(pages[0]) and "关键词" in norm(pages[0]) and "目录" not in norm(pages[0]))
    check("contents_is_second_page", len(pages)>1 and "目录" in norm(pages[1]))
    check("no_unfilled_page_placeholders", "待更新" not in text and "{{" not in text)
    check("manifest_page_count_matches_pdf", manifest['paper_pages'] == len(pages), actual=len(pages), manifest=manifest['paper_pages'])
    for heading, expected in manifest['heading_pages'].items():
        matched = [i for i,page in enumerate(pages,1) if i>2 and norm(heading) in [norm(line) for line in page.splitlines()]]
        check("heading_actual_page_matches_toc", bool(matched) and matched[0] == expected, heading=heading, expected_page=expected, observed_pages=matched)
        toc_lines = [norm(line) for line in pages[1].splitlines()]
        check("heading_toc_text_and_page_present", any(re.fullmatch(re.escape(norm(heading))+r"[.…·]*"+str(expected),line) for line in toc_lines), heading=heading, expected_page=expected)
    extracted = (OUT / "paper_extracted.txt").read_text(encoding="utf-8")
    check("saved_extracted_text_matches_actual_pdf", norm(extracted) == norm("\f".join(pages)))
    font_listing = run(["pdffonts", str(OUT / "paper.pdf")])
    check("pdf_font_listing_matches_manifest", font_listing == manifest["actual_pdf_fonts"])
    check("font_substitution_is_explicitly_disclosed", "替代" in manifest.get("font_status", ""))


def write_report(manifest_hash=None):
    failed = [c for c in CHECKS if not c["passed"]]
    result = {"schema_version": 1, "status": "passed_with_disclosed_font_limitation" if not failed else "failed", "checked_at": datetime.now(timezone.utc).isoformat(), "checker_sha256": sha(Path(__file__)), "build_manifest_sha256": manifest_hash, "checks_count": len(CHECKS), "passed_count": len(CHECKS)-len(failed), "failed_count": len(failed), "checks": CHECKS, "limitations": LIMITATIONS, "writes": [str(REPORT.relative_to(ROOT)), str(REPORT.with_suffix('.md').relative_to(ROOT))]}
    REPORT.parent.mkdir(parents=True,exist_ok=True)
    REPORT.write_text(json.dumps(result, ensure_ascii=False, indent=2)+"\n", encoding="utf-8")
    lines = ["# V2论文技术检查", "", f"实际检查：{len(CHECKS)}项；通过{len(CHECKS)-len(failed)}项；失败{len(failed)}项。", "", "检查内容：构建输入与产物SHA、图来源与嵌入、DOCX匿名元数据及字体字号/单倍行距、实际PDF连续居中页码与目录、关键V2数值、私密配置真实AK/SK在内存中的比对、34项原始文件清单完整性。", "", "失败项目："]
    lines += ["- " + c["name"] + ("："+c.get("path", "") if c.get("path") else "") for c in failed] if failed else ["- 无。"]
    lines += ["", "已知边界：", *["- "+v for v in LIMITATIONS], "", "详细逐项结果见同名JSON。没有记录任何私密值，没有执行官方上传或人工签署。"]
    REPORT.with_suffix(".md").write_text("\n".join(lines)+"\n",encoding="utf-8")
    print(json.dumps({"status":result['status'],"checks":len(CHECKS),"failed":len(failed),"report":rel(REPORT)},ensure_ascii=False))
    return 1 if failed else 0


def main():
    required = [OUT/name for name in ("paper.docx","paper.pdf","ai_usage.docx","ai_usage.pdf","build_manifest.json")]
    for path in required:
        check("required_file_exists",path.is_file(),path=rel(path))
    if not all(p.is_file() for p in required):
        return write_report()
    manifest_path = OUT / "build_manifest.json"
    manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
    check_hashes(manifest)
    docs = {OUT/"paper.docx":docx_checks(OUT/"paper.docx"),OUT/"ai_usage.docx":docx_checks(OUT/"ai_usage.docx",False)}
    pdfs = {OUT/"paper.pdf":read_pdf(OUT/"paper.pdf"),OUT/"ai_usage.pdf":read_pdf(OUT/"ai_usage.pdf")}
    content_checks(pdfs[OUT/"paper.pdf"],manifest)
    original_files()
    secret_scan(docs,pdfs)
    return write_report(sha(manifest_path))


if __name__ == "__main__":
    sys.exit(main())
