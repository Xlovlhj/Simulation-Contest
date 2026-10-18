#!/usr/bin/env python3
# AI-assisted: OpenAI Codex, 2026-10-18. Team review is required.
"""Record the real local browser application as a captioned MP4, offline."""
from __future__ import annotations
import argparse
import base64
from datetime import datetime, timezone
import hashlib
import json
import os
from pathlib import Path
import shutil
import subprocess
import sys
import time
import urllib.request

ROOT = Path(__file__).resolve().parents[1]
sys.dont_write_bytecode = True


class Browser:
    def __init__(self, workspace: Path):
        import websocket
        chrome = shutil.which('google-chrome') or shutil.which('chromium')
        if not chrome:
            raise RuntimeError('需要已安装的Chrome或Chromium以录制实际页面')
        self.profile = workspace / 'browser'
        self.profile.mkdir(parents=True, exist_ok=True)
        self.log = (workspace / 'browser.log').open('wb')
        env = dict(os.environ, TMPDIR=str(workspace))
        self.process = subprocess.Popen([chrome, '--headless', '--no-sandbox', '--disable-gpu',
            '--disable-background-networking', '--disable-extensions', '--no-first-run', '--no-default-browser-check',
            '--remote-debugging-port=0', '--remote-allow-origins=http://localhost',
            '--user-data-dir=' + str(self.profile), '--crash-dumps-dir=' + str(workspace),
            '--window-size=1280,720', 'about:blank'], cwd=ROOT, env=env,
            stdin=subprocess.DEVNULL, stdout=self.log, stderr=subprocess.STDOUT)
        portfile = self.profile / 'DevToolsActivePort'
        for _ in range(150):
            if portfile.exists():
                break
            if self.process.poll() is not None:
                raise RuntimeError('浏览器未启动；查看工作区录制日志')
            time.sleep(.1)
        else:
            raise RuntimeError('浏览器启动超时')
        port = int(portfile.read_text().splitlines()[0])
        self.address = 'http://127.0.0.1:' + str(port)
        with urllib.request.urlopen(self.address + '/json', timeout=5) as response:
            pages = json.load(response)
        page = next(p for p in pages if p['type'] == 'page')
        self.socket = websocket.create_connection(page['webSocketDebuggerUrl'], timeout=30, origin='http://localhost')
        self.sequence = 0
        self.errors = []
        self.call('Page.enable')
        self.call('Runtime.enable')
        self.call('Emulation.setDeviceMetricsOverride', {'width':1280, 'height':720,'deviceScaleFactor':1,'mobile':False})

    def call(self, method, params=None):
        self.sequence += 1
        identity = self.sequence
        self.socket.send(json.dumps({'id':identity, 'method':method, 'params':params or {}}))
        while True:
            message = json.loads(self.socket.recv())
            if message.get('method') == 'Runtime.exceptionThrown':
                self.errors.append(message['params'])
            if message.get('id') == identity:
                if 'error' in message:
                    raise RuntimeError(message['error'])
                return message.get('result', {})

    def js(self, expression):
        result = self.call('Runtime.evaluate', {'expression':expression, 'returnByValue':True, 'awaitPromise':True})
        if result.get('exceptionDetails'):
            raise RuntimeError(result['exceptionDetails'])
        return result.get('result', {}).get('value')

    def load(self, path):
        self.call('Page.navigate', {'url':path.as_uri()})
        for _ in range(150):
            if self.js('Boolean(window.projectDemo)'):
                return
            time.sleep(.1)
        raise RuntimeError('页面脚本未就绪')

    def screenshot(self):
        return base64.b64decode(self.call('Page.captureScreenshot', {'format':'jpeg', 'quality':84,'fromSurface':True})['data'])

    def close(self):
        if hasattr(self, 'socket'):
            self.socket.close()
        self.process.terminate()
        try:
            self.process.wait(timeout=10)
        except subprocess.TimeoutExpired:
            self.process.kill()
            self.process.wait()
        self.log.close()


def scenes(data):
    def choose(phase, policy, profile='primary'):
        return next(i for i,r in enumerate(data['replays']) if r['phase']==phase and r['policy_key']==policy and r['profile']==profile)
    return [
        {'tab':'overview','duration':13,'caption':'上海瑞金医院外围：将百度GIS观测、时间预测与交通治理实验连接起来。'},
        {'tab':'data','duration':20,'scroll':True,'caption':'跨日平均绝对误差仍较大：静态时间模型无法直接代表次日路况，结果如实保留。'},
        {'tab':'simulation','duration':27,'case':choose('S1','fixed'),'start':0,'end':1500,'caption':'一般交通：这里展示的是实际SUMO轨迹，车辆需求和微观参数为研究假设。'},
        {'tab':'simulation','duration':24,'case':choose('S2','A'),'start':480,'end':1320,'caption':'治理A：事件发布后，按当前已知完整路径代价决定是否绕行，保持原有需求。'},
        {'tab':'simulation','duration':17,'case':choose('S3','A','higher_demand'),'start':600,'end':1100,'caption':'较高需求下，独立路径引导A：保障任务从发布到到达需要292秒。'},
        {'tab':'simulation','duration':17,'case':choose('S3','B','higher_demand'),'start':600,'end':1100,'caption':'相同需求下，协同B降至232秒；普通交通总耗时下降，但仍有30辆车变慢。'},
        {'tab':'results','duration':20,'scroll':True,'caption':'完整对照显示：协同B仅在高需求情景获益，其余组无收益或增加等待。'},
        {'tab':'delivery','duration':10,'caption':'代码、数据、论文与复现入口已整理；队员按赛方AI规定完成核心工作与实质复核。'},
    ]


