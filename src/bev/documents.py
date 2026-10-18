"""离线文档排版。AI辅助：Codex/OpenAI；具体版本和发布日期未核实。"""
from __future__ import annotations

import html
import os
from pathlib import Path

from .io import PROJECT_ROOT, sha256
from .portal import render_markdown


def make_html(text: str, source: Path, output: Path, *, title: str, paper: bool=False) -> None:
    css=(PROJECT_ROOT/'paper/style.css').read_text(encoding='utf-8')
    if paper:
        css+='\nmain > table:last-of-type td,main > table:last-of-type th {padding:1mm 1.5mm;}\n'
    math=(PROJECT_ROOT/'portal/vendor/mathjax-3.2.2-full.js').read_text(encoding='utf-8')
    if paper:
        abstract,body=text.split('<!-- PAPER_ABSTRACT_END -->')
        first,_=render_markdown(abstract,source,{})
        main,toc=render_markdown(body,source,{})
        content=f'<section class="abstract-page">{first}</section><section class="contents-page"><h2>目录</h2>{toc}</section><main>{main}</main>'
    else:
        content,_=render_markdown(text,source,{})
    content=content.replace('loading="lazy"','loading="eager"')
    markup='<!doctype html><html lang="zh-CN"><head><meta charset="utf-8"><title>'+html.escape(title)+'</title><style>'+css+'</style>'
    markup+='<script>window.MathJax={startup:{typeset:false},svg:{fontCache:"local"},tex:{tags:"none"},options:{enableMenu:false}};</script><script>'+math+'</script></head><body>'+content
    markup+='''<script>window.paperReady=MathJax.startup.promise.then(async()=>{
      for(const el of document.querySelectorAll('.math-inline,.math-display')){
        const tex=el.textContent.trim().slice(2,-2);
        el.replaceChildren(await MathJax.tex2svgPromise(tex,{display:el.classList.contains('math-display')}));
      }
      await document.fonts.ready;
    });</script></body></html>'''
    output.parent.mkdir(parents=True,exist_ok=True)
    output.write_text(markup,encoding='utf-8')


