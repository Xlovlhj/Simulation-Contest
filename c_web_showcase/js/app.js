// The Glassbox 3D box shell. A box is a list of chapters (js/chapters/*.js).
// Each chapter connects a research question, method and result to a 3D scene,
// with controls, evidence windows and a video storyboard (reel).
import * as THREE from 'three';
import { Stage } from './stage.js';
import { BOX, CHAPTERS } from './chapters/index.js';
import { buildControl, sfx, audio, esc } from './ui.js';

const $ = (s) => document.querySelector(s);
const KEY = `${BOX.slug}.v1`;
const store = { visited: {}, muted: false };
try {
  const saved = JSON.parse(localStorage.getItem(KEY) || '{}');
  if (saved?.visited && typeof saved.visited === 'object') store.visited = saved.visited;
  store.muted = !!saved?.muted;
} catch { /* storage unavailable */ }
const save = () => { try { localStorage.setItem(KEY, JSON.stringify(store)); } catch { /* ignore */ } };
audio.muted = !!store.muted;

let stage;
try { stage = new Stage($('#stage')); } catch (e) { console.error(e); $('#noGL').hidden = false; throw e; }

let ch = null, inst = {}, s = {}, ctrls = [], time = 0, last = performance.now(), tick = 0, reel = null;
let modalFocus = null;

// ---------------------------------------------------------------- chapters
function openChapter(id) {
  const idx = Math.max(0, CHAPTERS.findIndex((c) => c.id === id));
  const c = CHAPTERS[idx];
  inst?.dispose?.();
  stage.clear();
  stage.setWorldMode(false);
  stage.setShift(0, 0);
  closeModal();
  ch = c;
  s = { ...(c.defaults || {}) };
  c.onChange?.(s, null);
  inst = c.build({ stage, s, THREE, openDetail: openModal, label: (html, pos, parent, cls) => stage.label(html, pos, parent, cls) }) || {};
  stage.onPick = inst.pick ? (o) => inst.pick(o) : null;
  stage.setView(c.view.pos, c.view.target, 1.1);
  $('#kicker').textContent = `${c.phase} · ${idx + 1} / ${CHAPTERS.length}`;
  $('#title').textContent = c.title;
  document.title = `${c.title} · ${BOX.title}`;
  if (!reel) history.replaceState(null, '', '#' + c.id);
  store.visited[c.id] = true; save();
  renderNav(); renderPanel(idx);
}

function renderNav() {
  const opened = CHAPTERS.filter((c) => store.visited[c.id]).length;
  $('#menuProgress').textContent = `${opened}/${CHAPTERS.length} 已查看`;
  $('#chapters').innerHTML = CHAPTERS.map((c, i) => {
    const status = c.phase;
    return `<button class="chap${c.id === ch?.id ? ' on' : ''}${store.visited[c.id] ? ' seen' : ''}" data-id="${c.id}" aria-current="${c.id === ch?.id}">
      <span class="n">${i + 1}</span>
      <span class="t"><b>${esc(c.short || c.title)}</b><small class="sub">${esc(c.subtitle || '')}</small><small class="st">${status}</small></span>
    </button>`;
  }).join('');
  $('#chapters .on')?.scrollIntoView({ block: 'nearest', inline: 'center' });
}
$('#chapters').addEventListener('click', (e) => { const b = e.target.closest('.chap'); if (b) openChapter(b.dataset.id); });

function onControl(key, value) { s[key] = value; ch.onChange?.(s, key); ctrls.forEach((c) => c.sync()); inst.update?.(0, s, time); updateReadout(); }
function onAction(item) { item.act?.(s, inst); ch.onChange?.(s, 'action'); ctrls.forEach((c) => c.sync()); inst.update?.(0, s, time); updateReadout(); sfx('click'); }

