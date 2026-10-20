// Original electric chapter: same car, floating chart and driving controls.
// Replace its example petrol/EV physics with the competition research equations.
import { canvasTexture, approach } from '../kit.js';
import { board, plotFrame } from '../car.js';
import { makeResearchVehicle } from '../research/vehicle.js';
import { powerAt, motorEnvelope, PARAMS, RULES } from '../research/physics.js';
import { RESULTS } from '../research/results.js';

const calculate = (speedKmh, mode) => powerAt({ speedKmh, mode, model: 'calibrated' });
const fmt = (v, digits = 1) => Number.isFinite(v) ? v.toFixed(digits) : '—';
const modeText = { accel: '最大牵引', cruise: '匀速行驶', regen: '制动回收' };
const candidateCount = RESULTS.candidates.length;
const protocol = RESULTS.controlProtocol;

export default {
  id: 'electric',
  short: '怎样选节能策略',
  title: '先选方案，再调温控',
  subtitle: `先比较${candidateCount}组方案，再每${protocol.control_period_s}秒更新温控动作。`,
  view: { pos: [8.2, 6.8, 11.0], target: [1.0, 2.4, 0] },
  controlsTitle: '观察回收怎样改变电功率',
  termsTitle: '策略选择的三个关键点',
  learn: `<div class="research-block"><b>把几个开关一起调</b><p>刹车时多收回多少能量、冷却何时开关、车内目标温度怎样微调？我们组合出<b>${candidateCount}组方案</b>，让它们从相同状态出发、完成同一行驶任务，比较用电量。</p></div>
    <p>先筛掉不满足温度、舒适和设备能力要求的方案，再选出耗电更少的一组。运行时每<b>${protocol.control_period_s}秒</b>按当前状态更新冷却与车内温控；实际回收仍受电量、温度和硬件限制。</p>
    <p class="research-takeaway"><b>选中的方案：</b>把回收请求从${Math.round(RULES.baseline.regen_fraction * 100)}%提高到${Math.round(RULES.optimized.regen_fraction * 100)}%，同时调整冷却和车内目标温度。这个比例是控制请求，实际能收回多少还要看车辆状态。</p>
    <p class="tip"><b>看懂机理：</b>下方滑条只解释当前速度下的能量流。点击车辆或图表查看搜索方法；完整行程节能率来自已完成的对照实验。</p>`,
  defaults: { mode: 'cruise', speed: 80, chart: 'power' },
  controls: [
    { key: 'mode', type: 'seg', label: '驾驶状态', options: [
      { v: 'accel', label: '加速' }, { v: 'cruise', label: '匀速' }, { v: 'regen', label: '回收' },
    ] },
    { key: 'speed', type: 'range', label: '车速', min: 0, max: 160, step: 1,
      fmt: (v) => Math.round(v) + ' km/h' },
    { key: 'chart', type: 'seg', label: '悬浮图表', options: [
      { v: 'power', label: '功率流向' }, { v: 'force', label: '牵引与阻力' },
    ] },
  ],
  terms: [
    { t: '有限规则搜索', d: `逐一比较${candidateCount}组候选，按三种温度下的能耗与约束选择规则；最优范围限定在这组候选之内。` },
    { t: '每5秒反馈', d: `每${protocol.control_period_s}秒依据当前电量、温度和环境更新温控动作，利用已知到达时间安排结束前的温度恢复；回收请求使用选定规则。` },
    { t: '回收还要扣除耗电', d: '电机回馈先抵消空调、冷却和低压附件负载；电池净功率为负，才是净充电。' },
  ],
  reel: [
    { ms: 6000, caption: `我们在相同工况和起点下，联合比较${candidateCount}组回收与温控规则。`, set: { mode: 'accel', chart: 'power' },
      anim: { speed: [20, 110] }, view: { pos: [9.2, 7.6, 12.4], target: [1.2, 2.6, 0] }, spin: 0.2 },
    { ms: 6000, caption: `选定规则后每${protocol.control_period_s}秒更新温控；回收效果还取决于电池状态和其他用电负载。`, set: { mode: 'regen', chart: 'power' },
      anim: { speed: [100, 20] }, view: { pos: [-6.4, 6.4, 10.8], target: [-0.6, 1.6, 0] }, spin: 0.15 },
  ],
  build({ stage, openDetail }) {
    const car = makeResearchVehicle(stage, { pickable: true });
    const cur = { speed: 80, mode: 'cruise', chart: 'power' };
    const chart = canvasTexture(900, 560, (g, w, h) => {
      if (cur.chart === 'power') {
        const p = plotFrame(g, w, h, {
          x0: 0, x1: 160, y0: -80, y1: 200, left: 86, right: 30, top: 86, bottom: 62,
          xTicks: [0, 40, 80, 120, 160], yTicks: [-50, 0, 50, 100, 150, 200],
          title: `${modeText[cur.mode]}：功率如何随车速变化`,
          xLabel: '车速 (km/h)', yLabel: '功率 (kW)',
        });
        const val = (speed, key) => {
          const state = calculate(speed, cur.mode);
          return state.feasible ? state[key] / 1000 : null;
        };
        p.line((v) => val(v, 'batteryW'), 0, 160, '#ffb547', 6);
        p.line((v) => val(v, 'motorDcW'), 0, 160, '#5ce1a9', 3, [8, 6]);
        p.line((v) => val(v, 'wheelW'), 0, 160, '#8ef0ff', 3);
        // A visible zero baseline avoids reading perspective slope as sign.
        g.strokeStyle = 'rgba(232,238,248,.48)';
        g.lineWidth = 1.5;
        g.beginPath(); g.moveTo(p.X(0), p.Y(0)); g.lineTo(p.X(160), p.Y(0)); g.stroke();
        const state = calculate(cur.speed, cur.mode);
        p.vline(cur.speed);
        if (state.feasible) {
          const kw = state.batteryW / 1000;
          p.dot(cur.speed, kw, '#ffb547');
          const right = cur.speed > 120;
          p.text(`${kw >= 0 ? '+' : ''}${kw.toFixed(1)} kW`,
            p.X(cur.speed) + (right ? -16 : 16), p.Y(kw) - 18,
            '#ffb547', 'bold 19px sans-serif', right ? 'right' : 'left');
        }
        p.text('电池总功率', 575, 122, '#ffb547', '20px sans-serif');
        p.text('电机直流侧', 575, 151, '#5ce1a9', '20px sans-serif');
        p.text('轮端机械功率', 575, 180, '#8ef0ff', '20px sans-serif');
      } else {
        const maxForce = motorEnvelope({ speedKmh: 0, model: 'calibrated' }).maxWheelForceN;
        const maxY = Math.ceil(maxForce / 2000) * 2000;
        const p = plotFrame(g, w, h, {
          x0: 0, x1: 160, y0: -3000, y1: maxY, left: 86, right: 30, top: 86, bottom: 62,
          xTicks: [0, 40, 80, 120, 160], yTicks: [-2000, 0, 2000, 4000, 6000, 8000].filter((v) => v <= maxY),
          title: '电机牵引边界与道路阻力', xLabel: '车速 (km/h)', yLabel: '力 (N)',
        });
        p.line((v) => motorEnvelope({ speedKmh: v, model: 'calibrated' }).maxWheelForceN,
          0, 160, '#5ce1a9', 6);
        p.line((v) => calculate(v, 'cruise').roadLoadN,
          0, 160, '#8ef0ff', 3, [10, 8]);
        const state = calculate(cur.speed, cur.mode);
        const force = state.speedMps > 0 ? state.wheelW / state.speedMps : 0;
        p.vline(cur.speed);
        if (state.feasible) p.dot(cur.speed, force, '#ffb547');
        p.text('最大牵引力', 575, 122, '#5ce1a9', '20px sans-serif');
        p.text('平路行驶阻力', 575, 151, '#8ef0ff', '20px sans-serif');
        p.text('当前需求', 575, 180, '#ffb547', '20px sans-serif');
      }
    });
    // Exact floating-board arrangement from CarClear's electric chapter.
    const chartBoard = board(chart.tex, 6.2, 3.86);
    chartBoard.position.set(2.2, 5.9, -3.3);
    chartBoard.rotation.y = -0.35;
    car.root.add(chartBoard);
    chartBoard.userData.partId = 'chart';
    stage.pickables.push(chartBoard);
    const lFlow = stage.label('', [-0.5, 4.5, 0], car.root, 'hot');
    let velocity = 80 / 3.6, drawn = '';
    return {
      update(dt, s) {
        velocity = approach(velocity, s.speed / 3.6, 3, Math.max(0, dt));
        const p = calculate(s.speed, s.mode);
        car.setFlow(p.feasible ? p.motorDcW : 0);
        car.updatePose(dt, velocity, p.rpm);
        const key = `${s.speed}|${s.chart}|${s.mode}|${p.feasible}`;
        if (key !== drawn) {
          drawn = key;
          Object.assign(cur, s);
          chart.redraw();
          lFlow.element.innerHTML = !p.feasible ? '当前状态超出硬件或电池边界' :
            p.motorDcW < -1 ? `<b>电机回馈</b> ${fmt(-p.motorDcW / 1000)} kW` :
            p.motorDcW > 1 ? `<b>电驱动</b> ${fmt(p.motorDcW / 1000)} kW` : '静止 · 低压附件仍用电';
        }
        lFlow.visible = stage.host.clientWidth >= 560;
      },
      pick(o) {
        const p = calculate(cur.speed, cur.mode);
        const base = RULES.baseline;
        const chosen = RULES.optimized;
        openDetail?.(`<p class="kicker">问题三 · 从规则选择到反馈执行</p>
          <h2>${candidateCount}组候选怎样比较</h2>
          <p>基准和候选使用同一校准 Rint 模型、同一 CLTC 车速任务、同样初始状态：SOC ${fmt(protocol.initial_soc * 100, 0)}%，电池初温等于环境温度，座舱初温 ${fmt(protocol.initial_cabin_c, 0)}℃。分别在25℃、40℃与−10℃下运行。</p>
          <p>回收比例 ${protocol.grid.regen_fraction.length} 档 × 冷却开启温度 ${protocol.grid.cooling_on_c.length} 档 × 冷却回差 ${protocol.grid.cooling_hysteresis_c.length} 档 × 座舱调整量 ${protocol.grid.cabin_economy_offset_c.length} 档，共 ${candidateCount} 组。目标是三环境下相对基准电耗的平均值，并计入驾驶结束时温度差异的热修正；只在满足约束的候选间比较。</p>
          <table><thead><tr><th>规则设置</th><th>基准</th><th>选定候选</th></tr></thead><tbody>
            <tr><td>回收请求比例</td><td>${fmt(base.regen_fraction, 2)}</td><td>${fmt(chosen.regen_fraction, 2)}</td></tr>
            <tr><td>名义冷却开启温度</td><td>${fmt(base.cooling_on_c, 0)}℃</td><td>${fmt(chosen.cooling_on_c, 0)}℃</td></tr>
            <tr><td>冷却回差</td><td>${fmt(base.cooling_hysteresis_c, 0)}℃</td><td>${fmt(chosen.cooling_hysteresis_c, 0)}℃</td></tr>
            <tr><td>座舱温度调整量</td><td>${fmt(base.cabin_economy_offset_c)}℃</td><td>${fmt(chosen.cabin_economy_offset_c)}℃</td></tr>
          </tbody></table>
          <p>回收比例是请求值，实际回馈仍受电量、温度和硬件边界约束。名义冷却阈值还受高温初态保护限制，并不意味着所有场景都等到 ${fmt(chosen.cooling_on_c, 0)}℃才冷却。</p>
          <h3>运行时怎样执行</h3><p>每 ${protocol.control_period_s} 秒根据当前状态修正热管理功率；利用计划到达时间，在结束前 ${protocol.terminal_cabin_return_s} 秒把座舱目标恢复到 ${protocol.cabin_target_c}℃。控制不读取未来车速。座舱舒适区间、电池温区、设备功率与最短切换间隔均采用预先声明的研究约束。</p>
          <p>这是离线有限规则搜索与在线反馈执行：得到候选集合内的优选规则，不能由此宣称连续参数空间的全局最优。</p>
          <h3>${o.userData.partId === 'chart' ? '这张图解释哪一步' : '车上的能量流解释哪一步'}</h3>
          <p>论文的平路主模型由惯性、滚动阻力与空气阻力计算轮端功率。网页道路章节另外提供坡度辅助扩展，本页仍采用平路。驱动时，还要补足传动、电机和逆变器的损耗，才能得到电机直流侧需求。</p>
          <p>电池总功率等于电机直流侧功率加附件功率。本页关闭温控，仅计入模型中的 300 W 低压附件。回收时，电机功率为负；当其绝对值超过附件耗电，电池才是净充电。</p>
          <p><b>当前：</b>轮端 ${fmt(p.wheelW / 1000)} kW，电机直流侧 ${fmt(p.motorDcW / 1000)} kW，附件 ${fmt(p.accessoryW / 1000)} kW，电池 ${fmt(p.batteryW / 1000)} kW。</p>
          <p>滑条使用论文校准后参数做当前状态计算，图中回收请求沿用基准${Math.round(base.regen_fraction * 100)}%。“加速”展示当前速度下的最大牵引，“回收”以 −1.5 m/s² 为示例。它解释回收和损耗；${candidateCount}组方案的搜索与整程对照是论文中已完成的实验。</p>
          <p class="muted">来源：论文第6.1—6.3节；支撑材料 <code>configs/control_protocol.json</code>、<code>configs/optimized_rule.json</code>、<code>results/tables/control_design_grid.csv</code>、<code>results/tables/control_evaluation.json</code>。控制效果是同模型、同条件下的仿真证据；本地运行耗时未等同于车载硬件部署测试。</p>`);
      },
      readout(s) {
        const p = calculate(s.speed, s.mode);
        if (!p.feasible) return `<div class="big">当前状态超出边界</div><small>${p.reason}</small>`;
        const net = p.batteryW < 0 ? '电池净回收' : '电池净供电';
        let note = '当前状态 · 25℃ · 电量80% · 校准参数 · 温控关闭';
        if (s.mode === 'regen') note += ` · 基准回收请求${Math.round(RULES.baseline.regen_fraction * 100)}% · 示例减速度 −1.5 m/s²`;
        else if (s.mode === 'accel') note += ` · 最大牵引（${PARAMS.calibrated.motor_power_max_w / 1000} kW 上限）`;
        return `<div class="big">${net} ${fmt(Math.abs(p.batteryW) / 1000)} kW</div>
          <div class="row"><span>电机 / 轮端</span><b>${fmt(p.motorDcW / 1000)} / ${fmt(p.wheelW / 1000)} kW</b></div>
          <small>${note}</small>`;
      },
      dispose() { car.dispose(); },
    };
  },
};
