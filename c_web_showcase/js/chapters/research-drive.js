// Adapted directly from CarClear's drive.js: the tilting road, rolling wheels,
// wind streaks and proportional force arrows retain the original visual grammar.
// The force/power evaluation uses the competition model's reference parameters.
import { THREE, arrow, approach } from '../kit.js';
import { CARU, makeBody, makeWheels } from '../car.js';
import { makeResearchRoad } from '../research/weather.js';
import { PARAMS, powerAt } from '../research/physics.js';
import { RESULTS } from '../research/results.js';

const F_SCALE = 1 / 1100;
const evaluate = (s) => powerAt({ speedKmh: s.speed, slopePct: s.slope, massKg: s.mass, mode: s.mode, model: 'reference', ambientC: 25, batteryC: 25 });
const number = (v) => Math.round(v).toLocaleString('zh-CN');
const q1 = RESULTS.q1;
const fixed = (value, digits = 3) => Number(value).toFixed(digits);

function modelEvidence() {
  const sensitivity = RESULTS.sensitivity.filter(r => r.fractional_perturbation === .1)
    .sort((a, b) => a.energy_rank - b.energy_rank);
  return `<h2>问题一：整段行程怎样算成能耗</h2>
    <p>输入1800秒CLTC车速序列、环境温度和车辆参数，在每个积分步连接轮端、电驱、电池与热管理。原模型最大步长0.5秒；这里的滑条只解释一个给定状态。</p>
    <h3>从行驶需求到电池状态</h3>
    <p>① 由速度变化计算惯性需求，再叠加滚阻、风阻；平路基准采用
      <code>F = δma + mgCr + ½ρCdAv²</code>，轮上功率为<code>P = Fv</code>。</p>
    <p>② 牵引时计入传动、电机和逆变器损耗；制动时按电机、电池与回收上限计算返回直流母线的功率。</p>
    <p>③ 电驱功率加上空调、加热、冷却和低压附件，得到电池端功率；Rint模型再计算电流、电压、SOC和焦耳热。</p>
    <p>④ 电池与座舱各用一个集中温度状态。温度影响内阻与回收，温控又消耗电能，从而把动力、电量和热管理连接起来。</p>
    <h3>25℃CLTC参考基准</h3>
    <table><tbody>
      <tr><th>时间 / 里程</th><td>30分钟 / ${fixed(q1.distance_km, 5)} km</td></tr>
      <tr><th>净端电能</th><td>${fixed(q1.net_energy_kwh, 6)} kWh</td></tr>
      <tr><th>百公里电耗</th><td>${fixed(q1.consumption_kwh_100km, 4)} kWh/100km</td></tr>
      <tr><th>终SOC / 电池峰温</th><td>${fixed(q1.soc_final * 100, 4)}% / ${fixed(q1.battery_max_c, 4)}℃</td></tr>
    </tbody></table>
    <p>该基准使用参考Rint、160Ah、初SOC80%，电池和座舱初温均为25℃。它与控制优化章的校准参数、初SOC94%属于不同实验，不能直接拼成节能对照。</p>
    <h3>七参数灵敏度：±10%扰动</h3>
    <table><thead><tr><th>参数</th><th>端电耗归一化灵敏度</th><th>排序</th></tr></thead><tbody>
      ${sensitivity.map(r => `<tr><td>${r.label}</td><td>${fixed(r.normalized_energy_sensitivity, 4)}</td><td>${r.energy_rank}</td></tr>`).join('')}
    </tbody></table>
    <p>±5%与±10%两种幅度都把整车质量、滚阻、风阻排在前三。内阻与热容对本基准的端电耗指标为零，仍会影响SOC或温度。</p>
    <p>瞬时功率的单位是kW；沿全程积分后才得到kWh。坡度滑条是受力关系的辅助扩展，论文CLTC主基准为平路；当前所选加速或制动状态也不是完整车速序列。</p>
    <p class="muted">来源：论文4.2—4.6节；<code>src/bev/model.py</code>、
      <code>results/tables/scenario_A_summary.json</code>、
      <code>results/tables/scenario_A_sensitivity.csv</code>。热状态为模型输出。</p>`;
}