function renderPanel(idx) {
  const c = ch, prev = CHAPTERS[idx - 1], next = CHAPTERS[idx + 1];
  $('#panel').innerHTML = `
    <p class="kicker">${esc(c.phase)} · ${idx + 1} / ${CHAPTERS.length}</p>
    <h1>${esc(c.title)}</h1>
    <p class="sub">${esc(c.subtitle)}</p>
    ${c.readoutPlacement === 'panel' ? '<section id="panelReadout" class="readout panel-readout" aria-live="polite" hidden></section>' : ''}
    <section class="learn">${c.learn}</section>
    ${c.controls?.length ? `<section><h3>${esc(c.controlsTitle || '查看模型与研究依据')}</h3><div class="controls"></div></section>` : ''}
    ${c.terms?.length ? `<section><h3>${esc(c.termsTitle || '方法与适用条件')}</h3><dl class="terms">${c.terms.map((t) => `<div><dt>${esc(t.t)}</dt><dd>${esc(t.d)}</dd></div>`).join('')}</dl></section>` : ''}
    <nav class="chapnav">${prev ? `<button class="btn ghost" data-go="${prev.id}">← ${esc(prev.short || prev.title)}</button>` : '<span></span>'}${next ? `<button class="btn" data-go="${next.id}">${esc(next.short || next.title)} →</button>` : ''}</nav>`;
  const box = $('#panel .controls');
  ctrls = (c.controls || []).map((d) => buildControl(d, s, onControl, onAction));
  if (box) ctrls.forEach((k) => box.appendChild(k.el));
  $('#panel').querySelectorAll('[data-go]').forEach((b) => b.addEventListener('click', () => { openChapter(b.dataset.go); $('#panel').scrollTop = 0; }));
  $('#panel').scrollTop = 0;
  updateReadout();
}

function updateReadout() {
  const stageReadout = $('#readout');
  const r = ch?.readoutPlacement === 'panel' ? $('#panelReadout') : stageReadout;
  if (r !== stageReadout) {
    stageReadout.hidden = true;
    stageReadout.innerHTML = '';
  }
  if (!r) return;
  const html = inst.readout ? inst.readout(s) : '';
  r.hidden = !html;
  if (html && r.innerHTML !== html) r.innerHTML = html;
}

// ---------------------------------------------------------------- detail windows
function openModal(html) {
  if ($('#modal').hidden) modalFocus = document.activeElement;
  $('#modalCard').innerHTML = `<button class="x" aria-label="关闭窗口">×</button>` + html;
  $('#modalCard').setAttribute('aria-label', '部件与研究说明');
  $('#modal').hidden = false;
  $('#modalCard .x').addEventListener('click', closeModal);
  $('#modalCard .x').focus();
}
function closeModal() {
  const wasOpen = !$('#modal').hidden;
  $('#modal').hidden = true; $('#modalCard').innerHTML = '';
  if (wasOpen && modalFocus?.isConnected) modalFocus.focus();
  modalFocus = null;
}
$('#modal').addEventListener('click', (e) => { if (e.target.id === 'modal') closeModal(); });

// ---------------------------------------------------------------- wiring
const soundBtn = $('#btnSound');
const renderSound = () => { soundBtn.classList.toggle('muted', audio.muted); soundBtn.setAttribute('aria-label', audio.muted ? '开启交互音效' : '关闭交互音效'); };
soundBtn.addEventListener('click', () => { audio.muted = !audio.muted; store.muted = audio.muted; save(); renderSound(); });
renderSound();
$('#aboutProject')?.addEventListener('click', (e) => {
  e.preventDefault();
  openModal(`<p class="kicker">C题成果展示</p><h2>电动汽车能量流与热管理系统建模及节能优化</h2>
    <p>我们研究的是：电动车的电用在了哪里，以及怎样同时调整刹车回收、冷却和空调，让相同的行程更省电。</p>
    <p>先建立动力、电池和温度共同变化的模型，用实测行车记录修正参数，再比较25℃、40℃和−10℃下的控制方案。网页按论文三问展示方法、误差改善和节能结论。</p>
    <p>图表与结论来自论文及支撑材料中的已完成实验；滑条计算当前设定下的力、功率或电池状态。完整行程结果见“节能结论”。天气、车体与电池排布是视觉示意。</p>
    <h3>页面与三维场景来源</h3>
    <p>本地改造沿用 CarClear 的页面结构、三维车辆和部件交互。</p>
    <p><a href="https://glassbox.how/carclear/" target="_blank" rel="noopener noreferrer">CarClear 原网页 ↗</a><br>
    <a href="https://github.com/bdeeps/carclear" target="_blank" rel="noopener noreferrer">原作者源码 ↗</a><br>
    <a href="THIRD_PARTY_NOTICES.md" target="_blank" rel="noopener noreferrer">来源与许可说明</a></p>
    <p class="muted">原作：Deepankar Biswas / Glassbox。代码 MIT；原作内容 CC BY 4.0。研究依据：本工作区 C 题论文与支撑材料。</p>`);
});
$('#reset').addEventListener('click', () => stage.resetView());
stage.onOrbit = () => $('#hint').classList.add('gone');
window.addEventListener('hashchange', () => { const id = location.hash.slice(1); if (id && id !== ch?.id && CHAPTERS.some((c) => c.id === id)) openChapter(id); });
window.addEventListener('keydown', (e) => {
  if (e.key === 'Escape' && !$('#modal').hidden) { closeModal(); return; }
  if (e.key === 'Tab' && !$('#modal').hidden) {
    const focusable = [...$('#modalCard').querySelectorAll('button,a[href],input,select,textarea,[tabindex="0"]')].filter(x => !x.disabled);
    const first = focusable[0], final = focusable.at(-1);
    if (e.shiftKey && document.activeElement === first) { e.preventDefault(); final?.focus(); }
    else if (!e.shiftKey && document.activeElement === final) { e.preventDefault(); first?.focus(); }
  }
  if (/INPUT|TEXTAREA|SELECT/.test(document.activeElement?.tagName) || !$('#modal').hidden) return;
  const i = CHAPTERS.indexOf(ch);
  if (e.key === ']' && CHAPTERS[i + 1]) openChapter(CHAPTERS[i + 1].id);
  if (e.key === '[' && CHAPTERS[i - 1]) openChapter(CHAPTERS[i - 1].id);
});

