"""恢复已固定版本的公开工况文件；只下载数据，不执行外部程序。"""
from pathlib import Path
import base64
import hashlib
import json
import urllib.request

root = Path(__file__).resolve().parents[1]
catalog = json.loads((root/"records/cltc_downloads.json").read_text(encoding="utf-8"))
for source in catalog["sources"]:
    target = root/source["local_path"]
    if target.exists() and hashlib.sha256(target.read_bytes()).hexdigest() == source["sha256"]:
        print(f"Verified cached: {target.name}")
        continue
    api = f"https://api.github.com/repos/{source['repository']}/contents/{source['path']}?ref={source['commit']}"
    with urllib.request.urlopen(api, timeout=20) as response:
        raw = base64.b64decode(json.load(response)["content"])
    if hashlib.sha256(raw).hexdigest() != source["sha256"]:
        raise ValueError("Downloaded file differs from the pinned original; no overwrite performed.")
    target.parent.mkdir(parents=True, exist_ok=True)
    target.write_bytes(raw)
    print(f"Downloaded and verified: {target.name}")