def render_pdf(html_path: Path, pdf_path: Path, *, paper: bool=False) -> dict:
    import pymupdf
    from playwright.sync_api import sync_playwright
    candidates=[Path(os.environ.get('PROGRAMFILES','C:/Program Files'))/'Google/Chrome/Application/chrome.exe',
                Path('C:/Program Files (x86)/Microsoft/Edge/Application/msedge.exe')]
    executable=next((p for p in candidates if p.exists()),None)
    if executable is None:
        raise RuntimeError('PDF排版需Chrome或Edge；数值仿真不需要浏览器。')
    errors,network=[],[]
    with sync_playwright() as engine:
        browser=engine.chromium.launch(executable_path=str(executable),headless=True)
        page=browser.new_page(offline=True,viewport={'width':650,'height':1300})
        page.on('pageerror',lambda e:errors.append(str(e)))
        page.on('request',lambda r:network.append(r.url) if r.url.startswith(('https:','http:')) else None)
        page.goto(html_path.resolve().as_uri(),wait_until='load')
        page.evaluate('async()=>await window.paperReady')
        page.evaluate('async()=>await Promise.all(Array.from(document.images).map(i=>i.decode()))')
        assert page.locator('[data-mml-node="merror"]').count()==0
        formulas=page.locator('.math-inline,.math-display').count()
        assert page.locator('mjx-container').count()==formulas
        figures=page.locator('img').count()
        page.emulate_media(media='print')
        assert page.evaluate('document.documentElement.scrollWidth<=document.documentElement.clientWidth+2'),'打印宽度溢出'
        previous=None
        for attempt in range(3):
            page.pdf(path=str(pdf_path),format='A4',print_background=True,prefer_css_page_size=True,
                     display_header_footer=True,header_template='<span></span>',
                     footer_template='<div style="width:100%;text-align:center;font:12pt SimSun;"><span class="pageNumber"></span></div>',
                     margin={'top':'22mm','bottom':'23mm','left':'19mm','right':'19mm'})
            if not paper:
                break
            with pymupdf.open(pdf_path) as partial:
                destinations={l['nameddest']:l['page']+1 for p in partial for l in p.get_links()
                              if l.get('nameddest') and l.get('page',-1)>=0}
            if destinations==previous:
                break
            page.evaluate('''destinations=>document.querySelectorAll('.contents-page .toc a').forEach(a=>{
              const key=decodeURIComponent(a.getAttribute('href').slice(1));
              if(!(key in destinations))throw Error('Missing contents destination');
              let label=a.querySelector('.toc-page');
              if(!label){label=document.createElement('span');label.className='toc-page';a.append(label);}
              label.textContent=destinations[key];
            })''',destinations)
            previous=destinations
        else:
            raise RuntimeError('目录页码未收敛')
        browser.close()
    assert not errors and not network,(errors,network)
    with pymupdf.open(pdf_path) as doc:
        texts=[p.get_text() for p in doc]
        assert all(len(t.strip())>100 for t in texts),'近空白页'
        assert not any(x in '\n'.join(texts) for x in ['Administrator','C:\\Users','@@'])
        for i,p in enumerate(doc):
            assert p.get_text(clip=pymupdf.Rect(0,p.rect.height-55,p.rect.width,p.rect.height)).strip()==str(i+1)
            for box in p.get_text('blocks'):
                assert box[0]>=0 and box[1]>=0 and box[2]<=p.rect.width+.1 and box[3]<=p.rect.height+.1
        if paper:
            assert '摘要' in texts[0] and '关键词' in texts[0] and '目录' in texts[1]
            assert '96串' in ''.join(texts) and '100串' not in ''.join(texts)
            def font_size(needle):
                return next(l['spans'][0]['size'] for p in doc for b in p.get_text('dict')['blocks'] if 'lines' in b
                            for l in b['lines'] if needle in ''.join(s['text'] for s in l['spans']))
            sizes={'body':font_size('电动车的驱动'),'title':font_size('集中参数仿真'),'heading':font_size('摘要')}
            assert all(abs(sizes[k]-v)<.15 for k,v in [('body',12),('title',16),('heading',14)])
            assert all(v in '\n'.join(texts) for v in ['14.0542','0.826','15.038','25.659','11.83','9.85','0.70','0.225357'])
        else:
            sizes={}
        from PIL import Image,ImageDraw
        w,h,columns=298,450,4
        sheet=Image.new('RGB',(columns*w,((len(doc)+columns-1)//columns)*h),'#e5e7eb')
        drawing=ImageDraw.Draw(sheet)
        for i,p in enumerate(doc):
            pix=p.get_pixmap(matrix=pymupdf.Matrix(.45,.45),alpha=False)
            thumb=Image.frombytes('RGB',[pix.width,pix.height],pix.samples)
            thumb.thumbnail((w-14,h-28));x=(i%columns)*w+(w-thumb.width)//2;y=(i//columns)*h+9
            sheet.paste(thumb,(x,y));drawing.text((x,y+thumb.height+4),f'Page {i+1}',fill='black')
        sheet.save(pdf_path.with_suffix('.preview.png'))
        result={'path':pdf_path.relative_to(PROJECT_ROOT).as_posix(),'pages':len(doc),'bytes':pdf_path.stat().st_size,
                'sha256':sha256(pdf_path),'formulas':formulas,'figures':figures,'actual_font_sizes_pt':sizes,
                'first_page_abstract':paper,'toc_page_numbers_checked':paper,'continuous_page_numbers':True,
                'text_inside_page':True,'http_requests':network,'page_errors':errors,'visual_review':'pending'}
    assert result['bytes']<=20_000_000
    return result
