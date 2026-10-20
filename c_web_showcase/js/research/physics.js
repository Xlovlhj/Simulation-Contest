// Current-state interactive calculations adapted from the project's model.py.
// These functions DO NOT integrate a drive cycle or generate a new research result.
const response = await fetch(new URL('../../data/research-parameters.json', import.meta.url));
if (!response.ok) throw new Error('研究参数载入失败');
const DATA = await response.json();
export const PARAMS = Object.freeze({ reference: DATA.reference, calibrated: DATA.calibrated });
export const CURVES = DATA.curves;
export const PROTOCOL = DATA.protocol;
export const RULES = Object.freeze({ baseline: PROTOCOL.baseline, optimized: DATA.optimizedRule });
export const SOURCES = DATA.sources;
export const INTERACTION_SCOPE = '滑条当前状态的机理计算，不是完整CLTC循环预测';
const clamp = (x, lo, hi) => Math.max(lo, Math.min(hi, x));
const params = (model = 'reference') => PARAMS[model] || PARAMS.reference;

export function interpolate(x, xs, ys) {
  if (x <= xs[0]) return ys[0];
  if (x >= xs.at(-1)) return ys.at(-1);
  for (let i = 1; i < xs.length; i++) {
    if (x <= xs[i]) return ys[i - 1] + (ys[i] - ys[i - 1]) * (x - xs[i - 1]) / (xs[i] - xs[i - 1]);
  }
  return ys.at(-1);
}

export function batteryAt({ soc = 0.8, batteryC = 25, powerW = 0, model = 'reference' } = {}) {
  const p = params(model);
  const valid = Number.isFinite(soc) && soc >= 0 && soc <= 1
    && batteryC >= CURVES.valid_temperature_c[0] && batteryC <= CURVES.valid_temperature_c[1];
  const ocvV = interpolate(soc, CURVES.ocv_soc, CURVES.ocv_cell_v) * CURVES.series_cells + (p.ocv_offset_v || 0);
  const normalized = interpolate(soc, CURVES.resistance_soc, CURVES.resistance_soc_factor)
    / interpolate(0.5, CURVES.resistance_soc, CURVES.resistance_soc_factor);
  const resistanceOhm = p.resistance_ref_ohm * normalized * (1 + CURVES.resistance_temp_slope_per_c * (25 - batteryC));
  const discriminant = ocvV * ocvV - 4 * resistanceOhm * powerW;
  const feasible = valid && Number.isFinite(powerW) && discriminant >= 0 && resistanceOhm >= 0;
  const currentA = feasible ? 2 * powerW / (ocvV + Math.sqrt(discriminant)) : null;
  const voltageV = feasible ? ocvV - currentA * resistanceOhm : null;
  return {
    model, soc, batteryC, powerW, ocvV, resistanceOhm, currentA, voltageV,
    jouleHeatW: feasible ? currentA * currentA * resistanceOhm : null,
    nominalEnergyKwh: p.capacity_ah * p.nominal_voltage_v / 1000,
    nominalRemainingKwh: p.capacity_ah * p.nominal_voltage_v * soc / 1000,
    socRatePerS: feasible ? -currentA / (3600 * p.capacity_ah) : null,
    socRatePpPerMin: feasible ? -currentA / (3600 * p.capacity_ah) * 6000 : null,
    resistanceEndpointHold: soc < CURVES.resistance_soc[0] || soc > CURVES.resistance_soc.at(-1),
    feasible, reason: feasible ? '' : '所选状态超过Rint功率能力或参数有效范围',
    energyNote: 'Q×额定电压×SOC的标称估算，不是可用续航或积分后的电池能量',
    scope: INTERACTION_SCOPE,
  };
}

export function motorEnvelope({ speedKmh = 60, model = 'reference' } = {}) {
  const p = params(model);
  const speedMps = Math.max(0, speedKmh) / 3.6;
  const omega = speedMps * p.gear_ratio / p.wheel_radius_m;
  const rpm = omega * 60 / (2 * Math.PI);
  const maxTorqueNm = rpm > p.motor_speed_max_rpm ? 0
    : Math.min(p.motor_torque_max_nm, omega > 0 ? p.motor_power_max_w / omega : Infinity);
  return {
    rpm, omega, maxTorqueNm,
    maxWheelForceN: maxTorqueNm * p.gear_ratio * p.gear_efficiency / p.wheel_radius_m,
    maxPowerW: p.motor_power_max_w,
  };
}

