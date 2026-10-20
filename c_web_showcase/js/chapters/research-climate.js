// The original CarClear electric-car scene, now with direct ambient-temperature
// controls. Weather is illustrative; electrical/thermal numbers are instantaneous
// evaluations of the supplied research equations, never a saved-trace playback.
import { M, box, clamp, approach } from '../kit.js';
import { pathOf, flowDots } from '../car.js';
import { makeResearchVehicle } from '../research/vehicle.js';
import { makeResearchRoad, makeWeather } from '../research/weather.js';
import { powerAt, thermalAt } from '../research/physics.js';
import { RESULTS } from '../research/results.js';

const fullJourney = temperature => RESULTS.long.find(r => r.ambient_c === temperature);
const saving = (temperature, digits = 3) => fullJourney(temperature).total_saving_pct.toFixed(digits);

function climateEvidence() {
  return `<h2>三种环境，为什么得到不同的节能幅度</h2>
    <p>题面把25℃CLTC列为必选基准，并给出低温−10/−20℃与高温40℃等可选场景。本文选25℃、40℃和−10℃，用同一校准模型、工况、初态约定与设备约束比较策略。</p>
    <h3>常温、高温与低温分别检验什么</h3>
    <p><b>25℃：</b>温控负担较轻，主要考察制动能量回收与基本能量分配。<br>
      <b>40℃：</b>座舱制冷和电池冷却共同占用功率，需要兼顾节能、舒适和热安全。<br>
      <b>−10℃：</b>座舱持续取暖，低温又限制电池回收；模型中的主要优化方向转为加热管理。</p>
    <h3>把驾驶和停车温控一起计入</h3>
    <table><thead><tr><th>环境</th><th>基准总电能/kWh</th><th>优化总电能/kWh</th><th>节能率</th></tr></thead><tbody>
      ${[25, 40, -10].map(t => { const r = fullJourney(t); return `<tr><td>${t}℃</td><td>${r.baseline_energy_kwh.toFixed(6)}</td><td>${r.optimized_energy_kwh.toFixed(6)}</td><td>${r.total_saving_pct.toFixed(3)}%</td></tr>`; }).join('')}
    </tbody></table>
    <p>以上是90分钟驾驶后，再进行120分钟共同停车温控的模型对照。−10℃总节能约${saving(-10, 2)}%，小于常温与高温；不能把短时关闭温控所少用的功率直接当成满足舒适要求的节能收益。</p>
    <h3>怎样区分回收与座舱目标的作用</h3>
    <p>将两组座舱目标都设为24℃、仅保留选定的回收和冷却设置后，单循环的末端热修正节能率如下：</p>
    <table><thead><tr><th>环境</th><th>相同24℃目标</th></tr></thead><tbody>
      ${[25, 40, -10].map(t => { const r = RESULTS.sameTarget.find(row => row.ambient_c === t); return `<tr><td>${t}℃</td><td>${r.corrected_saving_pct.toFixed(3)}%</td></tr>`; }).join('')}
    </tbody></table>
    <p>常温、高温的主要收益仍保留；低温仅约0.002%。这支持论文的结论：常温与高温重点改善回收和冷却，低温重点研究座舱加热。单循环热修正与上方完整行程的核算范围不同。</p>
    <h3>当前画面与论文对照的初态</h3>
    <p><b>本页交互：</b>采用参考参数、SOC80%，把电池和座舱都设为当前环境温度，计算这一给定状态的驱动、取暖或制冷功率。没有沿动画时间积分温度，也没有运行完整控制规则。</p>
    <p><b>论文策略对照：</b>采用校准Rint、初SOC94%，电池初温等于环境，座舱初温统一24℃；预先把座舱调到24℃的耗电未计入。两组共同遵守座舱22—26℃与电池−20—45℃等约束。</p>
    <p>雪花、热浪和部件色彩帮助辨认环境，不代表气象实测、积雪路面阻力或空间温度场。热状态及节能率属于仿真证据，尚无同车独立测温和实车节能验证。</p>
    <p class="muted">来源：C题题面运行场景；论文6.3—6.4节、表14—16；
      <code>configs/control_protocol.json</code>、
      <code>configs/durability_protocol.json</code>、
      <code>results/tables/durability_comparisons.csv</code>、
      <code>records/revision_20261019/results.json</code>。</p>`;
}

const thermalSnapshot = (s) => {
  const thermal = thermalAt({
    ambientC: s.temperature, batteryC: s.temperature, cabinC: s.temperature,
    cabinTargetC: 24, cooler: s.batteryCooling ? 'auto' : 'off',
    cabin: s.cabinClimate ? 'on' : 'off', model: 'reference',
  });
  const power = powerAt({
    speedKmh: s.speed, ambientC: s.temperature, batteryC: s.temperature,
    cabinC: s.temperature, model: 'reference',
    acW: thermal.acW, heaterW: thermal.heaterW, coolerW: thermal.coolerW,
  });
  return { thermal, power };
};