def main(argv=None):
    parser=argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--preview',action='store_true',help='Only inspect the browser and save one real screenshot per scene')
    parser.add_argument('--fps',type=int,default=8)
    args=parser.parse_args(argv)
    if not 2<=args.fps<=15:
        raise ValueError('Recording fps must be between 2 and 15')
    output=ROOT/'outputs/video';output.mkdir(parents=True,exist_ok=True)
    work=ROOT/'.cache'/('video_'+datetime.now(timezone.utc).strftime('%Y%m%dT%H%M%S'))
    work.mkdir(parents=True,exist_ok=False)
    browser=None;encoder=None
    try:
        browser=Browser(work);browser.load(ROOT/'outputs/app/index.html')
        data=browser.js('window.projectDemo.data');plan=scenes(data)
        browser.js('window.projectDemo.video()')
        if not args.preview:
            import imageio_ffmpeg
            target=output/'B_demo.mp4'
            encoder=subprocess.Popen([imageio_ffmpeg.get_ffmpeg_exe(),'-y','-hide_banner','-loglevel','error',
                '-f','image2pipe','-vcodec','mjpeg','-framerate',str(args.fps),'-i','-',
                '-c:v','libx264','-preset','fast','-crf','22','-pix_fmt','yuv420p','-r','24','-movflags','+faststart',str(target)],
                stdin=subprocess.PIPE, stdout=subprocess.DEVNULL, stderr=(work/'ffmpeg.log').open('wb'))
        frames=0
        for index,scene in enumerate(plan):
            browser.js('window.projectDemo.show('+json.dumps(scene['tab'])+');window.projectDemo.caption('+json.dumps(scene['caption'],ensure_ascii=False)+');window.scrollTo(0,0)')
            if 'case' in scene:
                browser.js('window.projectDemo.select('+str(scene['case'])+')')
            count=1 if args.preview else scene['duration']*args.fps
            scroll_max=browser.js('Math.max(0,document.documentElement.scrollHeight-innerHeight)') if scene.get('scroll') else 0
            for step in range(count):
                if 'case' in scene:
                    t=scene['start']+(scene['end']-scene['start'])*(step/max(1,count-1))
                    browser.js('window.projectDemo.setTime('+str(round(t,3))+')')
                if scroll_max:
                    progress=min(1,max(0,(step/max(1,count-1)-.15)/.7))
                    browser.js('window.scrollTo(0,'+str(round(scroll_max*progress))+')')
                if step==0:
                    time.sleep(.15)
                frame=browser.screenshot()
                if step==0:
                    (output/f'scene_{index+1:02d}.jpg').write_bytes(frame)
                if encoder:
                    encoder.stdin.write(frame)
                frames+=1
            print(f'场景 {index+1}/{len(plan)} 已录制：{scene["tab"]}',flush=True)
        if encoder:
            encoder.stdin.close()
            if encoder.wait(timeout=120):
                raise RuntimeError('Video encoder failed; see workspace log')
        if browser.errors:
            raise RuntimeError('Browser reported JavaScript exceptions')
        if not args.preview:
            metadata={'created_at':datetime.now(timezone.utc).isoformat(),'width':1280,'height':720,
                'duration_s':sum(x['duration'] for x in plan),'captured_browser_frames':frames,'capture_fps':args.fps,
                'output_fps':24,'scenes':plan,'source':'Actual local browser application rendering frozen SUMO FCD; not a screen capture of live road traffic',
                'app_sha256':hashlib.sha256((ROOT/'outputs/app/index.html').read_bytes()).hexdigest(),
                'video_sha256':hashlib.sha256(target.read_bytes()).hexdigest(),'bytes':target.stat().st_size,
                'javascript_exceptions':[],'audio':'none; Chinese on-screen captions'}
            (output/'video_manifest.json').write_text(json.dumps(metadata,ensure_ascii=False,indent=2)+'\n',encoding='utf-8')
            print(json.dumps({'video':str(target.relative_to(ROOT)),'duration_s':metadata['duration_s'],'bytes':metadata['bytes']},ensure_ascii=False))
    finally:
        if encoder and encoder.poll() is None:
            encoder.kill();encoder.wait()
        if browser:browser.close()
        profile=work/'browser'
        if profile.is_dir():shutil.rmtree(profile)
    return 0


if __name__=='__main__':
    raise SystemExit(main())
