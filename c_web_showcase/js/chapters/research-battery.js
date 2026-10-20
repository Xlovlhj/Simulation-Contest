// Stand-alone inspection of CarClear's original flat battery pack.
import { THREE, M, box, clamp, approach, canvasTexture } from '../kit.js';
import { rbox, pathOf, flowDots, board, plotFrame } from '../car.js';
import { makeResearchBattery } from '../research/vehicle.js';
import { batteryAt } from '../research/physics.js';
import { RESULTS } from '../research/results.js';

const selectedPower = (s) => (s.mode === 'charge' ? -1 : s.mode === 'discharge' ? 1 : 0) * s.power * 1000;
const snapshot = (s) => batteryAt({ soc: s.soc / 100, powerW: selectedPower(s), batteryC: 25, model: 'calibrated' });
const fmt = (v, digits = 1) => Number.isFinite(v) ? v.toFixed(digits) : '—';
const before = RESULTS.calibration.find(r => r.trip_id === 588 && r.variant === 'reference');
const after = RESULTS.calibration.find(r => r.trip_id === 588 && r.variant === 'joint_calibrated');

function calibrationEvidence() {
  const p = RESULTS.parameters.calibrated;
  const intervals = RESULTS.intervals.filter(r => r.block_length_s === 40);
  const names = {
    drag_coefficient: '空气阻力系数 Cd', rolling_coefficient: '滚动阻力系数 Cr',
    resistance_ref_ohm: '参考内阻 R / Ω', capacity_ah: '有效容量 Q / Ah', ocv_offset_v: '开路电压偏移 / V',
  };
  const rows = intervals.map(r => `<tr><td>${names[r.parameter]}</td><td>${fmt(r.estimate, 6)}</td><td>${fmt(r.ci_low, 6)} ～ ${fmt(r.ci_high, 6)}</td></tr>`).join('');
  const groups = ['train', 'validation', 'test_retrospective'].map(role => {
    const initial = RESULTS.calibrationGroups.find(r => r.role === role && r.variant === 'reference');
    const fitted = RESULTS.calibrationGroups.find(r => r.role === role && r.variant === 'joint_calibrated');
    const label = { train: '训练575、596', validation: '验证588', test_retrospective: '回顾比较600' }[role];
    return `<tr><td>${label}</td><td>${fmt(initial.energy_mape_pct, 3)}%</td><td>${fmt(fitted.energy_mape_pct, 3)}%</td></tr>`;
  }).join('');
  return `<p class="kicker">问题二 · 数据校准与参数辨识</p><h2>用行驶记录反推模型参数</h2>
    <p>直观地说，我们让模型按实测车速走一遍，再看预测的电量、电压和用电量与记录差多少。不断调整模型中的参数，让这些误差共同减小。</p>
    <h3>先拟合，再用另一段路检查</h3>
    <p>575、596两段路程用于校准；固定参数后，用588路程验证。600路程此前已经参与过模型比较，因此保留“回顾比较”身份，不称新盲测。校准时，记录中的空调和加热功率作为已知负载输入。</p>
    <h3>求解方法与输入输出</h3>
    <p>将实测车速插值到1秒网格，并作标准差1秒的高斯平滑；SOC、电压误差仍在原观测时刻比较，按观测代表的持续时间加权。</p>
    <p>目标函数同时考虑四项误差：SOC、电压、10秒平均功率、整段净端电能。分别用0.01的SOC比例、3V、5000W和实测总能量的5%归一化，避免只追求某一项。</p>
    <p>采用<b>有界非线性最小二乘法</b>，从两个初值求解，保留目标函数较小的一组。共同估计Cd、Cr、参考内阻R，同时调整容量Q和开路电压偏移；不为验证路程单独调整容量。</p>
    <h3>参数估计与不确定性</h3>
    <table><thead><tr><th>参数</th><th>估计值</th><th>95%条件近似区间</th></tr></thead><tbody>${rows}</tbody></table>
    <p>此表取40秒分块、1000次局部线性化分块重采样；另有20秒、80秒分块对照。区间没有对每次样本重新拟合，不能当作完整后验概率。滚阻区间跨过零表示当前数据下难以稳定区分参数，不表示允许负滚阻。</p>
    <h3>验证结果改善了哪些指标</h3>
    <p>588路程的SOC RMSE由${fmt(before.soc_rmse_pp, 3)}降至${fmt(after.soc_rmse_pp, 3)}个百分点；单路程能量APE由${fmt(before.energy_ape_pct, 3)}%降至${fmt(after.energy_ape_pct, 3)}%。</p>
    <table><thead><tr><th>数据组</th><th>校准前能量MAPE</th><th>校准后能量MAPE</th></tr></thead><tbody>${groups}</tbody></table>
    <p>APE是单段路程的相对误差绝对值；MAPE是组内各段APE的平均。588组只有一段路程，因此两者数值相同。</p>
    <h3>仍需保留的边界</h3>
    <p>588的电压RMSE从${fmt(before.voltage_rmse_v, 3)}V升至${fmt(after.voltage_rmse_v, 3)}V，说明改善SOC与能耗并没有让全部指标同时改善。风阻和滚阻仍会相互补偿；热状态缺少独立实测温度核验。论文的一阶RC补充用于回顾性电压比较，问题三主控制仍采用校准Rint。</p>
    <p>画面中的滑条查看校准后参数在给定电量、功率下的电学关系，不重新执行参数拟合，也不改写验证指标。当前有效容量为${fmt(p.capacity_ah, 4)}Ah，与Q1的160Ah参考基准分别使用。</p>
    <p>依据：论文第5.1–5.7节；<code>configs/calibration_protocol.json</code>、<code>src/bev/calibration.py</code>、<code>results/tables/calibration_metrics.csv</code>、<code>calibration_group_metrics.csv</code>、<code>parameter_intervals.csv</code>。</p>`;
}