export default {
  id: 'climate',
  short: '冷热环境的取舍',
  title: '冷热环境下的取舍',
  subtitle: '同样的方案，为什么天冷时省下的电更少？',
  view: { pos: [8.2, 6.8, 11.0], target: [0.7, 2.0, 0] },
  learn: `<p class="research-takeaway"><b>完整行程：常温与高温仍有收益，低温约${saving(-10, 2)}%。</b><br>
    25℃节能${saving(25, 2)}%，40℃节能${saving(40, 2)}%；均计入90分钟驾驶与120分钟停车温控，为同条件模型对照。</p>
    <div class="research-block"><b>为什么选这三种环境</b><p>25℃作为常温基准；40℃看冷气和电池冷却多用多少电；−10℃看取暖用电，以及电池能收回多少刹车能量。</p></div>
    <p><b>研究发现：</b>天冷时，车内取暖需要持续用电，电池又不能像常温时那样充分回收能量。这套方案的低温收益有限，后续应重点改进取暖。</p>
    <p><b>画面用来解释原因：</b>切换温度看驱动、座舱和冷却如何分担功率。雪与热浪是环境示意，右侧研究结论来自固定实验。</p>
    <p class="muted">画面从电量80%、车内与电池均等于环境温度的状态计算。正式节能对照从电量94%、车内24℃开始，详细条件见下方说明。</p>`,
  controlsTitle: '观察温控负荷的来源',
  termsTitle: '场景比较与初态约定',
  terms: [
    { t: '同条件温度场景', d: '25℃、40℃、−10℃采用相同车辆模型与策略评价流程；每个环境内部比较基准和优化，而不是跨温度直接算节能率。' },
    { t: '低温收益为什么较小', d: '取暖需要持续供电，低温又压缩回收空间。论文完整行程的−10℃总节能为0.699%，提示加热管理是重点。' },
    { t: '完整行程的边界', d: '结果包含90分钟驾驶和120分钟共同停车温控；出发前把座舱预调到24℃的用电没有计入。' },
    { t: '当前功率与研究结论', d: '滑条仅计算给定温度下的一次功率分配。开关用于说明负荷来源；正式策略仍需满足舒适与安全约束，动画不充当实测温度响应。' },
  ],
  defaults: { temperature: 25, speed: 60, cabinClimate: true, batteryCooling: true, xray: true },
  controls: [
    { key: 'temperature', type: 'range', label: '环境温度', min: -10, max: 40, step: 1, ends: ['−10℃ · 寒冷', '40℃ · 炎热'], fmt: (v) => `${v > 0 ? '+' : ''}${Math.round(v)} ℃` },
    { type: 'buttons', label: '温度快捷选择', items: [-10, 25, 40].map((v) => ({ label: `${v}℃`, act: (s) => { s.temperature = v; } })) },
    { key: 'speed', type: 'range', label: '车速', min: 0, max: 130, step: 1, fmt: (v) => `${Math.round(v)} km/h` },
    { key: 'cabinClimate', type: 'toggle', label: '座舱温控 · 目标24℃' },
    { key: 'batteryCooling', type: 'toggle', label: '电池自动冷却' },
    { key: 'xray', type: 'toggle', label: '透视车身' },
    { type: 'buttons', label: '三温度比较的研究证据', items: [
      { label: '场景结果与初态说明', act: (_s, inst) => inst.showClimateEvidence?.() },
    ] },
  ],
  reel: [
    { ms: 8500, caption: '−10℃时，取暖负荷与低温回收限制压缩节能空间；论文完整行程模型对照仅节能约0.70%。', set: { temperature: -10, speed: 50, cabinClimate: true, batteryCooling: true, xray: true }, view: { pos: [8.2, 6.8, 11], target: [.7, 2, 0] }, spin: 0 },
    { ms: 8500, caption: '25℃与40℃完整行程节能11.835%和9.850%。画面解释当前温控负荷，结论来自满足共同约束的固定实验。', set: { speed: 65, cabinClimate: true, batteryCooling: true }, anim: { temperature: [-10, 40] }, spin: 0 },
  ],
  build({ stage, openDetail }) {
    const road = makeResearchRoad(stage);
    const weather = makeWeather(stage);
    const vehicle = makeResearchVehicle(stage, { labels: false, opacity: .13 });
    const cabinAir = box(3.6, 1.0, 2.8, M.ghost(0x9bd4ff, .13));
    cabinAir.position.set(.25, 2.15, 0); vehicle.root.add(cabinAir);
    const heatPath = pathOf([[-.5, .95, 0], [1.6, 1.2, 0], [1.8, 1.9, 0], [.35, 2.3, 0]]);
    const coolingPath = pathOf([[-.3, .9, .9], [1.6, 1.0, 1.15], [3.5, 1.15, 1.15], [3.8, 1.3, 0]]);
    const cabinFlow = flowDots(heatPath, 25, .075, 0xffb547);
    const batteryFlow = flowDots(coolingPath, 25, .065, 0x87ddff);
    vehicle.root.add(cabinFlow, batteryFlow);
    const radiator = box(.22, .85, 1.5, M.metal(0x7697a5)); radiator.position.set(3.8, 1.1, 0);
    vehicle.root.add(radiator);
    const labelCabin = stage.label('座舱温控', [.3, 3.1, 0], vehicle.root);
    const labelBattery = stage.label('电池', [-.25, .85, 2.2], vehicle.root, 'hot');
    const thermometer = stage.label('', [2.8, 5.4, -1.4], stage.root);
    thermometer.element.classList.add('scene-weather-temperature');
    thermometer.element.style.cssText += ';background:transparent;border:0;box-shadow:none;text-align:center;padding:6px 10px;color:#d8edff;';
    let v = 60 / 3.6, drawn = '';
    let snapshot = null;
    return {
      update(dt, s) {
        const key = `${s.temperature}|${s.speed}|${s.cabinClimate}|${s.batteryCooling}`;
        if (key !== drawn) { snapshot = thermalSnapshot(s); drawn = key; }
        const { thermal, power } = snapshot;
        v = approach(v, s.speed / 3.6, 3, Math.max(0, dt));
        const { cold, hot } = weather.update(dt, s.temperature, v);
        road.update(dt, v, cold, hot);
        vehicle.setXray(s.xray);
        vehicle.setFlow(power.motorDcW);
        vehicle.updatePose(dt, v);
        const heating = thermal.heaterW > thermal.acW;
        const cabinW = thermal.heaterW + thermal.acW;
        cabinFlow.visible = cabinW > 1 && s.xray;
        cabinFlow.material.color.set(heating ? 0xffb069 : 0x81dcff);
        cabinFlow.step(dt, .4 + Math.min(3, cabinW / 1500));
        batteryFlow.visible = thermal.coolerW > 1 && s.xray;
        batteryFlow.step(dt, .45 + Math.min(2, thermal.coolerW / 800));
        cabinAir.visible = s.xray && s.cabinClimate && cabinW > 1;
        cabinAir.material.color.set(heating ? 0xff944f : 0x6ccfff);
        cabinAir.material.opacity = .06 + clamp(cabinW / 5000, 0, 1) * .10;
        labelCabin.element.innerHTML = !s.cabinClimate ? '座舱温控关闭' : cabinW > 1 ? `${heating ? '暖风' : '冷气'} ${(cabinW / 1000).toFixed(2)} kW` : '座舱暂不加热 / 制冷';
        labelBattery.element.innerHTML = thermal.coolerW > 1 ? `电池冷却 ${(thermal.coolerW / 1000).toFixed(2)} kW` : '电池冷却关闭';
        labelCabin.visible = labelBattery.visible = s.xray;
        // Narrow layouts already show the temperature in the fixed readout.
        // Keep the extra world-space label away from that card on desktop.
        thermometer.visible = stage.host.clientWidth >= 700;
        thermometer.element.innerHTML = `<b style="font:500 38px/1.15 var(--mono);color:${cold > .05 ? '#b9e5ff' : hot > .05 ? '#ffb186' : '#dce9ed'}">${Math.round(s.temperature)}°C</b><br><span style="font-size:12px;letter-spacing:.12em">${cold > .05 ? '寒冷 · 雪天示意' : hot > .05 ? '炎热 · 热浪示意' : '温和环境'}</span>`;
      },
      readout(s) {
        const { thermal, power } = thermalSnapshot(s);
        return `<div class="big">当前状态 ${Math.round(s.temperature)}℃ · ${Math.round(s.speed)} km/h</div>
          <div class="row"><span>功率分配：驱动 / 座舱 / 冷却</span><b>${(power.motorDcW / 1000).toFixed(1)} / ${((thermal.acW + thermal.heaterW) / 1000).toFixed(1)} / ${(thermal.coolerW / 1000).toFixed(1)} kW</b></div>
          <small>当前交互：参考参数、SOC80%、电池和座舱均为${Math.round(s.temperature)}℃。论文对照：校准参数、SOC94%、座舱初温24℃；完整行程结论见右侧。</small>`;
      },
      showClimateEvidence() { openDetail?.(climateEvidence()); },
      dispose() { weather.dispose(); vehicle.dispose(); },
    };
  },
};
