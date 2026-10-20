// Original CarClear chapter contract: car scene + floating chart + direct controls.
// Fixed research comparisons only. No trajectory player and no fabricated interpolation.
import { THREE, canvasTexture } from '../kit.js';
import { board, plotFrame } from '../car.js';
import { makeResearchVehicle } from '../research/vehicle.js';
import { RESULTS, TEMPERATURES, comparison } from '../research/results.js';
import { RULES } from '../research/physics.js';

const COLORS = { baseline: '#ffb547', optimized: '#5ce1a9', faint: 'rgba(232,238,248,.6)' };
const scopeName = { control: '单循环', sameTarget: '同24℃目标', long: '完整行程' };
const f = (x, n = 3) => Number(x).toFixed(n);
const facts = state => comparison(state.ambient, state.scope);
const selectedRule = state => {
  const item = facts(state);
  return (state.strategy === 'baseline' ? item.baseline?.rule : item.optimized?.rule)
    || RULES[state.strategy];
};

function staticCurve({ title, series, yMin, yMax, yLabel, yTicks }) {
  const data = RESULTS.q1StaticCurve, width = 640, height = 245;
  const left = 65, right = 20, top = 40, bottom = 48;
  const X = time => left + time / 1800 * (width - left - right);
  const Y = value => height - bottom - (value - yMin) / (yMax - yMin) * (height - top - bottom);
  const paths = series.map(s => {
    const column = data.fields.indexOf(s.field);
    const points = data.rows.map((row, i) =>
      `${i === 0 ? 'M' : 'L'}${X(row[0]).toFixed(2)},${Y(row[column] * (s.scale || 1)).toFixed(2)}`).join(' ');
    return `<path d="${points}" fill="none" stroke="${s.color}" stroke-width="2.5"/>`;
  }).join('');
  const horizontal = yTicks.map(t =>
    `<line x1="${left}" y1="${Y(t)}" x2="${width - right}" y2="${Y(t)}" stroke="#ffffff18"/><text x="${left - 9}" y="${Y(t) + 5}" text-anchor="end">${t}</text>`).join('');
  const ticks = [0, 5, 10, 15, 20, 25, 30].map(t =>
    `<text x="${X(t * 60)}" y="${height - bottom + 23}" text-anchor="middle">${t}</text>`).join('');
  return `<figure style="margin:18px 0">
    <svg viewBox="0 0 ${width} ${height}" role="img" aria-label="${title}，25℃CLTC参考模型归档结果"
      style="display:block;width:100%;background:#080a10;border:1px solid #ffffff20;border-radius:10px">
      <g font-family="sans-serif" font-size="14" fill="#c5d0df">
        <text x="16" y="23" fill="#e8eef8" font-size="17">${title}</text>
        <text x="${left}" y="${top - 7}" font-size="12">${yLabel}</text>
        ${horizontal}${ticks}${paths}
        <text x="${(left + width - right) / 2}" y="${height - 8}" text-anchor="middle">时间 / min</text>
      </g>
    </svg>
    <figcaption style="font-size:12px;color:#a5b2c2;margin-top:5px">
      ${series.map(s => `<span style="color:${s.color}">● ${s.label}</span>`).join('　')}
      · 归档基准结果静态图，100个展示点，含初态与全局极值。
    </figcaption>
  </figure>`;
}

