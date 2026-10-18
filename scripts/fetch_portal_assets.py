"""固定版本的离线阅读资源；只下载资源，不执行远程代码。"""
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path
import hashlib
import json
import urllib.request

ROOT = Path(__file__).resolve().parents[1]
SOURCES = {
    "mathjax-3.2.2-full.js": "https://cdnjs.cloudflare.com/ajax/libs/mathjax/3.2.2/es5/tex-svg-full.js",
    "mathjax-LICENSE.txt": "https://raw.githubusercontent.com/mathjax/MathJax/3.2.2/LICENSE",
    "mermaid-9.4.3.js": "https://cdn.jsdelivr.net/npm/mermaid@9.4.3/dist/mermaid.min.js",
    "mermaid-LICENSE.txt": "https://raw.githubusercontent.com/mermaid-js/mermaid/v9.4.3/LICENSE",
}


def main():
    target = ROOT / "portal/vendor"
    target.mkdir(parents=True, exist_ok=True)
    def download(item):
        name, url = item
        cached = target / name
        data = cached.read_bytes() if cached.exists() else urllib.request.urlopen(url, timeout=40).read()
        if not data or (name.endswith(".js") and len(data) < 10000):
            raise ValueError(f"Invalid downloaded asset: {name}")
        (target / name).write_bytes(data)
        return {"path": f"portal/vendor/{name}", "url": url, "bytes": len(data), "sha256": hashlib.sha256(data).hexdigest()}
    with ThreadPoolExecutor(max_workers=4) as pool:
        records = list(pool.map(download, SOURCES.items()))
    (target / "sources.json").write_text(json.dumps(records, ensure_ascii=False, indent=2)+"\n", encoding="utf-8")
    print(json.dumps(records, ensure_ascii=False, indent=2))


if __name__ == "__main__":
    main()