export function effectiveRegen({ soc = 0.8, batteryC = 25, fraction, model = 'reference' } = {}) {
  const p = params(model);
  const requested = fraction ?? p.regen_fraction;
  const temperatureFactor = clamp((batteryC - p.regen_temperature_zero_c)
    / (p.regen_temperature_full_c - p.regen_temperature_zero_c), 0, 1);
  return { requested, temperatureFactor, effective: soc < p.regen_soc_cutoff ? requested * temperatureFactor : 0 };
}

function motorFlow(wheelW, speedMps, regenFraction, p) {
  const omega = speedMps * p.gear_ratio / p.wheel_radius_m;
  const rpm = omega * 60 / (2 * Math.PI);
  const loss = torque => CURVES.motor_loss.constant_w
    + CURVES.motor_loss.torque_coefficient * torque ** 2
    + CURVES.motor_loss.speed_coefficient * omega ** 2;
  const out = {
    rpm, torqueNm: 0, wheelW, motorDcW: 0, motorMechanicalW: 0,
    gearLossW: 0, motorLossW: 0, inverterLossW: 0, frictionBrakeW: 0, regenWheelW: 0,
    feasible: rpm <= p.motor_speed_max_rpm + 1e-8, reason: '',
  };
  if (Math.abs(wheelW) < 1e-10) return out;
  if (omega <= 0) return { ...out, feasible: false, reason: '静止状态不能产生非零轮端功率' };
  if (wheelW > 0) {
    const shaft = wheelW / p.gear_efficiency;
    const torque = shaft / omega;
    const motorLoss = loss(torque);
    const dc = (shaft + motorLoss) / p.inverter_efficiency;
    Object.assign(out, {
      motorDcW: dc, motorMechanicalW: shaft, torqueNm: torque,
      gearLossW: shaft - wheelW, motorLossW: motorLoss, inverterLossW: dc - shaft - motorLoss,
    });
    if (shaft > p.motor_power_max_w + 1e-8 || torque > p.motor_torque_max_nm + 1e-8)
      Object.assign(out, { feasible: false, reason: '所选坡度/加速度的牵引需求超过电机边界' });
  } else {
    const available = -wheelW;
    let recovered = Math.min(available * regenFraction, p.motor_power_max_w / p.gear_efficiency,
      p.motor_torque_max_nm * omega / p.gear_efficiency);
    const generation = w => {
      const shaft = w * p.gear_efficiency, heat = w ? loss(shaft / omega) : 0;
      return { generated: Math.max(0, (shaft - heat) * p.inverter_efficiency), shaft, heat };
    };
    let g = generation(recovered);
    if (g.generated > p.regen_dc_cap_w) {
      let lower = 0, upper = recovered;
      for (let i = 0; i < 40; i++) {
        const mid = (lower + upper) / 2;
        if (generation(mid).generated > p.regen_dc_cap_w) upper = mid; else lower = mid;
      }
      recovered = lower; g = generation(recovered);
    }
    if (g.generated <= 1e-8) { recovered = 0; g = { generated: 0, shaft: 0, heat: 0 }; }
    Object.assign(out, {
      motorDcW: -g.generated, motorMechanicalW: -g.shaft, torqueNm: -g.shaft / omega,
      gearLossW: recovered - g.shaft, motorLossW: g.heat,
      inverterLossW: Math.max(0, g.shaft - g.heat - g.generated),
      frictionBrakeW: available - recovered, regenWheelW: recovered,
    });
  }
  if (rpm > p.motor_speed_max_rpm + 1e-8) out.reason = '电机转速超过参数上限';
  return out;
}

