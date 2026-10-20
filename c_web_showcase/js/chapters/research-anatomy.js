// CarClear Chapter 1 interaction, using the original electric-car parts.
import { approach } from '../kit.js';
import { makeResearchVehicle } from '../research/vehicle.js';
import { powerAt } from '../research/physics.js';
import { comparison } from '../research/results.js';

const ASSEMBLED_VIEW = { pos: [10.2, 7.5, 13.0], target: [0, 1.7, 0] };
const EXPLODED_VIEW = { pos: [11.0, 10.5, 13.5], target: [0, 3.0, 0] };
const wholeTrip = (temperature) => comparison(temperature, 'long');
const saving = (temperature) => wholeTrip(temperature).savingPct.toFixed(2);

const descriptions = {
  battery: ['动力电池模型', '把用电需求转换成电量变化与发热',
    '<b>输入：</b>电驱和附件总功率、当前 SOC、当前电池温度。<b>输出：</b>电流、端电压、SOC 变化和内阻发热。',
    'Rint 模型用开路电压和内阻求电流；电流积分更新 SOC，I²R 进入电池热模型。热模型返回的新温度又影响内阻与回收能力，因此电量和温度是一起计算的。',
    '功率与状态', '电池电学模型', '电量与发热'],
  inverter: ['逆变器模型', '把电机需求换算到电池直流侧',
    '<b>输入：</b>电机侧电功率与驱动/回收方向。<b>输出：</b>直流侧功率和逆变器转换损耗。',
    '论文把逆变器效率与电机损耗分开处理，再把电驱需求与空调、冷却和低压附件合并成电池负载。这样可以解释“电机在回收，电池却仍可能净放电”的情况。',
    '电机电功率', '逆变器效率', '直流侧功率'],
  motor: ['驱动与回收模型', '同一套方程，计算推车与发电',
    '<b>输入：</b>车速、轮端功率和允许的回收比例。<b>输出：</b>电机转速、转矩、电功率，以及未被回收的制动功率。',
    '电机损耗由常数项、转矩平方项和转速平方项构成。回收请求还受到电量、温度、转矩和功率边界约束；提高请求比例不等于把全部刹车能量收回来。',
    '行驶需求', '电机与回收约束', '用电或回馈'],
  reducer: ['传动模型', '连接车轮需求与电机工作点',
    '<b>输入：</b>车轮速度与轮端功率。<b>输出：</b>电机端转速、机械功率和传动损耗。',
    '我们通过固定传动比与传动效率，把整车纵向动力学和电机模型接起来。计算沿“需要多少轮端功率”反推电机与电池需求，画面能量流则显示实际传递方向。',
    '轮端需求', '传动比与效率', '电机端需求'],
  charging: ['研究范围：行驶与停车温控', '停车后继续算的，是温度恢复所需的电',
    '本研究比较车辆行驶用电，并在行驶结束后继续模拟共同停车温控。画面中的充电接口保留车辆结构关系，未另建充电桩或充电协议模型。',
    '90 分钟驾驶结束后，两种策略都继续运行 120 分钟停车温控，由电池供电。把这部分算进去，是为了检查行驶期间的节能有没有变成后续温控的补偿耗电。',
    '90 分钟驾驶', '共同停车温控', '完整行程电耗'],
  wheels: ['整车纵向动力学', '先算这一刻需要多大的推力',
    '<b>输入：</b>车速及其变化、整车质量、风阻与滚阻参数。<b>输出：</b>轮端牵引/制动功率，交给传动与电机模块。',
    '论文平路主模型计算惯性力、滚动阻力与空气阻力。风阻和滚阻系数再由实测行车数据校准。网页道路章节的坡度是额外机理展示，车轮转动也作了视觉减速。',
    '车速与车辆参数', '纵向动力学', '轮端功率'],
};

function detail(id, current) {
  const d = descriptions[id] || descriptions.battery;
  return `<p class="kicker">研究模型 · 模块之间怎样连接</p><h2>${d[0]}</h2>
    <p class="sub">${d[1]}</p>
    <svg viewBox="0 0 540 100" role="img" aria-label="${d.slice(4).join('到')}">
      <defs><marker id="part-arrow" markerWidth="8" markerHeight="8" refX="6" refY="3" orient="auto"><path d="M0 0L6 3L0 6" fill="#5ce1a9"/></marker></defs>
      <g fill="rgba(92,225,169,.08)" stroke="#5ce1a9" stroke-width="1.5">
        <rect x="5" y="20" width="145" height="54" rx="12"/><rect x="195" y="20" width="145" height="54" rx="12"/><rect x="385" y="20" width="145" height="54" rx="12"/>
        <path d="M155 47H188M345 47H378" marker-end="url(#part-arrow)"/>
      </g><g fill="#e8eef8" font-size="17" text-anchor="middle" font-family="sans-serif">
        <text x="77" y="53">${d[4]}</text><text x="267" y="53">${d[5]}</text><text x="457" y="53">${d[6]}</text>
      </g>
    </svg><p>${d[2]}</p><h3>我们怎样计算</h3><p>${d[3]}</p>
    <h3>当前画面的机理说明</h3><p>车速 ${current.speedKmh.toFixed(0)} km/h，电池总功率 ${(current.batteryW / 1000).toFixed(2)} kW。这里采用25℃、SOC80%、辨识后参数并关闭温控，解释模块间的瞬时关系；它不改写完整行程的实验结果。</p>
    <h3>与研究结论的关系</h3><p>这些模块共用同一组状态与能量账本，再用实测数据校准参数。完整行程模型对照中，25℃、40℃、−10℃的总耗电分别下降 ${saving(25)}%、${saving(40)}%、${saving(-10)}%。</p>
    <p class="muted">来源：论文第3—6节、表16；支撑材料 <code>src/bev/model.py</code>、<code>src/bev/control.py</code>、<code>results/tables/durability_comparisons.csv</code>。完整行程为90分钟驾驶与120分钟共同停车温控；结果为模型对照，温度未作同车独立实测验证。三维尺寸与模块排布为关系示意。</p>`;
}