// ---------------------------------------------------------------- loop
function frameStep(now) {
  const dt = Math.max(0, Math.min(0.05, (now - last) / 1000)); last = now;
  time += dt;
  stage.update(dt);
  inst.update?.(dt, s, time);
  stage.render();
  if (!reel && now - tick > 200) { tick = now; updateReadout(); }
}
function loop(now) { requestAnimationFrame(loop); if (!reel) frameStep(now); }

// ---------------------------------------------------------------- Glassbox director
// The studio records the storyboard (every chapter's `reel` scenes) frame by frame.
const STORYBOARD = CHAPTERS.flatMap((c) => (c.reel || []).map((r) => ({ ...r, chapter: c.id })));
const ease = (k) => k * k * (3 - 2 * k);
const tween = (a, b, k, log) => (typeof a === 'boolean' || typeof a === 'string' ? (k > 0.3 ? b : a) : log ? a * Math.pow(b / a, k) : a + (b - a) * k);
window.glassbox = {
  director: {
    scenes: STORYBOARD.map(({ caption, ms }) => ({ caption, ms })),
    setup({ width, height }) {
      reel = { scene: -1 };
      closeModal();
      document.body.classList.add('gb-reel');
      const host = $('#stage');
      host.style.width = width + 'px'; host.style.height = height + 'px';
      stage.renderer.setPixelRatio(1);
      stage.resize();
    },
    frame(i, t, dtMs = 1000 / 30) {
      const sc = STORYBOARD[i];
      if (reel.scene !== i) {
        const first = reel.scene < 0;
        reel.scene = i;
        if (first || ch?.id !== sc.chapter) openChapter(sc.chapter);
        Object.assign(s, sc.set);
        ch.onChange?.(s, null);
        if (sc.act) sc.act(s, inst);
        if (sc.view) stage.setView(sc.view.pos, sc.view.target, 1.2);
        stage.controls.autoRotate = sc.spin !== 0;
        stage.controls.autoRotateSpeed = sc.spin ?? 0.5;
      }
      const k = ease(Math.min(1, Math.max(0, (t - 0.1) / 0.75)));
      for (const [key, [a, b, log]] of Object.entries(sc.anim || {})) { s[key] = tween(a, b, k, log); ch.onChange?.(s, key); }
      frameStep(last + dtMs);
      return { main: stage.renderer.domElement };
    },
  },
};
window[BOX.slug] = {
  stage, ready: true, openChapter, setControl: onControl, openDetail: openModal,
  get settings() { return s; }, get chapter() { return ch; }, get instance() { return inst; },
  step(n = 1, ms = 16) { let t = last; for (let k = 0; k < n; k++) frameStep((t += ms)); },
};
window.carclear = window[BOX.slug];
window.evLab = window[BOX.slug];

openChapter(location.hash.slice(1) || CHAPTERS[0].id);
requestAnimationFrame(loop);
