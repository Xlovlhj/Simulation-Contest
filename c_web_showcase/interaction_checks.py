"""Browser checks for the retained CarClear interaction shell (workspace-only)."""
from pathlib import Path
import hashlib
import json
import sys
import threading
import time

sys.dont_write_bytecode = True
from browser_session import Browser
from run import RunManager, ShowcaseServer

ROOT = Path(__file__).resolve().parent
OUT = ROOT / '.runtime/interaction_checks'


def main():
    OUT.mkdir(parents=True, exist_ok=True)
    manager = RunManager()
    server = ShowcaseServer(('127.0.0.1', 0), manager)
    threading.Thread(target=server.serve_forever, daemon=True).start()
    browser = None
    checks = []

    def check(label, condition, detail=None):
        checks.append({'name': label, 'passed': bool(condition), 'detail': detail})
        print(('PASS ' if condition else 'FAIL ') + label, flush=True)

    try:
        browser = Browser(OUT)
        browser.resize(1920, 1080)
        browser.load(f'http://127.0.0.1:{server.server_port}/')
        time.sleep(1.8)
        shell = browser.js("['.menu','.stage-wrap','.panel'].map(s=>{let r=document.querySelector(s).getBoundingClientRect();return {x:r.x,y:r.y,w:r.width,h:r.height}})")
        check('Original CarClear three-column geometry', shell == [
            {'x': 0, 'y': 60, 'w': 250, 'h': 1020},
            {'x': 250, 'y': 60, 'w': 1290, 'h': 1020},
            {'x': 1540, 'y': 60, 'w': 380, 'h': 1020}], shell)
        check('Original application shell loaded, old dashboard absent', browser.js("!!document.querySelector('script[src=\"js/app.js\"]') && !!document.querySelector('link[href=\"css/app.css\"]') && !document.querySelector('script[src=\"js/showcase.js\"]') && !document.querySelector('#timeline')"))
        ids = browser.js("[...document.querySelectorAll('#chapters .chap')].map(e=>e.dataset.id)")
        check('Six research chapters registered', ids == ['anatomy','drive','battery','electric','climate','research-results'], ids)
        for chapter in ids:
            browser.click(f'#chapters .chap[data-id="{chapter}"]')
            time.sleep(1.3)
            check('Chapter renders: '+chapter, browser.js(f"window.carclear.chapter.id === {json.dumps(chapter)} && document.querySelector('#stage canvas').width > 0 && window.carclear.stage.root.children.length > 0"))
            browser.screenshot(OUT / f'1920-{chapter}.png')
        browser.click('#chapters .chap[data-id="anatomy"]')
        time.sleep(1)
        # Project a real battery module into screen coordinates and click it.
        point = browser.js("""(async()=>{const THREE=await import('/vendor/three/three.module.min.js');const stage=carclear.stage;const pack=stage.pickables.find(o=>o.userData.partId==='battery');const mesh=pack.children.find(o=>o.isInstancedMesh);let mat=new THREE.Matrix4();mesh.getMatrixAt(16,mat);let p=new THREE.Vector3().setFromMatrixPosition(mat);mesh.localToWorld(p);p.project(stage.camera);let r=stage.renderer.domElement.getBoundingClientRect();return {x:r.x+(p.x+1)*r.width/2,y:r.y+(1-p.y)*r.height/2}})()""")
        for event in ('mousePressed','mouseReleased'):
            browser.call('Input.dispatchMouseEvent', {'type':event, **point, 'button':'left','clickCount':1})
        check('Clicking 3D battery opens component window', browser.js("!document.querySelector('#modal').hidden && document.querySelector('#modalCard').textContent.includes('动力电池')"))
        browser.screenshot(OUT/'1920-battery-window.png')
        browser.call('Input.dispatchKeyEvent',{'type':'keyDown','key':'Escape','code':'Escape','windowsVirtualKeyCode':27})
        check('Escape closes component window', browser.js("document.querySelector('#modal').hidden"))

        def range_end(key, end):
            selector = f'.ctl[data-control="{key}"] input'
            browser.click(selector)
            for kind in ('keyDown','keyUp'):
                browser.call('Input.dispatchKeyEvent',{'type':kind,'key':end,'code':end,'windowsVirtualKeyCode':36 if end=='Home' else 35})
            time.sleep(.35)

        range_end('explode','End');time.sleep(1.2)
        check('Original explode slider reacts to keyboard', browser.js('carclear.settings.explode===1'))
        browser.screenshot(OUT/'1920-exploded.png')
        browser.click('#chapters .chap[data-id="battery"]');time.sleep(1)
        original = browser.js("document.querySelector('#readout').textContent")
        range_end('soc','Home')
        check('SOC slider changes electrical readout', browser.js('carclear.settings.soc===5') and browser.js("document.querySelector('#readout').textContent") != original)
        browser.click('.ctl[data-control="mode"] button[data-v="charge"]');time.sleep(.3)
        check('Charge selector selects reverse energy direction', browser.js("carclear.settings.mode==='charge' && document.querySelector('.ctl[data-control=\"mode\"] button[data-v=\"charge\"]').getAttribute('aria-pressed')==='true'"))
        range_end('explode','End');time.sleep(.9)
        browser.screenshot(OUT/'1920-battery-expanded.png')
        browser.click('#chapters .chap[data-id="electric"]');time.sleep(.7)
        browser.click('.ctl[data-control="mode"] button[data-v="regen"]');time.sleep(.35)
        check('Regeneration selected',browser.js("carclear.settings.mode==='regen' && document.querySelector('#readout').textContent.includes('回收')"))
        browser.screenshot(OUT/'1920-regen.png')
        browser.click('#chapters .chap[data-id="climate"]');time.sleep(.8)
        range_end('temperature','Home');time.sleep(.5)
        check('Temperature input directly selects snowy scene',browser.js("carclear.settings.temperature===-10 && document.querySelector('.stage-wrap').dataset.weather==='snow'"))
        browser.screenshot(OUT/'1920-snow.png')
        range_end('temperature','End');time.sleep(.5)
        check('Temperature input directly selects hot scene',browser.js("carclear.settings.temperature===40 && document.querySelector('.stage-wrap').dataset.weather==='heat'"))
        browser.screenshot(OUT/'1920-heat.png')
        browser.click('#chapters .chap[data-id="anatomy"]');time.sleep(.5)
        check('Weather cleaned when chapter changes',browser.js("!document.querySelector('.stage-wrap').dataset.weather && document.querySelector('.stage-wrap').style.background===''") )
        browser.click('#chapters .chap[data-id="research-results"]');time.sleep(.8)
        browser.click('.ctl[data-control="ambient"] button[data-v="40"]')
        browser.click('.ctl[data-control="scope"] button[data-v="long"]');time.sleep(.3)
        check('Long-trip figure comes from archived experiment',browser.js("document.querySelector('#panelReadout').textContent.includes('9.850')"))
        evidence_buttons=browser.js("[...document.querySelectorAll('#panel .ctl-buttons button')].map(e=>e.textContent)")
        browser.click('#panel .ctl-buttons button[data-i="0"]')
        text=browser.js("document.querySelector('#modalCard').textContent")
        check('Calibration window has metrics and uncertainty',all(x in text for x in ('2.661','15.038','95%')))
        browser.screenshot(OUT/'1920-calibration-window.png')
        browser.click('#modalCard .x')
        browser.click('#panel .ctl-buttons button[data-i="1"]')
        text=browser.js("document.querySelector('#modalCard').textContent")
        check('Q1 and sensitivity window present', '14.0542' in text and '七参数' in text)
        browser.click('#modalCard .x')
        browser.resize(1440,900)
        browser.click('#chapters .chap[data-id="anatomy"]');time.sleep(1)
        browser.screenshot(OUT/'1440-anatomy.png')
        browser.resize(390,844,True)
        for chapter in ('anatomy','battery','climate','research-results'):
            browser.click(f'#chapters .chap[data-id="{chapter}"]');time.sleep(.7)
            browser.js('window.scrollTo(0,0)');time.sleep(.1)
            dimensions=browser.js('({viewport:innerWidth,content:document.documentElement.scrollWidth})')
            check('Mobile fits width: '+chapter,dimensions['content']<=dimensions['viewport']+1,dimensions)
            browser.screenshot(OUT/f'mobile-{chapter}.png')
        urls=[r['url'] for r in browser.responses]
        check('No large trajectory data loaded',not any('/data/showcase.json' in u for u in urls))
        check('No JavaScript exceptions',not browser.errors,browser.errors)
        check('No failed resources',not browser.failed_requests,browser.failed_requests)
        report={'passed':all(c['passed'] for c in checks),'checks':checks,'javascript_errors':browser.errors,
                'console':browser.console,'responses':browser.responses,'failed_requests':browser.failed_requests,
                'source_hashes':{str(p.relative_to(ROOT)):hashlib.sha256(p.read_bytes()).hexdigest()
                    for p in [ROOT/'index.html',ROOT/'js/app.js',ROOT/'css/app.css',ROOT/'css/research.css',*sorted((ROOT/'js/research').glob('*.js')),*sorted((ROOT/'js/chapters').glob('research-*.js'))]}}
        (OUT/'report.json').write_text(json.dumps(report,ensure_ascii=False,indent=2))
        print('Report:',OUT/'report.json',flush=True)
        return 0 if report['passed'] else 1
    finally:
        if browser:browser.close()
        server.shutdown();server.server_close();manager.close()

if __name__=='__main__':
    raise SystemExit(main())
