"""Build a portable, offline project reading hub from authoritative local docs."""
# AI辅助：OpenAI Codex；精确型号及发布日期不可得，提交前据实际记录补齐并人工复核。
from __future__ import annotations

import base64
from datetime import datetime, timedelta, timezone
import hashlib
import json
import mimetypes
from pathlib import Path
import re
from urllib.parse import quote, unquote, urlsplit
import xml.etree.ElementTree as ET

PROJECT = Path(__file__).resolve().parents[2]


def document_paths(project):
    """All authored Markdown is discovered; vendor/runtime trees are excluded."""
    excluded = {".deps", ".git", "__pycache__", "node_modules", ".venv", "venv", "raw", "runtime",
                "frozen_inputs", "frozen_sources", "review_sources", "review_tools"}
    return sorted((p for p in project.rglob("*.md") if p.name != "AGENTS.md"
        and not excluded.intersection(p.relative_to(project).parts)
        and not any(part.endswith('_suite') for part in p.relative_to(project).parts[:-1])),
        key=lambda p: p.relative_to(project).as_posix())


def document_id(relative):
    return "doc-" + hashlib.sha256(relative.encode("utf-8")).hexdigest()[:12]


def local_target(project, source, href):
    parsed = urlsplit(href)
    if parsed.scheme or parsed.netloc:
        return None, parsed
    target = (source.parent / unquote(parsed.path)).resolve() if parsed.path else source.resolve()
    if not target.is_relative_to(project.resolve()):
        return None, parsed
    return target, parsed


def render_document(project, source, source_ids, image_cache, replay_path=None):
    import markdown

    raw = source.read_text(encoding="utf-8-sig")
    rendered = markdown.markdown(raw, extensions=["tables", "fenced_code", "sane_lists", "toc"],
        output_format="xhtml")
    tree = ET.fromstring("<div>" + rendered + "</div>")
    identifier = source_ids[source.resolve()]
    headings = []
    for node in tree.iter():
        if node.tag in {"h1", "h2", "h3", "h4", "h5", "h6"}:
            original = node.get("id", "section-" + str(len(headings)))
            node.set("id", identifier + "--" + original)
            headings.append({"id": node.get("id"), "level": int(node.tag[1]), "text": "".join(node.itertext())})
        if node.tag == "a":
            href = node.get("href", "")
            target, parsed = local_target(project, source, href)
            if target == project / "项目管理.html":
                node.set("href", "#home")
            elif replay_path is not None and target == replay_path:
                node.set("href", "#replay")
            elif target in source_ids:
                doc = source_ids[target]
                node.set("href", "#" + doc + ("/" + quote(doc + "--" + unquote(parsed.fragment), safe="") if parsed.fragment else ""))
            elif target is not None:
                node.set("href", quote(target.relative_to(project).as_posix(), safe="/"))
                if parsed.fragment:
                    node.set("href", node.get("href") + "#" + parsed.fragment)
                node.set("target", "_blank")
                node.set("rel", "noopener")
            elif parsed.scheme in {"http", "https", "mailto"}:
                node.set("target", "_blank")
                node.set("rel", "noopener noreferrer")
            else:
                node.attrib.pop("href", None)
        if node.tag == "img":
            target, _ = local_target(project, source, node.get("src", ""))
            if target is None or not target.is_file():
                raise ValueError(f"Image missing or outside project: {source.name}")
            key = target.relative_to(project).as_posix()
            if key not in image_cache:
                mime = mimetypes.guess_type(target.name)[0] or "application/octet-stream"
                image_cache[key] = "data:" + mime + ";base64," + base64.b64encode(target.read_bytes()).decode("ascii")
            node.attrib.pop("src", None)
            node.set("data-asset", key)
            node.set("loading", "lazy")
        if node.tag in {"video", "source"} and node.get("src"):
            target, _ = local_target(project, source, node.get("src", ""))
            if target is None or not target.is_file() or target.suffix.lower() != ".mp4":
                raise ValueError(f"Video missing or outside project: {source.name}")
            # Keep large media as a relative file beside the offline hub.
            # Documentation and images still live inside the generated HTML.
            node.set("src", quote(target.relative_to(project).as_posix(), safe="/"))
    for parent in list(tree.iter()):
        for i, child in enumerate(list(parent)):
            if child.tag == "table":
                parent.remove(child)
                wrapper = ET.Element("div", {"class": "table-scroll", "role": "region", "aria-label": "文档表格", "tabindex": "0"})
                wrapper.append(child)
                parent.insert(i, wrapper)
    text = " ".join(tree.itertext())
    return {"html": "".join(ET.tostring(child, encoding="unicode", method="html") for child in tree),
        "text": text, "headings": headings, "sha256": hashlib.sha256(source.read_bytes()).hexdigest()}