function baselineEvidence() {
  const q = RESULTS.q1;
  const sensitivity = RESULTS.sensitivity.filter(r => r.fractional_perturbation === 0.1)
    .sort((a, b) => a.energy_rank - b.energy_rank);
  return `<h2>Q1：基准模型与七参数灵敏度</h2>
    <p>25℃、1800秒CLTC，参考Rint、160Ah、初始SOC80%。这是独立的Q1基准，不能与Q3校准模型直接拼成节能对照。</p>
    <table><tbody>
      <tr><th>行驶距离</th><td>${f(q.distance_km, 5)} km</td></tr>
      <tr><th>净端电耗</th><td>${f(q.net_energy_kwh, 6)} kWh</td></tr>
      <tr><th>百公里电耗</th><td>${f(q.consumption_kwh_100km, 4)} kWh/100km</td></tr>
      <tr><th>终SOC</th><td>${f(q.soc_final * 100, 4)}%</td></tr>
      <tr><th>电池峰值温度</th><td>${f(q.battery_max_c, 4)}℃（仿真）</td></tr>
    </tbody></table>
    <h3>归档基准结果：SOC与温度</h3>
    ${staticCurve({ title: '参考模型的SOC变化', series: [{ field: 'soc', scale: 100, color: '#5ce1a9', label: 'SOC' }],
      yMin: 76, yMax: 80, yTicks: [76, 77, 78, 79, 80], yLabel: 'SOC / %' })}
    ${staticCurve({ title: '集总温度变化（仿真）', series: [
      { field: 'battery_c', color: '#ffb547', label: '电池' },
      { field: 'cabin_c', color: '#8ef0ff', label: '座舱' }],
      yMin: 25, yMax: 25.5, yTicks: [25, 25.1, 25.2, 25.3, 25.4, 25.5], yLabel: '温度 / ℃' })}
    <p>曲线直接摘自归档 <code>scenario_A_reference.csv</code>，原始3600步；只作静态结果分析，不随交互滑条改变，也不是新的完整循环求解。</p>
    <h3>电池端能量去向</h3>
    <table><thead><tr><th>DC支路</th><th>电量/kWh</th></tr></thead><tbody>
      ${q.dc_branches.map(r => `<tr><td>${r.branch}</td><td>${f(r.energy_kwh, 6)}</td></tr>`).join('')}
    </tbody></table>
    <p>回流支路用负号表示；电机、减速器、逆变器等内部损耗不能再加一遍到DC总支路中。</p>
    <h3>七参数灵敏度：±10%扰动</h3>
    <table><thead><tr><th>参数</th><th>端电耗灵敏度</th><th>排序</th></tr></thead><tbody>
      ${sensitivity.map(r => `<tr><td>${r.label}</td><td>${f(r.normalized_energy_sensitivity, 4)}</td><td>${r.energy_rank}</td></tr>`).join('')}
    </tbody></table>
    <p>表中为归一化端电耗灵敏度，正负表示变化方向；质量、滚阻、风阻影响较大。内阻与热容对这一端电耗口径为零，不等于它们对发热、SOC或其他场景没有影响。</p>
    <p>来源：<code>results/tables/scenario_A_summary.json</code>、<code>scenario_A_dc_energy.csv</code>、<code>scenario_A_sensitivity.csv</code>。温度为集总模型输出，未取得同车实测温度验证。</p>`;
}

