"""Re-acquire the five initial official transport sources with provenance."""
import concurrent.futures
import json
from download_sources import ROOT, download


def main():
    jobs = json.loads((ROOT / "source_urls.json").read_text(encoding="utf-8"))["initial_sources"]
    with concurrent.futures.ThreadPoolExecutor(max_workers=3) as executor:
        results = list(executor.map(lambda x: download(x["file"], x["url"]), jobs))
    (ROOT / "sources_initial.json").write_text(json.dumps(results, ensure_ascii=False, indent=2), encoding="utf-8")


if __name__ == "__main__":
    main()