def build_project_hub(project=PROJECT):
    project = Path(project).resolve()
    assets = project / "管理中心"
    config = json.loads((assets / "项目状态.json").read_text(encoding="utf-8"))
    terms = json.loads((assets / "术语表.json").read_text(encoding="utf-8"))
    if len({t["id"] for t in terms}) != len(terms):
        raise ValueError("Term IDs must be unique and stable")
    sources = document_paths(project)
    source_ids = {p.resolve(): document_id(p.relative_to(project).as_posix()) for p in sources}
    images, documents = {}, []
    for path in sources:
        relative = path.relative_to(project).as_posix()
        metadata = config.get("documents", {}).get(relative, {})
        group = metadata.get("group", "新增文档")
        title = metadata.get("title", path.stem)
        doc = {"id": source_ids[path.resolve()], "title": title, "path": relative,
            "group": group, "summary": metadata.get("summary", "新增项目文档。"),
            "notice": metadata.get("notice", ""),
            **render_document(project, path, source_ids, images, (project / config["replay"]).resolve())}
        documents.append(doc)
    for stage in config["stages"]:
        if not stage["items"]:
            raise ValueError("Stage needs acceptance items")
        stage["done"] = sum(bool(i["done"]) for i in stage["items"])
        stage["total"] = len(stage["items"])
        stage["percent"] = round(100 * stage["done"] / stage["total"])
    verification = json.loads((project / config["verification"]).read_text(encoding="utf-8"))
    replay_html = (project / config["replay"]).read_text(encoding="utf-8")
    match = re.search(r'<script id="payload" type="application/json">(.*?)</script>', replay_html, re.S)
    if not match:
        raise ValueError("Replay is missing archived data")
    replay = json.loads(match.group(1))
    replay_info = {"run_id": replay["run_id"], "seed": replay["run"]["seed"],
        "policy": replay["run"].get("meter_policy"), "frames": len(replay["states"]),
        "roads": len(replay["roads"]), "first": replay["states"][0], "last": replay["states"][-1]}
    built_at = datetime.now(timezone(timedelta(hours=8))).isoformat(timespec="seconds")
    payload = {"config": config, "terms": terms, "documents": documents, "images": images,
        "built_at": built_at, "verification": verification,
        "replay_html": replay_html, "replay_info": replay_info}
    # Escape HTML delimiters in data even if future Markdown includes script tags.
    encoded = json.dumps(payload, ensure_ascii=False).replace("<", "\\u003c").replace(">", "\\u003e").replace("&", "\\u0026")
    template = (assets / "页面模板.html").read_text(encoding="utf-8")
    if template.count("__PROJECT_PAYLOAD__") != 1:
        raise ValueError("Template payload marker must occur exactly once")
    target = project / "项目管理.html"
    target.write_text(template.replace("__PROJECT_PAYLOAD__", encoded), encoding="utf-8")
    manifest = {"built_at": built_at, "page": target.name, "document_count": len(documents),
        "term_count": len(terms), "embedded_images": len(images), "page_bytes": target.stat().st_size,
        "documents": [{k: d[k] for k in ["id", "title", "path", "sha256"]} for d in documents]}
    (assets / "同步清单.json").write_text(json.dumps(manifest, ensure_ascii=False, indent=2), encoding="utf-8")
    return {k: v for k, v in manifest.items() if k != "documents"}