function evidence(state) {
  const item = facts(state);
  const ref = RESULTS.calibration.find(r => r.trip_id === 588 && r.variant === 'reference');
  const calibrated = RESULTS.calibration.find(r => r.trip_id === 588 && r.variant === 'joint_calibrated');
  const estimates = RESULTS.parameters.calibrated;
  const groups = ['train', 'validation', 'test_retrospective'].map(role => {
    const before = RESULTS.calibrationGroups.find(r => r.role === role && r.variant === 'reference');
    const after = RESULTS.calibrationGroups.find(r => r.role === role && r.variant === 'joint_calibrated');
    const name = { train: '训练575/596', validation: '验证588', test_retrospective: '回顾比较600' }[role];
    return `<tr><td>${name}</td><td>${f(before.energy_mape_pct)}%</td><td>${f(after.energy_mape_pct)}%</td></tr>`;
  }).join('');
  const intervals = RESULTS.intervals.filter(r => r.block_length_s === 40 &&
    ['drag_coefficient', 'resistance_ref_ohm'].includes(r.parameter));
  const rows = TEMPERATURES.map(t => {
    const r = comparison(t, state.scope);
    return `<tr><td>${t}℃</td><td>${f(r.baselineEnergyKwh)}</td><td>${f(r.optimizedEnergyKwh)}</td><td>${f(r.savingPct)}%</td></tr>`;
  }).join('');
  return `<h2>本章数值从哪里来</h2>
    <p>当前为<b>${state.ambient}℃ · ${scopeName[state.scope]}</b>。这些数值来自已经完成的固定实验；前几章的滑条只做给定状态的机理计算，不改写本页结果。</p>
    <h3>同一条件下比较策略</h3>
    <p>校准Rint模型，初始SOC94%，初始座舱24℃；相同工况和约束。采用72组有限规则搜索，不宣称连续空间全局最优。${state.scope === 'long' ? '此处为90分钟驾驶加120分钟共同停车温控恢复。' : '此处为1800秒CLTC。'}</p>
    ${state.scope === 'sameTarget' ? '<p>同目标对照保留选定回收与冷却设置，仅把座舱经济偏置由1.5℃改为0℃，共同目标24℃；这次核查没有重新寻优。</p>' : ''}
    <table><thead><tr><th>环境</th><th>基准kWh</th><th>优化kWh</th><th>${item.accountingLabel}</th></tr></thead><tbody>${rows}</tbody></table>
    <p>${state.scope === 'long' ? '图中比较包含停车实际继续仿真的总端电耗。'
      : '图中柱高比较基准端电耗与优化末端热修正能耗；表内“优化kWh”仍为修正前实际端电耗。两者口径明确区分。'}</p>
    <h3>参数校准：改善什么</h3>
    <p>两段训练路程575/596用于拟合；588为验证；600为回顾比较，不称新盲测。</p>
    <p>联合估计：风阻系数 <b>Cd=${f(estimates.drag_coefficient, 6)}</b>，滚阻系数 <b>Cr=${f(estimates.rolling_coefficient, 6)}</b>，参考内阻 <b>R=${f(estimates.resistance_ref_ohm, 6)}Ω</b>。同时调整容量${f(estimates.capacity_ah, 4)}Ah与开路电压偏移${f(estimates.ocv_offset_v, 4)}V，不是只拟合三个参数。</p>
    <p>588验证路程：SOC RMSE ${f(ref.soc_rmse_pp)} → ${f(calibrated.soc_rmse_pp)}个百分点；能量APE ${f(ref.energy_ape_pct)}% → ${f(calibrated.energy_ape_pct)}%。电压RMSE ${f(ref.voltage_rmse_v)} → ${f(calibrated.voltage_rmse_v)}V，未同步改善。</p>
    <table><thead><tr><th>数据组</th><th>校准前能量MAPE</th><th>校准后能量MAPE</th></tr></thead><tbody>${groups}</tbody></table>
    <p>MAPE为组内逐路程能量APE的平均；只有一段路程的验证组，其MAPE与该段APE数值相同。</p>
    <p>40秒分块、95%条件近似区间：${intervals.map(r =>
      `${r.parameter === 'drag_coefficient' ? '风阻系数Cd' : '参考内阻R/Ω'}：${f(r.ci_low, 4)}–${f(r.ci_high, 4)}`).join('；')}。局部线性化分块重采样，并非完整重拟合；滚阻辨识仍不稳定。</p>
    <h3>Q1基准与控制基准分别标注</h3>
    <p>Q1采用参考Rint／160Ah／初始SOC80%，25℃CLTC的电耗为${f(RESULTS.q1.consumption_kwh_100km, 4)}kWh/100km；不能直接作为本页Q3优化对照的分母。</p>
    <h3>来源与边界</h3>
    <p>控制：<code>results/tables/control_evaluation.json</code><br>同目标：<code>records/revision_20261019/results.json</code><br>长程：<code>results/tables/durability_comparisons.csv</code><br>校准：<code>results/tables/calibration_metrics.csv</code></p>
    <p>温度与控制效果为仿真证据，未取得同车独立实测温度；预先把座舱调到24℃的耗电未计入。部分鲁棒性扰动案例不满足全部约束。车辆部件位置为示意。</p>`;
}