export default {
  id: 'drive',
  short: '模型怎么算电',
  title: '模型怎么算电',
  subtitle: '问题一：按一份车速表行驶，算清用电、电量和温度。',
  view: { pos: [2.4, 7.6, 19.0], target: [1.0, 2.0, 0] },
  learn: `<p class="research-takeaway"><b>基准结果：25℃，30分钟，${fixed(q1.distance_km, 2)} km。</b><br>
    净耗电${fixed(q1.net_energy_kwh)}度，折合<b>每百公里${fixed(q1.consumption_kwh_100km, 2)}度电</b>（kWh）。</p>
    <div class="research-block"><b>我们怎么计算</b><p>先算车轮需要多大动力，再算电机要用多少电、刹车能收回多少。把空调和冷却用电加进来，逐步更新电池剩余电量与温度，最后累计整段行程的耗电。</p></div>
    <p><b>发现哪些因素更关键：</b>我们把7个参数分别调高、调低5%和10%。两种幅度下，对基准耗电影响最大的前三项都是：车重、轮胎滚动阻力和空气阻力。</p>
    <p class="tip"><b>用画面理解模型：</b>箭头显示当前状态下的受力和功率。改变车速或载重可看计算机制；坡度是辅助扩展，论文主基准仍是平路。</p>`,
  controlsTitle: '用当前状态理解模型',
  termsTitle: '模型方法与结果口径',
  terms: [
    { t: '把动力、电池与温度一起算', d: '这叫机电热联合模型：车轮需求决定电机用电，电池随之掉电、发热，温度又影响回收能力和空调、冷却的用电。' },
    { t: '从功率到全程能耗', d: '每一步电池端功率乘时间，再沿1800秒累加，得到净端电能。kW是当前用电速率，kWh是累计用电量。' },
    { t: '参数影响有多大', d: '保持其他条件相同，一次只改一个参数，看耗电变化多少。这种比较叫灵敏度分析。' },
    { t: '当前交互的范围', d: '滑条只计算参考参数下的给定状态。坡度mg·sinθ是展示扩展，不会改变已经归档的平路CLTC结果。' },
  ],
  defaults: { speed: 60, slope: 0, mass: PARAMS.reference.mass_kg, mode: 'cruise' },
  controls: [
    { key: 'speed', type: 'range', label: '车速', min: 0, max: 130, step: 1, fmt: (v) => `${Math.round(v)} km/h` },
    { key: 'slope', type: 'range', label: '道路坡度', min: -8, max: 15, step: .5, ends: ['下坡', '上坡'], fmt: (v) => `${v > 0 ? '+' : ''}${v.toFixed(1)}%` },
    { key: 'mass', type: 'range', label: '整车质量 · 含载荷', min: 1000, max: 2200, step: 10, fmt: (v) => `${number(v)} kg` },
    { key: 'mode', type: 'seg', label: '驾驶动作', options: [{ v: 'cruise', label: '保持车速' }, { v: 'accel', label: '加速' }, { v: 'regen', label: '制动' }] },
    { type: 'buttons', label: '问题一的方法与证据', items: [
      { label: '模型链条与基准结果', act: (_s, inst) => inst.showModelEvidence?.() },
    ] },
  ],
  reel: [
    { ms: 8500, caption: '问题一从车速工况计算轮上需求，再连接电驱损耗、SOC和热管理。25℃CLTC基准电耗为14.0542 kWh/100km。', set: { mass: PARAMS.reference.mass_kg, mode: 'cruise', slope: 0 }, anim: { speed: [25, 120] }, view: { pos: [2.4, 7.6, 19], target: [1, 2, 0] }, spin: 0 },
    { ms: 6500, caption: '七参数对照中，质量、滚阻和风阻影响最明显。滑条与坡道帮助解释受力，归档结论仍来自完整平路工况。', set: { speed: 70, mode: 'cruise' }, anim: { slope: [0, 8] }, spin: 0 },
  ],
  build({ stage, openDetail }) {
    const world = new THREE.Group(); stage.root.add(world);
    const road = makeResearchRoad(stage, world);
    const car = new THREE.Group(); world.add(car);
    const body = makeBody({ color: 0xdfe6ee }); car.add(body.group);
    const wheels = makeWheels(); wheels.forEach((w) => car.add(w.group));
    const mk = (color, cls = '') => {
      const a = arrow(color, 1, .34, .07); world.add(a);
      const tag = new THREE.Group(); world.add(tag);
      const label = stage.label('', [0, 0, 0], tag, cls);
      return { arrow: a, tag, label };
    };
    const drive = mk(0xffb547, 'hot'), drag = mk(0x8ef0ff), roll = mk(0xb0b6c2), hill = mk(0xc49bff);
    const setArrow = (item, x, y, z, direction, force, text) => {
      const length = Math.max(.05, Math.min(7, Math.abs(force) * F_SCALE));
      item.arrow.set(length); item.arrow.visible = item.label.visible = Math.abs(force) > 20;
      item.arrow.position.set(x, y, z);
      item.arrow.rotation.set(0, 0, direction > 0 ? -Math.PI / 2 : Math.PI / 2);
      item.tag.position.set(x + direction * (length + .2), y + .55, z);
      item.label.element.innerHTML = text;
    };
    let v = 60 / 3.6, tilt = 0, wheelAngle = 0;
    return {
      update(dt, s) {
        dt = Math.max(0, dt);
        v = approach(v, s.speed / 3.6, 3, dt);
        tilt = approach(tilt, Math.atan(s.slope / 100), 4, dt);
        world.rotation.z = tilt;
        road.update(dt, v);
        wheelAngle += v / PARAMS.reference.wheel_radius_m * dt / 4;
        wheels.forEach((w) => { w.spin.rotation.z = -wheelAngle; });
        const f = evaluate(s);
        const traction = f.roadLoadN + f.inertiaN;
        setArrow(drive, -CARU.axle, .25, CARU.track + .7, traction >= 0 ? 1 : -1, traction, `${traction >= 0 ? '车轮推力' : '制动力'} ${number(Math.abs(traction))} N`);
        drive.tag.position.set(-CARU.axle - 1.9, .3, CARU.track + .7);
        setArrow(roll, CARU.axle + .2, .8, CARU.track + .9, -1, f.rollingN, `滚阻 ${number(f.rollingN)} N`);
        const dragLength = Math.max(.05, Math.min(7, f.dragN * F_SCALE));
        setArrow(drag, 5.85 + dragLength, 1.5, 0, -1, f.dragN, `风阻 ${number(f.dragN)} N`);
        drag.tag.position.set(5.9 + dragLength * .5, 2.2, 0);
        setArrow(hill, 0, 2.2, -.2, f.hillN >= 0 ? -1 : 1, f.hillN, `${f.hillN >= 0 ? '上坡阻力' : '下坡助力'} ${number(Math.abs(f.hillN))} N`);
      },
      readout(s) {
        const f = evaluate(s);
        const title = f.feasible ? `当前${f.batteryW >= 0 ? '供电' : '回收'} ${(Math.abs(f.batteryW) / 1000).toFixed(1)} kW` : '当前需求超过电机能力';
        return `<div class="big">${title}</div>
          <div class="row"><span>模型分项：风阻 / 滚阻 / 坡度</span><b>${number(f.dragN)} / ${number(f.rollingN)} / ${number(f.hillN)} N</b></div>
          <small>${!f.feasible ? f.reason : s.mode === 'regen' ? '参考参数的当前状态计算；减速度1.5 m/s²是交互设定。完整CLTC基准结论见右侧。' : s.slope ? '坡度为受力机制扩展；不会重算或改写论文平路基准。' : '参考参数的当前状态计算；全程积分结果与灵敏度结论见右侧。'}</small>`;
      },
      showModelEvidence() { openDetail?.(modelEvidence()); },
    };
  },
};