export function powerAt({
  speedKmh = 60, mode = 'cruise', accel, slopePct = 0, massKg, ambientC = 25,
  batteryC = ambientC, cabinC = 24, soc = 0.8, model = 'reference',
  regenFraction, acW = 0, heaterW = 0, coolerW = 0,
} = {}) {
  const original = params(model), p = { ...original, mass_kg: massKg ?? original.mass_kg };
  const speedMps = Math.max(0, speedKmh) / 3.6;
  const angle = Math.atan(slopePct / 100);
  const rollingN = speedMps > 0 ? p.mass_kg * p.gravity_m_s2 * p.rolling_coefficient * Math.cos(angle) : 0;
  const dragN = 0.5 * p.air_density_kg_m3 * p.drag_coefficient * p.frontal_area_m2 * speedMps ** 2;
  const hillN = p.mass_kg * p.gravity_m_s2 * Math.sin(angle);
  const envelope = motorEnvelope({ speedKmh, model });
  const acceleration = accel ?? (mode === 'regen' ? -1.5 : mode === 'accel'
    ? (envelope.maxWheelForceN - rollingN - dragN - hillN) / (p.mass_kg * p.rotating_mass_factor) : 0);
  const inertiaN = p.rotating_mass_factor * p.mass_kg * acceleration;
  const wheelW = (rollingN + dragN + hillN + inertiaN) * speedMps;
  const regen = effectiveRegen({ soc, batteryC, fraction: regenFraction, model });
  const flow = motorFlow(wheelW, speedMps, regen.effective, p);
  const accessoryW = p.low_voltage_w + acW + heaterW + coolerW;
  const batteryW = flow.motorDcW + accessoryW;
  const battery = batteryAt({ soc, batteryC, powerW: batteryW, model });
  return {
    ...flow, ...battery, speedKmh, speedMps, accel: acceleration, slopePct,
    rollingN, dragN, hillN, inertiaN, roadLoadN: rollingN + dragN + hillN,
    wheelW, motorDcW: flow.motorDcW, accessoryW, batteryW,
    acW, heaterW, coolerW, lowVoltageW: p.low_voltage_w, ambientC, cabinC,
    regenRequested: regen.requested, regenEffective: regen.effective,
    instantKwh100: speedKmh > 0 ? batteryW / 1000 * 100 / speedKmh : null,
    feasible: flow.feasible && battery.feasible,
    reason: flow.reason || battery.reason,
    slopeNote: slopePct ? '坡度项mg·sinθ为交互教学扩展；论文CLTC基准为平路' : '',
    modeNote: mode === 'regen' && accel === undefined ? '当前减速度−1.5m/s²为交互示例设定，不是实测工况' : '',
    scope: INTERACTION_SCOPE,
  };
}

export function thermalAt({
  ambientC = 25, batteryC = ambientC, cabinC = 24, cabinTargetC = 24,
  cooler = 'auto', cabin = 'on', model = 'reference', strategy = 'baseline',
  powerW = 0, soc = 0.8, solarHeatW,
} = {}) {
  const p = params(model), rule = RULES[strategy] || RULES.baseline;
  const solar = solarHeatW ?? (model === 'calibrated' && ambientC >= 35 ? 500 : p.solar_heat_w);
  const cabinPassiveHeatW = p.cabin_environment_w_k * (ambientC - cabinC) + solar + p.occupant_heat_w;
  // One decision snapshot: no dwell memory, no future arrival target, no state integration.
  const requestedHeat = PROTOCOL.cabin_feedback_w_k * (cabinTargetC - cabinC) - cabinPassiveHeatW;
  const heaterW = cabin === 'off' ? 0 : Math.min(p.heater_max_w, Math.max(0, requestedHeat) / p.heater_efficiency);
  let acW = cabin === 'off' ? 0 : Math.min(p.ac_max_w, Math.max(0, -requestedHeat) / p.cooling_cop);
  const coolerOn = cooler === 'on' || (cooler === 'auto' && batteryC >= rule.cooling_on_c);
  const coolingHeatW = coolerOn ? p.cooler_conductance_w_k * Math.max(batteryC - p.coolant_temperature_c, 0) : 0;
  const coolerW = coolingHeatW / p.cooling_cop;
  acW = Math.min(acW, Math.max(0, PROTOCOL.compressor_total_max_w - coolerW));
  const battery = batteryAt({ soc, batteryC, powerW, model });
  const batteryEnvironmentW = p.battery_environment_w_k * (batteryC - ambientC);
  const batteryNetHeatW = (battery.jouleHeatW ?? 0) - batteryEnvironmentW - coolingHeatW;
  const cabinNetHeatW = cabinPassiveHeatW + p.heater_efficiency * heaterW - p.cooling_cop * acW;
  const regen = effectiveRegen({ soc, batteryC, fraction: rule.regen_fraction, model });
  return {
    ...battery, ambientC, batteryC, cabinC, cabinTargetC, coolerOn,
    acW, heaterW, coolerW, coolingHeatW, cabinPassiveHeatW, cabinNetHeatW,
    batteryEnvironmentW, batteryNetHeatW,
    batteryRateCPerMin: batteryNetHeatW / p.battery_heat_capacity_j_k * 60,
    cabinRateCPerMin: cabinNetHeatW / p.cabin_heat_capacity_j_k * 60,
    solarHeatW: solar, regenEffective: regen.effective, auxiliaryW: acW + heaterW + coolerW + p.low_voltage_w,
    scope: '给定温度与负载的瞬时热收支示意；未积分热历史，未执行5秒滞回/末端控制',
    batteryHeatingNote: '原模型无主动电池加热器；低温电池仅按焦耳热、环境换热与冷却项计算',
  };
}
