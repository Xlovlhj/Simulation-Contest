"""用真实浏览器核查离线阅读、勾选持久化、导入导出与窄屏布局。"""
from datetime import datetime, timedelta, timezone
import json
import os
from pathlib import Path
import sys

from playwright.sync_api import sync_playwright, expect

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "src"))
from bev.io import read_json, write_json, sha256
from bev.portal import OUTPUT_NAME, page_id


def main():
    output = ROOT / "results/portal_checks"
    output.mkdir(parents=True, exist_ok=True)
    candidates = [Path(os.environ.get("PROGRAMFILES", "C:/Program Files")) / "Google/Chrome/Application/chrome.exe",
                  Path("C:/Program Files (x86)/Microsoft/Edge/Application/msedge.exe")]
    executable = next((p for p in candidates if p.exists()), None)
    if executable is None:
        raise RuntimeError("Chrome or Edge is needed for this optional browser check.")
    errors, network, unexpected_files, completed = [], [], [], []
    with sync_playwright() as engine:
        browser = engine.chromium.launch(executable_path=str(executable), headless=True)
        context = browser.new_context(viewport={"width": 1440, "height": 1080}, accept_downloads=True, offline=True)
        page = context.new_page()
        page.on("pageerror", lambda error: errors.append(str(error)))
        page.on("request", lambda request: network.append(request.url) if request.url.startswith(("http://", "https://")) else None)
        page.on("request", lambda request: unexpected_files.append(request.url) if request.url.startswith("file:") and request.url.split('#')[0] != (ROOT/OUTPUT_NAME).as_uri() else None)
        page.goto((ROOT / OUTPUT_NAME).as_uri(), wait_until="load")
        expect(page.locator("#progress-title")).to_be_visible()
        expect(page.locator('.stage-card [role="progressbar"]')).to_have_count(6)
        latest=read_json(ROOT / "records/latest_state.json")
        project=read_json(ROOT / "portal/project.json")
        expect(page.locator(".hero-aside .badge")).to_have_text(project["stage_label"])
        expect(page.locator(".hero-copy .lead")).to_have_text(project["description"])
        expect(page.locator(".focus a")).to_have_attribute("data-page",page_id(latest["report"]))
        page.locator(".stage-card details").first.locator("summary").click()
        for item in project['stages'][0]['items']:
            expect(page.locator(".stage-card").first).to_contain_text(item['text'])
        page.screenshot(path=str(output / "homepage_desktop.png"), full_page=True)
        completed.append("Offline file opening, homepage and six declared progress bars")

        overview_id = page_id("docs/三问任务与成果速览.md")
        page.locator(f'.hero-copy a[data-page="{overview_id}"]').click()
        expect(page.locator(f'.nav-item[data-page="{overview_id}"]')).to_have_attribute("aria-current", "page")
        overview = page.locator("#document-content")
        for phrase in ["第一问", "第二问", "第三问", "通俗解释", "14.05", "6.03 V", "8.30 V", "11.83%", "59/60", "0/60", "19页最终技术稿"]:
            expect(overview).to_contain_text(phrase)
        page.screenshot(path=str(output / "question_overview_desktop.png"), full_page=True)
        completed.append("Homepage one-sentence purpose and overview link; new three-question tab, plain explanations, metrics and limitations")

        page.locator('.nav-item[data-page="terms"]').click()
        total = len(read_json(ROOT / "portal/terms.json"))
        expect(page.locator(".term-card")).to_have_count(total)
        checkbox = page.get_by_role("checkbox", name="已掌握：空气阻力系数", exact=True)
        checkbox.check()
        expect(page.locator("#term-cd")).to_have_class("term-card mastered")
        page.reload(wait_until="load")
        expect(page.get_by_role("checkbox", name="已掌握：空气阻力系数", exact=True)).to_be_checked()
        page.locator('[data-filter="mastered"]').click()
        expect(page.locator(".term-card")).to_have_count(1)
        page.locator('[data-filter="unmastered"]').click()
        expect(page.locator(".term-card")).to_have_count(total-1)
        page.locator('[data-filter="all"]').click()
        page.locator("#term-category").select_option(label="车辆参数")
        expect(page.locator("#term-soc")).to_have_count(0)
        page.locator("#term-category").select_option("all")
        page.locator("#term-search").fill("SOC")
        expect(page.locator("#term-soc")).to_be_visible()
        page.locator("#term-search").fill("")
        page.screenshot(path=str(output / "glossary_desktop.png"), full_page=False)
        completed.append("Glossary checkbox fading, reload persistence, learned filter, category and term search")

        with page.expect_download() as download_info:
            page.locator("#export-learning").click()
        download_path = output / "learning_roundtrip.json"
        download_info.value.save_as(download_path)
        exported = json.loads(download_path.read_text(encoding="utf-8"))
        assert exported["mastered"] == ["cd"]
        page.get_by_role("checkbox", name="已掌握：空气阻力系数", exact=True).uncheck()
        page.on("dialog", lambda dialog: dialog.accept())
        page.locator("#learning-import").set_input_files(download_path)
        expect(page.get_by_role("checkbox", name="已掌握：空气阻力系数", exact=True)).to_be_checked()
        invalid = {"name": "invalid.json", "mimeType": "application/json", "buffer": b'{"format":"wrong","mastered":["soc"]}'}
        page.locator("#learning-import").set_input_files(invalid)
        expect(page.locator("#toast")).to_contain_text("有效")
        expect(page.get_by_role("checkbox", name="已掌握：空气阻力系数", exact=True)).to_be_checked()
        expect(page.locator("#toast")).not_to_have_class("visible",timeout=5000)
        completed.append("Learning JSON export/import round trip; invalid import leaves state intact")

        page.locator("#global-search").fill("理想终态热修正")
        expect(page.locator("h1")).to_contain_text("查找")
        expect(page.locator(".search-result").first).to_be_visible()
        page.locator('.search-result[data-search-page="terms"]').first.click()
        expect(page.locator("#term-heat-correction")).to_be_visible()
        expect(page.get_by_role("checkbox", name="已掌握：空气阻力系数", exact=True)).to_be_checked()
        completed.append("Full-text document/term search and result navigation")

        docs = page.evaluate("JSON.parse(document.getElementById('portal-data').textContent).pages.map(p=>({id:p.id,path:p.path}))")
        for doc in docs:
            page.locator(f'.nav-item[data-page="{doc["id"]}"]').click()
            expect(page.locator(f'.nav-item[data-page="{doc["id"]}"]')).to_have_attribute("aria-current","page")
            expect(page.locator("#document-content")).to_be_visible()
            assert page.locator("#document-content").inner_text().strip(), doc["path"]
            try:
                page.wait_for_function("Array.from(document.querySelectorAll('.math-inline,.math-display')).every(el=>el.querySelector('mjx-container'))",timeout=20000)
            except Exception as error:
                detail=page.evaluate("({hash:location.hash,error:document.getElementById('document-content')?.dataset.mathError,missing:Array.from(document.querySelectorAll('.math-inline,.math-display')).filter(el=>!el.querySelector('mjx-container')).map(el=>el.textContent).slice(0,5)})")
                raise AssertionError(json.dumps({'document':doc,'detail':detail,'network':network},ensure_ascii=True)) from error
            assert page.locator('[data-mml-node="merror"]').count()==0, doc["path"]
            assert page.locator('[data-math-error]').count()==0, doc["path"]
            assert page.evaluate("Array.from(document.querySelectorAll('mjx-assistive-mml')).every(el=>el.getBoundingClientRect().width<=1.1 && el.getBoundingClientRect().height<=1.1)"), (doc["path"], "Assistive math layer must not produce duplicate visible formulas")
        principle_id = page_id("docs/C题建模原理说明.md")
        page.locator(f'.nav-item[data-page="{principle_id}"]').click()
        expect(page.locator(".mermaid-figure svg")).to_be_visible(timeout=20000)
        expect(page.locator("mjx-container").first).to_be_visible(timeout=20000)
        page.wait_for_function("Array.from(document.querySelectorAll('.math-inline,.math-display')).every(el=>el.querySelector('mjx-container'))",timeout=20000)
        assert page.locator('[data-mml-node="merror"]').count() == 0
        page.screenshot(path=str(output / "principles_desktop.png"), full_page=False)
        completed.append(f"All {len(docs)} document tabs and their formulas render without math errors; offline Mermaid diagram renders")

        page.locator('.nav-item[data-page="results"]').click()
        expected_figures=page.evaluate("JSON.parse(document.getElementById('portal-data').textContent).figures.length")
        expect(page.locator(".figure-card")).to_have_count(expected_figures)
        if "durability" in latest:
            expect(page.locator("#content")).to_contain_text("驾驶90分钟 + 停车温控2小时")
            expect(page.locator("#content")).to_contain_text("17组热终态接近")
        if "reliability" in latest:
            evidence=latest["reliability"]
            expect(page.locator("#content")).to_contain_text(f"{evidence['accepted']} / {evidence['requested']} 次非线性重拟合")
            expect(page.locator("#content")).to_contain_text(f"{100*evidence['Cr_lower_bound_fraction']:.1f}%")
            expect(page.locator("#content")).to_contain_text("旧线性区间穿零的结果保留")
            completed.append("Latest nonlinear reliability evidence, boundary mass and conditional-range limits are visible")
        page.locator("[data-figure]").first.click()
        expect(page.locator("#detail-dialog")).to_be_visible()
        page.locator("#close-dialog").click()
        page.locator("#development-video").evaluate("async video => { video.muted=true; await video.play(); }")
        page.wait_for_function("document.getElementById('development-video').currentTime > 0")
        page.locator("#development-video").evaluate("video => video.pause()")
        completed.append("Embedded figure enlargement and actual offline MP4 playback")

        page.set_viewport_size({"width":390,"height":844})
        page.goto((ROOT / OUTPUT_NAME).as_uri()+"#p=home",wait_until="load")
        expect(page.locator("#menu-toggle")).to_be_visible()
        page.screenshot(path=str(output / "homepage_mobile.png"),full_page=True)
        for target in ["home", overview_id, "terms", principle_id, "results", "resources"]:
            page.locator("#menu-toggle").click()
            page.locator(f'.nav-item[data-page="{target}"]').click()
            if target==principle_id:
                expect(page.locator("mjx-container").first).to_be_visible(timeout=20000)
            assert page.evaluate("document.documentElement.scrollWidth <= window.innerWidth"), target
        completed.append("390px layout, mobile navigation and no page-level horizontal overflow")

        no_storage = browser.new_context(viewport={"width":1280,"height":800},offline=True)
        no_storage.add_init_script("Object.defineProperty(Storage.prototype, 'setItem', {value:function(){throw new Error('blocked')}})")
        fallback = no_storage.new_page()
        fallback.goto((ROOT / OUTPUT_NAME).as_uri()+"#p=terms",wait_until="load")
        fallback.get_by_role("checkbox",name="已掌握：荷电状态",exact=True).check()
        expect(fallback.locator("#storage-status")).to_contain_text("未允许保存")
        expect(fallback.locator("#term-soc")).to_have_class("term-card mastered")
        completed.append("Storage-denied mode keeps interaction usable and advises export")
        no_storage.close()
        browser.close()
    assert not errors, errors
    assert not network, network
    assert not unexpected_files, unexpected_files
    report={"date_beijing":datetime.now(timezone(timedelta(hours=8))).isoformat(timespec="seconds"),
            "browser":str(executable),"html_sha256":sha256(ROOT/OUTPUT_NAME),"completed":completed,"page_errors":errors,"http_requests":network,"unexpected_file_requests":unexpected_files,"passed":True}
    write_json(output/"browser_verification.json",report)
    print(json.dumps(report,ensure_ascii=True,indent=2))


if __name__=="__main__":
    main()
