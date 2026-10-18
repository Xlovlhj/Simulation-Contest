"""Render the real V2 page and check its local-only serving boundary."""
import hashlib
import json
from datetime import datetime, timezone
import os
from pathlib import Path
import shutil
import socket
import subprocess
import sys
import time
import urllib.error
import urllib.parse
import urllib.request

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT / 'scripts'))
from record_demo import Browser


def main():
    output = ROOT / 'outputs/quality'
    work = ROOT / '.cache' / ('v2v_' + datetime.now(timezone.utc).strftime('%H%M%S_%f'))
    work.mkdir(parents=True)
    with socket.socket() as sock:
        sock.bind(('127.0.0.1', 0)); port = sock.getsockname()[1]
    server_log = (work / 'server.log').open('wb')
    server = subprocess.Popen([sys.executable, '-B', str(ROOT / 'project_v2.py'), '--port', str(port)],
                              cwd=ROOT, env={**os.environ, 'PYTHONDONTWRITEBYTECODE': '1', 'TMPDIR': str(work)},
                              stdin=subprocess.DEVNULL, stdout=server_log, stderr=subprocess.STDOUT)
    browser = None
    try:
        address = f'http://127.0.0.1:{port}'
        for _ in range(100):
            try:
                with urllib.request.urlopen(address, timeout=1) as response:
                    if response.status == 200: break
            except (OSError, urllib.error.URLError):
                if server.poll() is not None: raise RuntimeError('Local view server failed')
                time.sleep(.05)
        else: raise RuntimeError('Local server not ready')
        http_checks = {}
        for path, expected in [('/', 200), ('/downloads/', 403), ('/%2e%2e/PROJECT_PLAN.md', 404),
                               ('/config/baidu.local.json', 404), ('/scripts/collect_baidu.py', 404)]:
            try:
                with urllib.request.urlopen(address + path, timeout=5) as response: status = response.status
            except urllib.error.HTTPError as error:
                status = error.code
            http_checks[path] = status
            if status != expected: raise ValueError(f'Unexpected HTTP boundary: {path}: {status}')
        browser = Browser(work)
        browser.call('Emulation.setDeviceMetricsOverride', {'width':1280,'height':900,'deviceScaleFactor':1,'mobile':False})
        browser.call('Page.navigate', {'url': address})
        for _ in range(100):
            if browser.js('Boolean(window.revisionV2?.ready)'): break
            time.sleep(.05)
        else: raise RuntimeError('V2 client script did not become ready')
        browser.js("document.documentElement.style.scrollBehavior='auto'")
        if browser.js('window.revisionV2.data.governance.runs.length') != 44:
            raise ValueError('Page does not contain all 44 governance runs')
        profile_checks = []
        profiles = browser.js('window.revisionV2.data.profiles.map(p=>p.id)')
        for profile in profiles:
            browser.js('window.revisionV2.select(' + json.dumps(profile) + ')')
            row = browser.js("({profile:document.getElementById('profile').value,badge:document.getElementById('screenPill').textContent,bars:document.querySelectorAll('#timeChart rect').length,errorRows:document.querySelectorAll('#errorRows tr').length})")
            if row['profile'] != profile or row['bars'] != 12 or row['errorRows'] != 3:
                raise ValueError('Profile interaction did not update actual chart and metrics')
            profile_checks.append(row)
        browser.js("window.revisionV2.select('headway_15');document.getElementById('onlyCurrent').checked=true;document.getElementById('onlyCurrent').dispatchEvent(new Event('change'))")
        selected_only = browser.js("({acceptedRows:document.querySelectorAll('#acceptedTable tbody tr').length,rejectedRows:document.querySelectorAll('#rejectedTable tbody tr').length})")
        if selected_only['acceptedRows'] != 0 or selected_only['rejectedRows'] != 6:
            raise ValueError('Training-screen governance tables are not separated correctly')
        browser.js("window.revisionV2.select(window.revisionV2.data.selected);document.getElementById('onlyCurrent').checked=false;document.getElementById('onlyCurrent').dispatchEvent(new Event('change'))")
        images = []
        for section in ('overview', 'times', 'governance'):
            browser.js(f"window.scrollTo(0,document.getElementById('{section}').getBoundingClientRect().top+scrollY-58)")
            time.sleep(.15)
            image = output / f'v2_view_{section}.jpg'
            image.write_bytes(browser.screenshot()); images.append(str(image.relative_to(ROOT)))
        links = browser.js("Array.from(document.querySelectorAll('.download-grid a')).map(a=>a.getAttribute('href'))")
        for link in links:
            with urllib.request.urlopen(address + '/' + urllib.parse.quote(link, safe='/'), timeout=5) as response:
                if response.status != 200: raise ValueError('Download link unavailable')
        browser.call('Emulation.setDeviceMetricsOverride', {'width':390,'height':844,'deviceScaleFactor':1,'mobile':True})
        browser.js('window.scrollTo(0,0)'); time.sleep(.1)
        mobile = browser.js('({viewport:innerWidth,documentWidth:document.documentElement.scrollWidth})')
        if mobile['documentWidth'] > mobile['viewport']:
            raise ValueError('Mobile page overflows viewport horizontally')
        image = output / 'v2_view_mobile.jpg'; image.write_bytes(browser.screenshot()); images.append(str(image.relative_to(ROOT)))
        # Also load as a file URL: all content and charts must work offline.
        browser.call('Page.navigate', {'url': (ROOT / 'outputs/revision_v2/index.html').as_uri()})
        for _ in range(100):
            if browser.js("location.protocol==='file:'&&Boolean(window.revisionV2?.ready)"): break
            time.sleep(.05)
        else: raise RuntimeError('Offline file page failed')
        if browser.errors: raise ValueError('Browser reported JavaScript exceptions')
        record = {'status':'passed','page':'outputs/revision_v2/index.html',
                  'page_sha256':hashlib.sha256((ROOT/'outputs/revision_v2/index.html').read_bytes()).hexdigest(),
                  'manifest_sha256':hashlib.sha256((ROOT/'outputs/revision_v2/manifest.json').read_bytes()).hexdigest(),
                  'http_checks':http_checks,'profile_checks':profile_checks,'governance_filter_check':selected_only,
                  'download_links_checked':len(links),'governance_runs':44,'offline_file_rendered':True,
                  'mobile_layout':mobile,'javascript_exceptions':browser.errors,'screenshots':images,
                  'browser':'Installed Chrome, actual headless rendering via local CDP',
                  'mutations':'Workspace-only static artifacts and local browser profile; no collection or numerical rerun'}
        (output/'v2_view_check.json').write_text(json.dumps(record,ensure_ascii=False,indent=2)+'\n')
        print(json.dumps(record,ensure_ascii=False,indent=2))
    finally:
        if browser: browser.close()
        server.terminate()
        try: server.wait(timeout=10)
        except subprocess.TimeoutExpired: server.kill(); server.wait()
        server_log.close()
        shutil.rmtree(work/'browser',ignore_errors=True)


if __name__=='__main__': main()
