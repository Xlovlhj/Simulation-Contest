"""Reusable local Chrome session for optional showcase browser checks.

Uses an existing Chrome/Chromium installation and websocket-client. Browser
profiles, caches, logs and screenshots stay inside the supplied workspace.
"""
from __future__ import annotations

import base64
import json
import os
from pathlib import Path
import shutil
import subprocess
import sys
import time
import urllib.request

sys.dont_write_bytecode = True
ROOT = Path(__file__).resolve().parent


class Browser:
    def __init__(self, workspace: Path):
        import websocket
        chrome = shutil.which("google-chrome") or shutil.which("chromium")
        if not chrome:
            raise RuntimeError("An existing Chrome or Chromium is required")
        self.profile = workspace / f"profile-{os.getpid()}"
        self.profile.mkdir(parents=True, exist_ok=True)
        self.log = (workspace / "chrome.log").open("wb")
        for name in ("tmp", "cache", "config", "data"):
            (workspace / name).mkdir(exist_ok=True)
        env = dict(os.environ)
        # Chromium's SingletonSocket needs a short path (Unix sockets cap 108 B).
        env.update(TMPDIR=str(ROOT / ".runtime/tmp"), XDG_CACHE_HOME=str(workspace / "cache"),
                   XDG_CONFIG_HOME=str(workspace / "config"), XDG_DATA_HOME=str(workspace / "data"))
        self.process = subprocess.Popen([
            chrome, "--headless", "--no-sandbox", "--disable-dev-shm-usage", "--disable-background-networking",
            "--disable-extensions", "--no-first-run", "--no-default-browser-check",
            "--remote-debugging-port=0", "--remote-allow-origins=http://localhost",
            "--user-data-dir=" + str(self.profile), "--crash-dumps-dir=" + str(workspace),
            "--use-gl=angle", "--use-angle=swiftshader", "--enable-unsafe-swiftshader",
            "--window-size=1440,1000", "about:blank",
        ], cwd=ROOT, env=env, stdin=subprocess.DEVNULL, stdout=self.log, stderr=subprocess.STDOUT)
        portfile = self.profile / "DevToolsActivePort"
        for _ in range(150):
            if portfile.exists():
                break
            if self.process.poll() is not None:
                raise RuntimeError("Chrome failed to start; see chrome.log")
            time.sleep(0.1)
        else:
            raise RuntimeError("Chrome startup timed out")
        port = int(portfile.read_text().splitlines()[0])
        with urllib.request.urlopen(f"http://127.0.0.1:{port}/json", timeout=5) as response:
            page = next(p for p in json.load(response) if p["type"] == "page")
        self.socket = websocket.create_connection(
            page["webSocketDebuggerUrl"], timeout=30, origin="http://localhost"
        )
        self.sequence = 0
        self.errors = []
        self.failed_requests = []
        self.responses = []
        self.console = []
        self.call("Page.enable")
        self.call("Runtime.enable")
        self.call("Network.enable")
        self.resize(1440, 1000)

    def call(self, method, params=None):
        self.sequence += 1
        identity = self.sequence
        self.socket.send(json.dumps({"id": identity, "method": method, "params": params or {}}))
        while True:
            message = json.loads(self.socket.recv())
            event, params = message.get("method"), message.get("params", {})
            if event == "Runtime.exceptionThrown":
                self.errors.append(params)
            elif event == "Network.loadingFailed":
                self.failed_requests.append(params)
            elif event == "Network.responseReceived":
                response = params["response"]
                self.responses.append({"url": response["url"], "status": response["status"]})
            elif event == "Runtime.consoleAPICalled":
                self.console.append({"type": params["type"], "args": [
                    a.get("value", a.get("description", "")) for a in params.get("args", [])
                ]})
            if message.get("id") == identity:
                if "error" in message:
                    raise RuntimeError(message["error"])
                return message.get("result", {})

    def js(self, expression):
        result = self.call("Runtime.evaluate", {
            "expression": expression, "returnByValue": True, "awaitPromise": True,
        })
        if result.get("exceptionDetails"):
            raise RuntimeError(result["exceptionDetails"])
        return result.get("result", {}).get("value")

    def resize(self, width, height, mobile=False):
        self.call("Emulation.setDeviceMetricsOverride", {
            "width": width, "height": height, "deviceScaleFactor": 1, "mobile": mobile,
        })

    def load(self, url):
        self.call("Page.navigate", {"url": url})
        for _ in range(200):
            if self.js("Boolean(window.evLab?.ready)"):
                return
            time.sleep(0.1)
        raise RuntimeError("Page failed to become ready")

    def screenshot(self, path):
        time.sleep(0.15)
        data = self.call("Page.captureScreenshot", {"format": "png", "fromSurface": True})["data"]
        path.write_bytes(base64.b64decode(data))
        print("SCREENSHOT " + str(path), flush=True)

    def click(self, selector):
        bounds = self.js(
            f"(()=>{{const e=document.querySelector({json.dumps(selector)});"
            "if(!e)return null;e.scrollIntoView({block:'center'});"
            "const r=e.getBoundingClientRect();return {x:r.x+r.width/2,y:r.y+r.height/2};})()"
        )
        if not bounds:
            raise RuntimeError(f"No element {selector}")
        for event in ("mousePressed", "mouseReleased"):
            self.call("Input.dispatchMouseEvent", {
                "type": event, "x": bounds["x"], "y": bounds["y"], "button": "left", "clickCount": 1,
            })

    def close(self):
        if hasattr(self, "socket"):
            self.socket.close()
        self.process.terminate()
        try:
            self.process.wait(timeout=10)
        except subprocess.TimeoutExpired:
            self.process.kill()
            self.process.wait()
        self.log.close()