export default {
  id: 'research-results',
  short: '节能结论',
  title: '停车后，还能省多少电',
  subtitle: '90分钟驾驶，再加120分钟停车温控，比较完整过程。',
  controlsTitle: '查看研究对照',
  termsTitle: '结果的比较口径',
  readoutPlacement: 'panel',
  view: { pos: [8.4, 6.8, 11.6], target: [0.5, 2.4, 0] },
  learn: `<p class="research-takeaway"><b>完整过程的总电耗降低</b><br>
      常温25℃ <b>${f(comparison(25, 'long').savingPct, 2)}%</b>　高温40℃ <b>${f(comparison(40, 'long').savingPct, 2)}%</b><br>
      低温−10℃ <b>${f(comparison(-10, 'long').savingPct, 2)}%</b></p>
    <div class="research-block"><b>怎样公平比较</b><p>两种方案使用同一辆模型车，按同一份车速表、从相同电量和温度出发。停车后继续用同一套温控规则，检查行驶时省下的电，会不会又花在停车冷却上。</p></div>
    <div class="research-block"><b>研究得到什么</b><p>常温和高温仍有较明显收益；低温的回收受限，座舱取暖占用更多电量。同24℃目标的补充对照支持常温、高温收益主要来自回收和冷却设置。</p></div>
    <p class="muted">这些是已有仿真结果；预调温耗电未计，温控效果仍需同车实测验证。</p>`,
  terms: [
    { t: '同条件对照', d: '两策略使用同一模型、工况和初态，避免把车辆或电池容量变化误当节能。' },
    { t: '末端热修正', d: '对结束时不同的热状态进行能量核算；它不是已经实际发生的停车过程。' },
    { t: '完整行程', d: '90分钟驾驶，再继续模拟120分钟共同停车温控，把后续耗电一起比较。' },
  ],
  defaults: { ambient: 25, scope: 'long', strategy: 'optimized' },
  controls: [
    { key: 'ambient', type: 'seg', label: '环境温度', options: [
      { v: 25, label: '25℃' }, { v: 40, label: '40℃' }, { v: -10, label: '−10℃' },
    ] },
    { key: 'scope', type: 'seg', label: '比较范围', options: [
      { v: 'control', label: '单循环' }, { v: 'sameTarget', label: '同目标' }, { v: 'long', label: '完整行程' },
    ] },
    { key: 'strategy', type: 'seg', label: '查看策略', options: [
      { v: 'baseline', label: '基准' }, { v: 'optimized', label: '优化' },
    ] },
    { type: 'buttons', label: '查阅结果依据', items: [
      { label: '公平对照、验证与来源', act: (state, inst) => inst.showEvidence(state) },
      { label: 'Q1基准与七参数灵敏度', act: (state, inst) => inst.showBaselineEvidence() },
    ] },
  ],
  reel: [
    { ms: 7000, caption: '相同工况与初态下，比较基准策略和优化策略。', set: { ambient: 25, scope: 'control', strategy: 'optimized' } },
    { ms: 7000, caption: '把停车温控算进去，常温和高温仍有收益；低温收益较小。', set: { ambient: 40, scope: 'long', strategy: 'optimized' } },
  ],
  build({ stage, openDetail }) {
    const root = new THREE.Group(); stage.root.add(root);
    const vehicle = makeResearchVehicle(stage, { parent: root, opacity: 0.12 });
    vehicle.setFlow(0);
    const state = { ambient: 25, scope: 'long', strategy: 'optimized' };
    const chart = canvasTexture(1000, 600, (g, w, h) => {
      const pairs = TEMPERATURES.map(t => comparison(t, state.scope));
      const ymax = state.scope === 'long' ? 16 : 4;
      const p = plotFrame(g, w, h, {
        x0: 0, x1: 3, y0: 0, y1: ymax, left: 90, right: 35, top: 110, bottom: 130,
        xTicks: [0.5, 1.5, 2.5], xFmt: x => `${TEMPERATURES[Math.floor(x)]}℃`,
        yTicks: state.scope === 'long' ? [0, 4, 8, 12, 16] : [0, 1, 2, 3, 4],
        title: `${scopeName[state.scope]} · 同条件能耗比较`,
        xLabel: '环境温度', yLabel: '电能 / kWh',
      });
      g.font = '21px sans-serif'; g.textAlign = 'left';
      g.fillStyle = COLORS.baseline; g.fillText('■ 基准', 90, 78);
      g.fillStyle = COLORS.optimized; g.fillText(state.scope === 'long' ? '■ 优化总端电耗' : '■ 优化（含末端热修正）', 240, 78);
      pairs.forEach((r, i) => {
        const selected = r.ambient_c === Number(state.ambient);
        const width = 52;
        const x = p.X(i + 0.5);
        if (selected) {
          g.fillStyle = 'rgba(142,240,255,.07)';
          g.fillRect(p.X(i) + 8, p.Y(ymax), p.X(i + 1) - p.X(i) - 16, p.Y(0) - p.Y(ymax));
        }
        const vals = [r.baselineEnergyKwh, r.correctedEnergyKwh];
        vals.forEach((value, j) => {
          const bx = x + (j === 0 ? -width - 5 : 5);
          g.globalAlpha = selected ? 1 : 0.68;
          g.fillStyle = j === 0 ? COLORS.baseline : COLORS.optimized;
          g.fillRect(bx, p.Y(value), width, p.Y(0) - p.Y(value));
          g.globalAlpha = 1;
          g.font = 'bold 21px sans-serif'; g.textAlign = 'center'; g.fillText(f(value, 2), bx + width / 2, p.Y(value) - 12);
          if (selected && ((state.strategy === 'baseline') === (j === 0))) {
            g.strokeStyle = '#ffffff'; g.lineWidth = 2; g.strokeRect(bx - 3, p.Y(value) - 3, width + 6, p.Y(0) - p.Y(value) + 3);
          }
        });
        g.fillStyle = selected ? '#e8eef8' : COLORS.faint; g.font = 'bold 23px sans-serif';
        g.textAlign = 'center'; g.fillText(`节能 ${f(r.savingPct, 2)}%`, x, h - 55);
      });
    });
    const panel = board(chart.tex, 7.1, 4.26);
    panel.position.set(1.8, 5.9, -3.2); panel.rotation.y = -0.24; root.add(panel);
    const caption = stage.label('论文固定实验 · 不由滑条重算', [0.1, 4.15, 0], root, 'hot');
    let previous = '';
    return {
      update(dt, s) {
        const key = `${s.ambient}|${s.scope}|${s.strategy}`;
        if (key !== previous) {
          Object.assign(state, s); chart.redraw(); previous = key;
          caption.element.textContent = `${s.ambient}℃ · ${scopeName[s.scope]} · 论文固定实验`;
          const r = selectedRule(s);
          vehicle.modules.material.color.set(s.strategy === 'optimized' ? 0x3d8065 : 0x7c6339);
          vehicle.modules.material.emissive.set(s.strategy === 'optimized' ? 0x174d36 : 0x392916);
          // The color identifies strategy; it is not a spatial thermal field.
          vehicle.motor.userData.regenFraction = r.regen_fraction;
        }
        vehicle.updatePose(dt, 0);
      },
      readout(s) {
        const item = facts(s), rule = selectedRule(s);
        const shownEnergy = s.strategy === 'baseline' ? item.baselineEnergyKwh : item.optimizedEnergyKwh;
        return `<div class="big">节能 ${f(item.savingPct)}%</div>
          <div class="row"><span>${s.strategy === 'baseline' ? '基准' : '优化'}用电量</span><b>${f(shownEnergy)} kWh</b></div>
          <div class="row"><span>对照范围</span><b>${item.durationLabel}</b></div>
          <div class="row"><span>回收请求比例</span><b>${Math.round(rule.regen_fraction * 100)}%</b></div>
          <div class="row"><span>冷却开启设定</span><b>${rule.cooling_on_c}℃</b></div>
          ${s.scope === 'sameTarget' ? '<div class="row"><span>车内目标温度调整</span><b>0℃ · 沿用已选规则</b></div>' : ''}
          <small>${item.accountingLabel}；校准模型，起始电量94%、车内24℃。冷却还受安全保护和启停温差规则影响；数据来自已完成仿真。</small>`;
      },
      showEvidence(s) { openDetail?.(evidence(s)); },
      showBaselineEvidence() { openDetail?.(baselineEvidence()); },
      dispose() { vehicle.dispose(); },
    };
  },
};