export default {
  id: 'anatomy',
  short: '研究了什么',
  title: '既省电，也管好温度',
  subtitle: '把动力、电池与温控连起来，用数据校准，再比较节能方案。',
  view: ASSEMBLED_VIEW,
  controlsTitle: '拆开模型，看模块怎样连接',
  termsTitle: '对应赛题的三个问题',
  learn: `<p>同样一辆车、同样一段路，怎样既少用电，又把电池和乘员舱的温度管好？这是我们研究的问题。</p>
    <div class="research-block"><p><b>① 建模：</b>把动力、电池和热管理放在一起算。<br>
    <b>② 校准：</b>用真实行车数据调整参数。<br>
    <b>③ 比较：</b>在常温、高温和低温下选择节能策略。</p></div>
    <p class="research-takeaway"><b>得到的结论：</b>在本次模型对照中，常温与高温通过刹车回收和冷却调整，计入停车用电后仍能省电；低温收益较小，后续应重点改进车内取暖。</p>
    <p class="tip"><b>看懂模型：</b>拖动拆解、点击内部部件，查看我们给它什么输入，又算出了什么。</p>`,
  defaults: { explode: 0, xray: true, flow: true, speed: 40 },
  controls: [
    { key: 'explode', type: 'range', label: '拆解程度', min: 0, max: 1, step: 0.01,
      ends: ['组装', '拆开'], fmt: (v) => Math.round(v * 100) + '%' },
    { key: 'xray', type: 'toggle', label: '透视车壳' },
    { key: 'flow', type: 'toggle', label: '显示能量路径' },
    { key: 'speed', type: 'range', label: '匀速行驶', min: 0, max: 120, step: 1,
      fmt: (v) => Math.round(v) + ' km/h' },
  ],
  terms: [
    { t: '第一问：电去了哪里', d: '把行驶、电池和温度变化一起计算，按25℃标准车速过程（CLTC）算出耗电，再比较哪些参数影响大。' },
    { t: '第二问：模型能否贴近数据', d: '用两段路程调整参数，在另一路程检查剩余电量和耗电量的预测误差，并估计参数的不确定范围。' },
    { t: '第三问：怎样控制更省电', d: '比较回收、冷却与座舱温控规则，同时检查温度、舒适性和后续停车用电。' },
  ],
  reel: [{
    ms: 7000, caption: '我们把动力、电池与热管理算在一起，再用行车数据校准，比较三种温度下的节能方案。',
    set: { xray: true, flow: true, speed: 40 }, anim: { explode: [0, 0.8] },
    view: { pos: [9.2, 7.8, 11.4], target: [0, 2.4, 0] }, spin: 0.25,
  }],
  build({ stage, openDetail }) {
    const car = makeResearchVehicle(stage, { pickable: true });
    let split = 0;
    let expanded = false;
    let current = powerAt({ speedKmh: 40, mode: 'cruise', model: 'calibrated' });
    return {
      update(dt, s) {
        split = approach(split, s.explode, 5, Math.max(0, dt));
        car.setExplode(split);
        car.setXray(s.xray);
        const p = powerAt({ speedKmh: s.speed, mode: 'cruise', model: 'calibrated' });
        current = p;
        car.setFlow(s.flow ? p.motorDcW : 0);
        car.updatePose(dt, p.speedMps, p.rpm);
        if (s.explode > 0.35 && !expanded) {
          expanded = true;
          stage.setView(EXPLODED_VIEW.pos, EXPLODED_VIEW.target, 0.8);
        } else if (s.explode < 0.15 && expanded) {
          expanded = false;
          stage.setView(ASSEMBLED_VIEW.pos, ASSEMBLED_VIEW.target, 0.8);
        }
      },
      pick(o) { if (o.userData.partId) openDetail?.(detail(o.userData.partId, current)); },
      readout() {
        return `<div class="big">25℃少用 ${saving(25)}% 的电</div>
          <div class="row"><span>40℃高温</span><b>节能 ${saving(40)}%</b></div>
          <div class="row"><span>−10℃低温</span><b>节能 ${saving(-10)}%</b></div>
          <small>90分钟驾驶＋120分钟停车温控 · 模型对照</small>`;
      },
      dispose() { car.dispose(); },
    };
  },
};
