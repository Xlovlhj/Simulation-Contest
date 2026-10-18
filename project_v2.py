#!/usr/bin/env python3
"""Serve the saved V2 explanation locally; never starts collection or simulation."""
from __future__ import annotations
import argparse
import functools
import hashlib
import http.server
import json
from pathlib import Path
import sys
from urllib.parse import unquote, urlsplit

sys.dont_write_bytecode = True
ROOT = Path(__file__).resolve().parent
DIRECTORY = ROOT / "outputs/revision_v2"


class LocalHandler(http.server.SimpleHTTPRequestHandler):
    def translate_path(self, path):
        parts = unquote(urlsplit(path).path).split('/')
        if '..' in parts or any('\x00' in part for part in parts):
            return str(DIRECTORY / '__not_found__')
        translated = Path(super().translate_path(path)).resolve()
        if not translated.is_relative_to(DIRECTORY.resolve()):
            return str(DIRECTORY / '__not_found__')
        return str(translated)

    def list_directory(self, path):
        self.send_error(403, 'Directory listing disabled')
        return None


def check_artifacts():
    manifest_path = DIRECTORY / 'manifest.json'
    if not manifest_path.is_file() or not (DIRECTORY / 'index.html').is_file():
        raise ValueError('请先运行 python3 scripts/build_revision_v2.py 生成已完成结果的说明页。')
    manifest = json.loads(manifest_path.read_text())
    if manifest.get('status') != 'built_from_completed_results':
        raise ValueError('当前说明页尚未从完整结果生成。')
    for name, expected in manifest['artifacts'].items():
        path = (DIRECTORY / name).resolve()
        if not path.is_relative_to(DIRECTORY.resolve()) or not path.is_file():
            raise ValueError('说明页文件路径不合法：' + name)
        if hashlib.sha256(path.read_bytes()).hexdigest() != expected:
            raise ValueError('说明页文件发生变化，请重新生成：' + name)


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--port', type=int, default=8766)
    args = parser.parse_args()
    if not 1024 <= args.port <= 65535:
        raise ValueError('端口须在1024至65535之间。')
    check_artifacts()
    handler = functools.partial(LocalHandler, directory=str(DIRECTORY))
    with http.server.ThreadingHTTPServer(('127.0.0.1', args.port), handler) as server:
        print(f'打开 http://127.0.0.1:{args.port} 查看V2研究说明；按Ctrl+C停止。', flush=True)
        print('此入口只展示已保存结果，不会启动百度采集、标定或仿真。', flush=True)
        server.serve_forever()


if __name__ == '__main__':
    try:
        main()
    except KeyboardInterrupt:
        pass
    except (OSError, ValueError, KeyError) as error:
        print('无法启动：' + str(error), file=sys.stderr)
        raise SystemExit(1)
