#!/usr/bin/env python3
# AI-assisted: OpenAI Codex, 2026-10-18. Exact model snapshot not supplied.
"""B题统一入口：本地展示、离线复现、检查与打包。"""
from __future__ import annotations
import argparse
import functools
import http.server
import json
import os
from pathlib import Path
import subprocess
import sys

sys.dont_write_bytecode = True
ROOT = Path(__file__).resolve().parent


def python_path() -> str:
    local = ROOT / '.venv/bin/python'
    return str(local) if local.exists() else sys.executable


def run_script(name: str, *args: str) -> None:
    env = dict(os.environ)
    temporary = ROOT / '.cache/runtime'
    temporary.mkdir(parents=True, exist_ok=True)
    env.update(TMPDIR=str(temporary), PYTHONDONTWRITEBYTECODE='1')
    subprocess.run([python_path(), '-B', str(ROOT / 'scripts' / name), *args], cwd=ROOT, env=env, check=True)


def build_app() -> None:
    if (ROOT / 'SUPPORT_MANIFEST.json').is_file():
        # The compact support archive deliberately omits regenerable SUMO XML.
        # Its standalone app is already built, so check its frozen bytes rather
        # than invoking a rebuild that depends on omitted runtime artifacts.
        run_script('check_delivery.py')
        print('支撑包成品已通过完整性校验，可运行 python3 project.py 查看。', flush=True)
        print('此操作复用冻结网页；如需从零核对数值，按 README 准备工作区环境后运行 python3 project.py reproduce。', flush=True)
        return
    run_script('build_project.py')


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('action', nargs='?', default='serve', choices=['serve', 'status', 'build', 'reproduce', 'check', 'package', 'video'])
    parser.add_argument('--port', type=int, default=8765)
    args = parser.parse_args()
    try:
        if args.action == 'status':
            run_script('collect_baidu.py', '--status')
        elif args.action == 'build':
            build_app()
        elif args.action == 'reproduce':
            run_script('reproduce_project.py')
        elif args.action == 'check':
            run_script('check_delivery.py')
        elif args.action == 'package':
            run_script('package_project.py')
        elif args.action == 'video':
            run_script('record_demo.py')
        else:
            directory = ROOT / 'outputs/app'
            if not (directory / 'index.html').exists():
                build_app()
            if not 1024 <= args.port <= 65535:
                raise ValueError('端口须在1024至65535之间')

            class LocalHandler(http.server.SimpleHTTPRequestHandler):
                def translate_path(self, path):
                    translated = Path(super().translate_path(path)).resolve()
                    if not translated.is_relative_to(directory.resolve()):
                        return str(directory / '__not_found__')
                    return str(translated)

                def list_directory(self, path):
                    self.send_error(403, 'Directory listing disabled')
                    return None

            handler = functools.partial(LocalHandler, directory=str(directory))
            server = http.server.ThreadingHTTPServer(('127.0.0.1', args.port), handler)
            print(f'打开 http://127.0.0.1:{args.port} ；按 Ctrl+C 结束本地展示。', flush=True)
            print('此入口仅展示冻结结果，不会启动百度采集。', flush=True)
            server.serve_forever()
    except KeyboardInterrupt:
        return 0
    except (OSError, ValueError, KeyError, subprocess.CalledProcessError) as exc:
        print('操作未完成：' + str(exc), file=sys.stderr)
        return 1
    return 0


if __name__ == '__main__':
    raise SystemExit(main())