export default {
  id: 'battery',
  short: '用实测校准',
  title: '让预测更接近真实行驶',
  subtitle: '问题二：用行驶数据反推参数，再用另一段路验证。',
  controlsTitle: '查看校准后模型',
  termsTitle: '校准与验证的含义',
  view: { pos: [7.8, 6.8, 10.2], target: [0.6, 2.5, 0] },
  learn: `<p class="research-takeaway"><b>验证路程（588）：预测更接近实测</b><br>
      用电量误差 <b>${fmt(before.energy_ape_pct, 2)}% → ${fmt(after.energy_ape_pct, 3)}%</b><br>
      剩余电量比例误差 <b>${fmt(before.soc_rmse_pp, 3)} → ${fmt(after.soc_rmse_pp, 3)}个百分点</b></p>
    <div class="research-block"><b>我们怎么做</b><p>先用两段实测路程（575、596）调整空气阻力、轮胎滚动阻力和电池内部阻力，并一起估计容量与电压偏移。参数定下来后，再用另一段路程（588）检查预测。</p></div>
    <div class="research-block"><b>参数怎样得到</b><p>让模型按实测车速运行，同时减少电量、电压、平均功率和总能量的误差。画面展示校准后电池模型的状态关系。</p></div>
    <p class="muted">剩余电量与耗电预测改善了，电压预测误差却变大了；具体数值与参数范围见“校准依据”。</p>`,
  defaults: { explode: 0, soc: 80, mode: 'discharge', power: 20 },
  controls: [
    { key: 'explode', type: 'range', label: '拆开模块与电芯', min: 0, max: 1, step: 0.01,
      ends: ['组装', '展开'], fmt: (v) => Math.round(v * 100) + '%' },
    { key: 'soc', type: 'range', label: 'SOC · 剩余电量比例', min: 5, max: 95, step: 1,
      fmt: (v) => Math.round(v) + '%' },
    { key: 'mode', type: 'seg', label: '能量方向', options: [
      { v: 'discharge', label: '放电' }, { v: 'charge', label: '充电' }, { v: 'rest', label: '静置' },
    ] },
    { key: 'power', type: 'range', label: '给定功率 · Rint 示意', min: 0, max: 60, step: 1,
      fmt: (v) => Math.round(v) + ' kW' },
    { type: 'buttons', label: '查阅方法与验证', items: [
      { label: '校准依据与参数区间', act: (s, inst) => inst.showCalibration() },
    ] },
  ],
  terms: [
    { t: '参数反推', d: '把实测记录与模型预测放在一起，寻找能共同减小各项误差的参数。采用有界非线性最小二乘法。' },
    { t: '验证误差', d: '588路程不参与这次拟合。SOC用RMSE，单位为百分点；单路程能量用APE，单位为%。' },
    { t: '参数区间', d: '用分块重采样近似检查参数有多不确定。风阻和滚阻仍存在相互补偿，不能只看一个最优数值。' },
  ],
  reel: [{
    ms: 6000, caption: '用两段实测路程校准后，验证路程的能量误差从16.48%降至0.826%。',
    set: { mode: 'discharge', power: 20 }, anim: { explode: [0, 0.9], soc: [80, 45] },
    view: { pos: [8.2, 7.0, 11.2], target: [0.6, 2.6, 0] }, spin: 0.15,
  }],
  build({ stage, openDetail }) {
    const root = new THREE.Group();
    stage.root.add(root);
    const { pack, modules, casing } = makeResearchBattery();
    pack.position.set(-0.6, 1.2, 0);
    root.add(pack);
    // Replace one visible module by a separately inspectable copy at the same home.
    modules.place(23, [2.25, 0, 1.2], null, 0);
    modules.done();
    const module = new THREE.Group();
    module.position.set(2.25, 0, 1.2);
    pack.add(module);
    const shell = rbox(0.78, 0.3, 0.72, 0.04, M.clear(0x65cba1, 0.48));
    module.add(shell);
    const lid = box(0.78, 0.035, 0.72, M.plastic(0x2f6f5a, {
      emissive: new THREE.Color(0x0e3a2c), emissiveIntensity: 0.9,
    }));
    lid.position.y = 0.17;
    module.add(lid);
    const cells = [];
    for (let i = 0; i < 6; i++) {
      const cell = rbox(0.09, 0.2, 0.60, 0.015,
        M.metal(0x9db8ac, { metalness: 0.45, roughness: 0.45 }));
      cell.position.set(-0.29 + i * 0.115, 0, 0);
      const tab = box(0.028, 0.04, 0.08, M.metal(i % 2 ? 0xc98952 : 0xe1d7aa));
      tab.position.set(0, 0.12, -0.19);
      cell.add(tab);
      module.add(cell);
      cells.push(cell);
    }
    // Six visible cells are structural illustration, not a claimed cell topology.
    const load = rbox(0.85, 0.65, 1.0, 0.08, M.metal(0x7d899c));
    load.position.set(-4.4, 1.2, 0);
    root.add(load);
    const flow = flowDots(pathOf([[-2.6, 1.34, 0], [-3.25, 1.34, 0], [-4.0, 1.34, 0]]),
      14, 0.07, 0xffb547);
    root.add(flow);
    const lSOC = stage.label('SOC 80%', [0.2, -0.4, 1.9], pack, 'hot');
    const lModule = stage.label('模块 → 电芯', [0, 0.7, 0.5], module);
    const lLoad = stage.label('向用电设备供电', [0, -0.6, 0.6], load);
    const lSchematic = stage.label('模块与电芯排布为结构示意', [1.4, 0.1, -2.1], root);
    lSchematic.element.style.opacity = '0.62';
    const cur = { soc: 80 };
    const minV = Math.floor(batteryAt({ soc: 0, model: 'calibrated' }).ocvV / 20) * 20;
    const maxV = Math.ceil(batteryAt({ soc: 1, model: 'calibrated' }).ocvV / 20) * 20;
    const chart = canvasTexture(900, 560, (g, w, h) => {
      const p = plotFrame(g, w, h, {
        x0: 0, x1: 100, y0: minV, y1: maxV, left: 82, right: 32, top: 86, bottom: 62,
        xTicks: [0, 20, 40, 60, 80, 100],
        yTicks: Array.from({ length: Math.floor((maxV - minV) / 40) + 1 }, (_, i) => minV + i * 40),
        title: '校准后的电池模型：SOC与开路电压', xLabel: 'SOC (%)', yLabel: '开路电压 (V)',
      });
      const ocv = (soc) => batteryAt({ soc: soc / 100, model: 'calibrated' }).ocvV;
      p.line(ocv, 0, 100, '#5ce1a9', 5);
      p.vline(cur.soc);
      p.dot(cur.soc, ocv(cur.soc), '#ffb547');
      p.text(`${cur.soc.toFixed(0)}% → ${ocv(cur.soc).toFixed(1)} V`, 470, 126, '#e8eef8');
      p.text('25 °C · 辨识后参数', 470, 160, '#92a6bb', '20px sans-serif');
    });
    const chartBoard = board(chart.tex, 5.8, 3.61);
    chartBoard.position.set(-0.4, 5.0, -2.7);
    chartBoard.rotation.y = -0.25;
    root.add(chartBoard);
    [casing, modules, shell, lid, ...cells].forEach((obj) => {
      obj.userData.batteryPart = true;
      stage.pickables.push(obj);
    });
    let split = 0, lastKey = '';
    return {
      update(dt, s) {
        split = approach(split, s.explode, 5, Math.max(0, dt));
        module.position.set(2.25 + split * 1.1, split * 1.7, 1.2 + split * 0.35);
        module.scale.setScalar(1 + split * 0.8);
        lid.position.y = 0.17 + split * 0.9;
        cells.forEach((cell, i) => {
          cell.position.set((-0.29 + i * 0.115) * (1 + split * 1.6), split * 0.1, 0);
        });
        casing.material.opacity = 0.2 - split * 0.1;
        lModule.visible = split > 0.15;
        lSchematic.visible = stage.host.clientWidth >= 560;
        const p = snapshot(s);
        flow.visible = Math.abs(selectedPower(s)) > 1 && p.feasible;
        if (flow.visible) {
          const charge = s.mode === 'charge';
          flow.material.color.set(charge ? 0x5ce1a9 : 0xffb547);
          flow.step(Math.max(0, dt), (charge ? -1 : 1) * (0.4 + s.power / 30));
        }
        const key = `${s.soc}|${s.mode}|${s.power}|${p.feasible}`;
        if (key !== lastKey) {
          lastKey = key;
          cur.soc = clamp(s.soc, 0, 100);
          chart.redraw();
          lSOC.element.textContent = `SOC ${s.soc.toFixed(0)}%`;
          lLoad.element.textContent = !p.feasible ? '超出电池模型边界' :
            s.mode === 'charge' ? '外部充电输入' :
            s.mode === 'discharge' ? '向用电设备供电' : '静置 · 无电流';
        }
      },
      pick() {
        const s = snapshot({ soc: cur.soc, mode: 'rest', power: 0 });
        openDetail?.(`<p class="kicker">电池结构与电学模型</p><h2>电芯、模块、电池包</h2>
          <p>多个电芯组成模块，模块再组合为电池包。画面保留原 CarClear 的模块造型，展开的 6 个电芯用于说明层次关系，数量与排布不代表研究车辆的实际装配。</p>
          <h3>论文怎样计算电池</h3><p>开路电压由 SOC–OCV 曲线确定，端电压满足 <b>U = U<sub>oc</sub> − IR</b>，负载功率满足 <b>P = UI</b>。电流取正表示放电，取负表示充电。</p>
          <p>内阻发热为 <b>I²R</b>。SOC 则由电流随时间积分得到；此页固定在滑条选中的电量状态，便于观察关系。SOC 低于 10% 或高于 90% 时，内阻修正系数使用数据表最近端点的值。</p>
          <p>0–60 kW 滑条是给定功率下的 Rint 电学示意，没有加入 BMS 充电限值、充电机能力或充电协议，不能据此推断车辆的实际充电能力。</p>
          <p>当前 SOC 下，Q × 额定电压 × SOC 的标称剩余能量约为 <b>${fmt(s.nominalRemainingKwh)} kWh</b>。这是标称估算，不是工况续航预测。</p>`);
      },
      readout(s) {
        const p = snapshot(s);
        if (!p.feasible) return `<div class="big">当前状态超出边界</div><small>${p.reason}</small>`;
        const state = s.mode === 'charge' ? '充电' : s.mode === 'discharge' ? '放电' : '静置';
        return `<div class="big">${state} · ${fmt(Math.abs(selectedPower(s)) / 1000)} kW</div>
          <div class="row"><span>端电压 / 电流</span><b>${fmt(p.voltageV)} V / ${fmt(p.currentA)} A</b></div>
          <div class="row"><span>内阻发热</span><b>${fmt(p.jouleHeatW)} W</b></div>
          <small>校准后参数 · 25℃给定状态${p.resistanceEndpointHold ? ' · 内阻按表端值保持' : ''}；此交互不改写验证结果。</small>`;
      },
      showCalibration() { openDetail?.(calibrationEvidence()); },
    };
  },
};
