// CarClear's shared physics and parts: a representative 2.0 L petrol engine (slider-crank
// kinematics, valve timing, firing order and a torque curve), a five-speed gearbox with real
// tooth counts, road loads, a fuel model, an electric drive, and 3D builders for the car,
// wheels, gears and chart boards.
import { THREE, M, rod, beam, box, torus, tube, spring, clamp, lerp } from './kit.js';

export const TAU = Math.PI * 2;
export const G = 9.81;                 // m/s²
export const RHO = 1.2;                // air density, kg/m³ (sea level, about 20 °C)
export const KMH = 3.6;

// ---------------------------------------------------------------- the engine
// A 2.0 L inline four, square bore and stroke (86 × 86 mm gives 1,998 cm³, a very common
// layout), a 145 mm connecting rod (rod/crank ratio 3.4), compression ratio 10.5:1.
export const ENGINE = { bore: 0.086, stroke: 0.086, rod: 0.145, cyl: 4, cr: 10.5, idle: 800, redline: 6500 };
ENGINE.r = ENGINE.stroke / 2;
ENGINE.disp = ENGINE.cyl * Math.PI * (ENGINE.bore / 2) ** 2 * ENGINE.stroke;   // m³ (0.001998)

// Slider-crank: piston height above the crank centre at crank angle a (0 = top dead centre),
//   x(a) = r cos a + √(l² − r² sin² a).
export function pistonX(a, r = ENGINE.r, l = ENGINE.rod) { const s = Math.sin(a); return r * Math.cos(a) + Math.sqrt(l * l - r * r * s * s); }
// Piston velocity dx/dt for crank speed w (rad/s).
export function pistonV(a, w, r = ENGINE.r, l = ENGINE.rod) { const s = Math.sin(a), c = Math.cos(a); return -w * r * s * (1 + (r * c) / Math.sqrt(l * l - r * r * s * s)); }
export const meanPistonSpeed = (rpm) => (2 * ENGINE.stroke * rpm) / 60;             // m/s

// Firing order 1-3-4-2 with throws for cylinders 1 and 4 together and 2 and 3 opposite them
// (a flat-plane four). Each cylinder starts its power stroke this many crank degrees after
// cylinder 1: one firing every 180°.
export const FIRE_AT = [0, 540, 180, 360];                 // cylinders 1, 2, 3, 4
export const THROW = [0, 180, 180, 0];                     // crank-pin angle offsets, degrees
export const STROKES = ['Power', 'Exhaust', 'Intake', 'Compression'];
// Where cylinder i (0-based) is in its 720° cycle at crank angle th (degrees):
//   0–180 power, 180–360 exhaust, 360–540 intake, 540–720 compression.
export const cyclePhase = (th, i) => ((((th - FIRE_AT[i]) % 720) + 720) % 720);
export const strokeOf = (phi) => STROKES[Math.floor(phi / 180) % 4];
// Valve timing, typical of a road engine (crank degrees in the cycle above):
// exhaust opens 40° before bottom dead centre (140) and closes 10° after TDC (370);
// intake opens 10° before TDC (350) and closes 40° after BDC (580). The 20° where both are
// open is the "overlap". Lift is a smooth bump, max 9 mm.
export const VALVES = { exh: [140, 370], int: [350, 580], lift: 0.009 };
export function valveLift(phi, [open, close]) {
  let p = phi; if (p < open - 360) p += 720;
  if (p < open || p > close) return 0;
  const k = (p - open) / (close - open);
  return Math.sin(Math.PI * k) ** 2;                       // 0 → 1 → 0
}
// Spark 20° before top dead centre on the compression stroke (a typical part-load advance).
export const SPARK_AT = 700;

// Torque at full throttle (N·m) against rpm. A representative curve for a naturally aspirated
// 2.0 L petrol four, not one specific engine: about 200 N·m peak at 4,000 rpm and about 110 kW
// (148 hp) near 6,000 rpm, like many family cars of the 2000s and 2010s.
const TQ = [[800, 128], [1000, 135], [1500, 150], [2000, 165], [2500, 178], [3000, 188], [3500, 196], [4000, 200], [4500, 198], [5000, 193], [5500, 185], [6000, 175], [6500, 161], [7000, 143]];
// Smooth monotone-ish cubic (Catmull-Rom) through the table.
function cr(table, x) {
  if (x <= table[0][0]) return table[0][1];
  const n = table.length;
  if (x >= table[n - 1][0]) return table[n - 1][1];
  let i = 1; while (table[i][0] < x) i++;
  const p0 = table[Math.max(0, i - 2)], p1 = table[i - 1], p2 = table[i], p3 = table[Math.min(n - 1, i + 1)];
  const t = (x - p1[0]) / (p2[0] - p1[0]), h = p2[0] - p1[0];
  const m1 = ((p2[1] - p0[1]) / (p2[0] - p0[0])) * h, m2 = ((p3[1] - p1[1]) / (p3[0] - p1[0])) * h;
  const t2 = t * t, t3 = t2 * t;
  return (2 * t3 - 3 * t2 + 1) * p1[1] + (t3 - 2 * t2 + t) * m1 + (-2 * t3 + 3 * t2) * p2[1] + (t3 - t2) * m2;
}
export const torqueAt = (rpm) => (rpm < ENGINE.idle * 0.5 ? 0 : cr(TQ, rpm));
// Power: P = T × ω. In kW: N·m × rpm / 9,549 (9,549 = 60,000 / 2π).
export const kW = (nm, rpm) => (nm * rpm) / 9549;
export const powerAt = (rpm) => kW(torqueAt(rpm), rpm);
// Imperial: 1 N·m = 0.7376 lb·ft; hp = lb·ft × rpm / 5,252 (5,252 = 33,000 / 2π); 1 hp = 745.7 W.
export const LBFT = 0.73756, HPW = 745.7;
export const hp = (kw) => (kw * 1000) / HPW;
export const PEAK = (() => { let t = [0, 0], p = [0, 0]; for (let r = 800; r <= 6500; r += 10) { if (torqueAt(r) > t[1]) t = [r, torqueAt(r)]; if (powerAt(r) > p[1]) p = [r, powerAt(r)]; } return { tRpm: t[0], t: t[1], pRpm: p[0], p: p[1] }; })();

// Fuel: petrol holds about 43 MJ/kg × 0.745 kg/L ≈ 32 MJ, or 8.9 kWh, per litre.
export const PETROL_KWH_L = 8.9;
export const AFR = 14.7;              // stoichiometric air:fuel ratio for petrol, by mass
// Air drawn in per cylinder per intake stroke at full throttle: swept volume × air density × 90%
// volumetric efficiency.
export const airPerStroke = () => (ENGINE.disp / ENGINE.cyl) * RHO * 0.9;   // kg (≈ 0.54 g)

// Fuel power by a Willans-line model: fuel = (brake power + friction) / indicated efficiency.
// Friction mean effective pressure from Heywood (Internal Combustion Engine Fundamentals, 1988):
// fmep ≈ 0.97 + 0.15 (N/1000) + 0.05 (N/1000)² bar, plus throttling (pumping) losses at part load
// of up to 0.3 bar. Indicated efficiency 0.40. This gives 27–33% brake efficiency at full load,
// 20–25% when cruising and much less when idling along, like a real petrol engine: about
// 6.5 L/100 km at a steady 100 km/h and 8.7 at 130 km/h for this car.
export function fuelPower(brakeKW, rpm) {
  const n = rpm / 1000, bmepMax = (torqueAt(rpm) * 4 * Math.PI) / ENGINE.disp / 1e5;      // bar
  const bmep = (brakeKW * 1000 * 120) / (ENGINE.disp * rpm) / 1e5;
  const fmep = 0.97 + 0.15 * n + 0.05 * n * n + 0.3 * (1 - clamp(bmep / bmepMax, 0, 1));
  const fricKW = (fmep * 1e5 * ENGINE.disp * rpm) / 120 / 1000;
  return (brakeKW + fricKW) / 0.4;
}

// ---------------------------------------------------------------- gearbox and wheels
// A two-shaft five-speed: every pair shares the same centre distance, so the tooth counts of each
// pair add up to 46. Ratio = driven teeth ÷ driving teeth.
export const GEARS = [[10, 36], [15, 31], [19, 27], [23, 23], [26, 20]];   // [input, output] teeth
export const RATIOS = GEARS.map(([a, b]) => b / a);                         // 3.60 2.07 1.42 1.00 0.77
export const FINAL_TEETH = [10, 41], FINAL = 41 / 10;                        // final drive 4.10
export const TYRE_R = 0.31;           // m: dynamic rolling radius of a 205/55 R16 tyre (632 mm across)
export const ETA_DRIVE = 0.9;         // gearbox, final drive and joints together pass on about 90%
export const wheelRpm = (engineRpm, g) => engineRpm / (RATIOS[g] * FINAL);
export const speedAt = (engineRpm, g) => (wheelRpm(engineRpm, g) * TAU * TYRE_R) / 60;      // m/s
export const rpmAt = (v, g) => ((v / (TAU * TYRE_R)) * 60) * RATIOS[g] * FINAL;
// Force the tyres push with at full throttle in gear g at engine speed rpm.
export const wheelTorque = (rpm, g, throttle = 1) => torqueAt(rpm) * throttle * RATIOS[g] * FINAL * ETA_DRIVE;
export const wheelForce = (rpm, g, throttle = 1) => wheelTorque(rpm, g, throttle) / TYRE_R;

// ---------------------------------------------------------------- road loads
// A mid-size family car: 1,300 kg with a driver, rolling resistance 0.012, drag coefficient 0.30
// and 2.2 m² of frontal area.
export const CAR = { mass: 1300, crr: 0.012, cd: 0.3, area: 2.2 };
export function loads(v, { mass = CAR.mass, slope = 0, crr = CAR.crr, cda = CAR.cd * CAR.area } = {}) {
  const th = Math.atan(slope / 100);
  const roll = crr * mass * G * Math.cos(th), hill = mass * G * Math.sin(th), drag = 0.5 * RHO * cda * v * v;
  return { roll, hill, drag, total: roll + hill + drag };
}
// Best full-throttle force at road speed v, trying every gear that keeps the engine between
// idle and the redline. Below first gear's idle speed, the clutch slips at 2,500 rpm.
export function bestForce(v, throttle = 1) {
  let best = { f: 0, g: 0, rpm: ENGINE.idle };
  for (let g = 0; g < 5; g++) {
    let rpm = rpmAt(v, g);
    if (rpm > ENGINE.redline) continue;
    if (rpm < ENGINE.idle) { if (g) continue; rpm = 2500; }
    const f = wheelForce(rpm, g, throttle);
    if (f > best.f) best = { f, g, rpm };
  }
  return best;
}
// Rotating parts (wheels, shafts, flywheel) add apparent mass, much more in low gears:
// factor ≈ 1.04 + 0.0015 × (overall ratio)² (a common rule of thumb in vehicle dynamics texts).
export const massFactor = (g) => 1.04 + 0.0015 * (RATIOS[g] * FINAL) ** 2;

// 0–100 km/h at full throttle: step the car forward in time, changing up at the redline
// (0.35 s with no drive while shifting). Grip limit: 50% of the weight sits on the driven
// wheels and the tyres grip with μ = 1.0.
export function zeroTo100({ mass = CAR.mass, slope = 0 } = {}) {
  let v = 0, t = 0, g = 0, shift = 0;
  const grip = 0.5 * mass * G * 1.0, dt = 0.01;
  while (v < 100 / KMH && t < 60) {
    let rpm = rpmAt(v, g);
    if (rpm >= ENGINE.redline && g < 4) { g++; shift = 0.35; rpm = rpmAt(v, g); }
    const drive = shift > 0 ? 0 : Math.min(grip, wheelForce(Math.max(rpm, 2500), g));
    const a = (drive - loads(v, { mass, slope }).total) / (mass * massFactor(g));
    if (a <= 0 && shift <= 0) return Infinity;
    v = Math.max(0, v + a * dt); t += dt; shift -= dt;
  }
  return t >= 60 ? Infinity : t;
}
// Top speed: where the best full-throttle force equals the road loads.
export function topSpeed(opts = {}) {
  let v = 1;
  for (; v < 90; v += 0.05) if (bestForce(v).f < loads(v, opts).total) break;
  return v;
}
// Steady cruising: the highest gear that keeps the engine above 1,300 rpm and can still give the
// power needed. Returns the gear, rpm, brake power (kW) and fuel use (L/100 km).
export function cruise(v, opts = {}) {
  const need = (loads(v, opts).total * v) / ETA_DRIVE / 1000;                 // kW at the crank
  for (let g = 4; g >= 0; g--) {
    const rpm = Math.max(rpmAt(v, g), g === 0 ? 1300 : 0);
    if (rpm < 1300 && g > 0) continue;
    if (rpm > ENGINE.redline) continue;
    if (need > powerAt(rpm) * 0.98) continue;
    const fuelKW = fuelPower(Math.max(0, need), rpm);
    const l100 = v > 0.5 ? ((fuelKW * (100 / (v * KMH))) / PETROL_KWH_L) : 0;
    return { g, rpm, need, fuelKW, l100, eff: need > 0 ? need / fuelKW : 0 };
  }
  return null;
}

// ---------------------------------------------------------------- electric drive
// A typical compact electric car: 60 kWh battery, a 150 kW motor with 310 N·m from standstill,
// one reduction gear of 11.5:1 and a top motor speed of 16,000 rpm (160 km/h), 1,800 kg.
// Battery to wheel efficiency about 88%; 0.6 kW for lights, electronics and climate.
export const EV = { kwh: 60, kw: 150, nm: 310, ratio: 11.5, maxRpm: 16000, mass: 1800, crr: 0.010, cd: 0.27, area: 2.25, eta: 0.88, aux: 0.6, regen: 0.65 };
EV.baseRpm = (EV.kw * 9549) / EV.nm;                              // ≈ 4,620 rpm: full torque up to here, then full power
export const evTorque = (rpm) => (rpm > EV.maxRpm ? 0 : rpm <= EV.baseRpm ? EV.nm : (EV.kw * 9549) / rpm);
export const evRpmAt = (v) => ((v / (TAU * TYRE_R)) * 60) * EV.ratio;
export const evForce = (v) => (evTorque(evRpmAt(v)) * EV.ratio * 0.95) / TYRE_R;       // 95%: one gear pair
export const evLoads = (v) => loads(v, { mass: EV.mass, crr: EV.crr, cda: EV.cd * EV.area });
export function evCruise(v) {
  const wheelKW = (evLoads(v).total * v) / 1000;
  const batKW = wheelKW / EV.eta + EV.aux;
  return { wheelKW, batKW, kwh100: v > 0.5 ? batKW * (100 / (v * KMH)) : 0 };
}

// 0–100 km/h for the electric car: full torque from standstill, then full power. Same grip limit
// as the petrol car (50% of the weight on the driven wheels, μ = 1.0), rotating-mass factor 1.05.
export function evZeroTo100() {
  let v = 0, t = 0; const grip = 0.5 * EV.mass * G, dt = 0.01;
  while (v < 100 / KMH && t < 60) { const a = (Math.min(grip, evForce(v)) - evLoads(v).total) / (EV.mass * 1.05); v += a * dt; t += dt; }
  return t;
}

// ---------------------------------------------------------------- 3D: small helpers
export function vrod(y0, y1, r0, r1 = r0, mat, seg = 24) { const m = rod(y0, y1, r0, r1, mat, seg); m.position.set(0, m.position.x, 0); m.rotation.z = Math.PI / 2; return m; }
export function zrod(z0, z1, r0, r1 = r0, mat, seg = 24) { const m = rod(z0, z1, r0, r1, mat, seg); m.position.set(0, 0, m.position.x); m.rotation.y = -Math.PI / 2; return m; }
export function rbox(w, h, d, r, mat) {
  // A box with rounded vertical edges, made by extruding a rounded rectangle along Z.
  const s = new THREE.Shape(), x = w / 2 - r, y = h / 2 - r;
  s.moveTo(-x, -h / 2); s.lineTo(x, -h / 2); s.absarc(x, -y, r, -Math.PI / 2, 0); s.lineTo(w / 2, y); s.absarc(x, y, r, 0, Math.PI / 2);
  s.lineTo(-x, h / 2); s.absarc(-x, y, r, Math.PI / 2, Math.PI); s.lineTo(-w / 2, -y); s.absarc(-x, -y, r, Math.PI, Math.PI * 1.5);
  const g = new THREE.ExtrudeGeometry(s, { depth: d, bevelEnabled: false, curveSegments: 8 }); g.translate(0, 0, -d / 2);
  const m = new THREE.Mesh(g, mat); m.castShadow = true; m.receiveShadow = true; return m;
}

// A spur gear in the XY plane (axis Z), module m: pitch radius m·z/2, teeth m tall above it.
export function spurGeometry(z, m, width, hole = 0.3) {
  const rp = (m * z) / 2, ra = rp + m, rr = rp - 1.25 * m, pitch = TAU / z;
  const wr = 0.29 * pitch, wt = 0.15 * pitch;
  const s = new THREE.Shape();
  for (let i = 0; i < z; i++) {
    const c = i * pitch;
    const P = (r, a) => [r * Math.cos(a), r * Math.sin(a)];
    const pts = [P(rr, c - wr), P(ra, c - wt), P(ra, c + wt), P(rr, c + wr)];
    if (i === 0) s.moveTo(...pts[0]); else s.lineTo(...pts[0]);
    s.lineTo(...pts[1]); s.lineTo(...pts[2]); s.lineTo(...pts[3]);
    const a0 = c + wr, a1 = c + pitch - wr;
    for (let k = 1; k <= 3; k++) s.lineTo(...P(rr, a0 + ((a1 - a0) * k) / 4));
  }
  if (hole > 0) { const h = new THREE.Path(); h.absarc(0, 0, rr * hole, 0, TAU, true); s.holes.push(h); }
  const g = new THREE.ExtrudeGeometry(s, { depth: width, bevelEnabled: true, bevelThickness: width * 0.08, bevelSize: m * 0.12, bevelSegments: 1, curveSegments: 6 });
  g.translate(0, 0, -width / 2);
  return g;
}
// Rotation of a driven gear so its teeth mesh with a driver. The driver (z1 teeth) turns by a1;
// the driven gear (z2) sits in direction dir (radians) from the driver. Returns the driven angle.
export function meshAngle(a1, z1, z2, dir) {
  // A driver tooth points along dir when a1 + 2πk/z1 = dir. The driven gear must then show a gap
  // along dir + π: a2 = dir + π − π/z2 at that moment, and it turns −z1/z2 times as fast.
  return -(z1 / z2) * (a1 - dir) + dir + Math.PI - Math.PI / z2;
}
// A bevel gear: a toothed cone frustum along Y (teeth as ridges). Big end at the bottom.
export function bevelGeometry(rBig, rSmall, h, z) {
  const g = new THREE.CylinderGeometry(rSmall, rBig, h, z * 4, 1, false);
  const p = g.attributes.position, v = new THREE.Vector3();
  for (let i = 0; i < p.count; i++) {
    v.fromBufferAttribute(p, i);
    const r = Math.hypot(v.x, v.z); if (r < 1e-4) continue;
    const a = Math.atan2(v.z, v.x), k = Math.floor(((a / TAU) * z * 4 + 0.5 + 1e6)) % 4;
    const bump = k === 1 || k === 2 ? 1.1 : 0.94;
    v.x *= bump; v.z *= bump; p.setXYZ(i, v.x, v.y, v.z);
  }
  g.computeVertexNormals();
  return g;
}

// ---------------------------------------------------------------- 3D: wheels
// A road wheel with its axle along Z. The whole thing sits in `group` (steer it about Y);
// `spin` turns. Includes a brake disc and a calliper that don't spin. R in scene units.
export function makeWheel(R = 0.775, width = 0.5, { side = 1, rim = 0xc9ced8, tyre = 0x1c1e24 } = {}) {
  const group = new THREE.Group(), spin = new THREE.Group(); group.add(spin);
  const rIn = R * 0.66;
  const prof = [[rIn, -width / 2], [R * 0.93, -width / 2], [R, -width * 0.32], [R, width * 0.32], [R * 0.93, width / 2], [rIn, width / 2]];
  const tg = new THREE.LatheGeometry(prof.map(([r, y]) => new THREE.Vector2(r, y)), 48);
  tg.rotateX(Math.PI / 2);
  const tyreM = new THREE.Mesh(tg, M.matte(tyre, { roughness: 0.92, side: THREE.DoubleSide })); tyreM.castShadow = true; spin.add(tyreM);
  // Tread blocks so the spin shows.
  const tread = new THREE.InstancedMesh(new THREE.BoxGeometry(0.06, 0.035, width * 0.8), M.matte(0x33363e), 28);
  const o = new THREE.Object3D();
  for (let i = 0; i < 28; i++) { const a = (i / 28) * TAU; o.position.set(Math.cos(a) * R, Math.sin(a) * R, 0); o.rotation.set(0, 0, a); o.updateMatrix(); tread.setMatrixAt(i, o.matrix); }
  spin.add(tread);
  const rimMat = M.metal(rim, { roughness: 0.25 });
  const barrel = new THREE.Mesh(new THREE.CylinderGeometry(rIn, rIn, width * 0.9, 40, 1, true), M.metal(0x8a93a3, { side: THREE.DoubleSide })); barrel.rotation.x = Math.PI / 2; spin.add(barrel);
  const face = side * width * 0.3;
  for (let k = 0; k < 5; k++) { const a = (k / 5) * TAU; const sp = beam([Math.cos(a) * 0.12, Math.sin(a) * 0.12, face], [Math.cos(a) * rIn, Math.sin(a) * rIn, face], R * 0.06, rimMat, 8); spin.add(sp); }
  const hub = new THREE.Mesh(new THREE.CylinderGeometry(R * 0.16, R * 0.16, width * 0.4, 20), rimMat); hub.rotation.x = Math.PI / 2; hub.position.z = face * 0.8; spin.add(hub);
  const disc = new THREE.Mesh(new THREE.CylinderGeometry(rIn * 0.86, rIn * 0.86, 0.05, 36), M.metal(0x9aa0aa, { roughness: 0.5 })); disc.rotation.x = Math.PI / 2; disc.position.z = -side * width * 0.12; spin.add(disc);
  const caliper = box(0.26, 0.36, 0.16, M.plastic(0xd8412f)); caliper.position.set(-rIn * 0.62, rIn * 0.3, -side * width * 0.12); group.add(caliper);
  return { group, spin, caliper, disc };
}

// ---------------------------------------------------------------- 3D: the car
// Scene units: 1 = 0.4 m. Forward is +X, up is +Y, the car's right side is +Z.
// Wheelbase 2.64 m, track 1.55 m, 4.4 m long, 1.8 m wide, 1.45 m tall.
export const CARU = { u: 0.4, axle: 3.3, track: 1.94, R: TYRE_R / 0.4, W: 205 / 400 };
function bodyShape() {
  const s = new THREE.Shape(), Y0 = 0.5, rA = 0.98, cy = CARU.R, dx = Math.sqrt(rA * rA - (cy - Y0) ** 2), da = Math.asin((cy - Y0) / rA);
  s.moveTo(-5.4, Y0);
  s.lineTo(-CARU.axle - dx, Y0); s.absarc(-CARU.axle, cy, rA, Math.PI + da, -da, true);
  s.lineTo(CARU.axle - dx, Y0); s.absarc(CARU.axle, cy, rA, Math.PI + da, -da, true);
  s.lineTo(5.35, Y0 + 0.05);
  s.quadraticCurveTo(5.62, 0.9, 5.6, 1.3);
  s.quadraticCurveTo(5.58, 1.72, 5.2, 1.8);
  s.lineTo(2.3, 2.15);
  s.lineTo(-3.7, 2.2);
  s.lineTo(-5.15, 2.25);
  s.quadraticCurveTo(-5.5, 2.2, -5.52, 1.8);
  s.lineTo(-5.55, 1.05);
  s.quadraticCurveTo(-5.55, 0.55, -5.4, Y0);
  return s;
}
function cabinShape() {
  const s = new THREE.Shape();
  s.moveTo(2.45, 2.1);
  s.quadraticCurveTo(1.3, 3.0, 0.7, 3.5);
  s.quadraticCurveTo(0.45, 3.62, 0.0, 3.63);
  s.lineTo(-1.8, 3.6);
  s.quadraticCurveTo(-2.2, 3.58, -2.5, 3.3);
  s.lineTo(-3.85, 2.15);
  s.lineTo(2.45, 2.1);
  return s;
}
// Pinch an extruded side profile in plan view so it looks like a car, not a slab: the sides lean
// in towards the top (tumblehome) and the nose and tail are rounded.
function shapeBody(geo, { y0 = 0.5, y1 = 3.6, lean = 0.16, ends = 4.3 } = {}) {
  const p = geo.attributes.position;
  for (let i = 0; i < p.count; i++) {
    const x = p.getX(i), y = p.getY(i), z = p.getZ(i);
    const up = clamp((y - y0) / (y1 - y0), 0, 1), end = clamp((Math.abs(x) - ends) / 1.3, 0, 1);
    const k = (1 - lean * up * up) * (1 - 0.3 * end * end);
    p.setZ(i, z * k);
  }
  geo.computeVertexNormals();
  return geo;
}
function roofShape() {
  const s = new THREE.Shape();
  s.moveTo(0.95, 3.38);
  s.quadraticCurveTo(0.45, 3.64, 0.0, 3.65);
  s.lineTo(-1.8, 3.62);
  s.quadraticCurveTo(-2.2, 3.6, -2.55, 3.28);
  s.lineTo(-2.35, 3.24);
  s.quadraticCurveTo(-2.1, 3.46, -1.8, 3.47);
  s.lineTo(0.0, 3.5);
  s.quadraticCurveTo(0.4, 3.49, 0.8, 3.3);
  s.lineTo(0.95, 3.38);
  return s;
}
export function makeBody({ color = 0xe8553f } = {}) {
  const g = new THREE.Group();
  const paint = new THREE.MeshPhysicalMaterial({ color, metalness: 0.35, roughness: 0.32, clearcoat: 1, clearcoatRoughness: 0.15, transparent: true, opacity: 1, side: THREE.DoubleSide });
  const bg = new THREE.ExtrudeGeometry(bodyShape(), { depth: 4.3, bevelEnabled: true, bevelThickness: 0.12, bevelSize: 0.1, bevelSegments: 3, curveSegments: 24, steps: 1 });
  bg.translate(0, 0, -2.15);
  shapeBody(bg, { y0: 1.2, y1: 2.3, lean: 0.06 });
  const shell = new THREE.Mesh(bg, paint); shell.castShadow = true;
  const edges = new THREE.LineSegments(new THREE.EdgesGeometry(bg, 25), new THREE.LineBasicMaterial({ color: 0xffb2a0, transparent: true, opacity: 0 }));
  shell.add(edges);
  const glassMat = new THREE.MeshPhysicalMaterial({ color: 0x1a2a3c, metalness: 0.2, roughness: 0.08, transparent: true, opacity: 0.78, side: THREE.DoubleSide });
  const cg = new THREE.ExtrudeGeometry(cabinShape(), { depth: 3.9, bevelEnabled: true, bevelThickness: 0.14, bevelSize: 0.14, bevelSegments: 3, curveSegments: 16 });
  cg.translate(0, 0, -1.95);
  shapeBody(cg, { y0: 2.1, y1: 3.7, lean: 0.24, ends: 9 });
  const cabin = new THREE.Mesh(cg, glassMat); cabin.castShadow = true;
  const rg = new THREE.ExtrudeGeometry(roofShape(), { depth: 3.9, bevelEnabled: true, bevelThickness: 0.1, bevelSize: 0.05, bevelSegments: 2, curveSegments: 16 });
  rg.translate(0, 0.02, -1.95);
  shapeBody(rg, { y0: 2.1, y1: 3.7, lean: 0.24, ends: 9 });
  const roof = new THREE.Mesh(rg, paint); roof.castShadow = true; cabin.add(roof);
  const cEdges = new THREE.LineSegments(new THREE.EdgesGeometry(cg, 25), new THREE.LineBasicMaterial({ color: 0x9ab8d8, transparent: true, opacity: 0 }));
  cabin.add(cEdges);
  // Lights, and a dark bumper strip.
  for (const z of [-1.45, 1.45]) {
    const hl = box(0.12, 0.26, 0.62, M.glow(0xfff4d6)); hl.position.set(5.5, 1.45, z); shell.add(hl);
    const tl = box(0.1, 0.24, 0.62, M.glow(0xff3b30)); tl.position.set(-5.55, 1.75, z); shell.add(tl);
  }
  const bumperMat = M.matte(0x23262f);
  const fb = box(0.2, 0.3, 3.6, bumperMat); fb.position.set(5.52, 0.8, 0); shell.add(fb);
  const rb = box(0.2, 0.3, 3.6, bumperMat); rb.position.set(-5.5, 0.8, 0); shell.add(rb);
  g.add(shell, cabin);
  const set = (op) => {
    paint.opacity = op; paint.depthWrite = op > 0.9; glassMat.opacity = Math.min(0.78, op * 0.78);
    glassMat.depthWrite = op > 0.9; shell.castShadow = cabin.castShadow = roof.castShadow = op > 0.5;
    bumperMat.transparent = op < 0.9; bumperMat.opacity = Math.max(op, 0.15);
    edges.material.opacity = (1 - op) * 0.7; cEdges.material.opacity = (1 - op) * 0.55;
  };
  return { group: g, shell, cabin, paint, glass: glassMat, setOpacity: set };
}

// Four wheels at the corners. Returns the wheels in the order front-left, front-right,
// rear-left, rear-right (left is −Z).
export function makeWheels() {
  const out = [];
  for (const [x, z] of [[CARU.axle, -CARU.track], [CARU.axle, CARU.track], [-CARU.axle, -CARU.track], [-CARU.axle, CARU.track]]) {
    const w = makeWheel(CARU.R, CARU.W, { side: Math.sign(z) });
    w.group.position.set(x, CARU.R, z);
    out.push(w);
  }
  return out;
}

// ---------------------------------------------------------------- 3D: the engine block (small, for the car)
// An inline four seen from outside: block, head, cam cover, sump, pulleys and four pistons that
// can bob (for the X-ray). Crank along X. Origin at the crank centre.
export function makeMiniEngine() {
  const g = new THREE.Group();
  const iron = M.metal(0x7c8594, { roughness: 0.45, metalness: 0.7, transparent: true, opacity: 1 });
  const bl = box(1.8, 0.75, 0.8, iron); bl.position.y = 0.25;
  const head = box(1.75, 0.3, 0.72, M.metal(0xa4adbb, { roughness: 0.35 })); head.position.y = 0.78;
  const cover = rbox(1.7, 0.2, 0.52, 0.08, M.plastic(0x2a2e37)); cover.rotation.x = Math.PI / 2; cover.position.y = 1.02;
  const sump = box(1.6, 0.25, 0.6, M.metal(0x4b525e)); sump.position.y = -0.25;
  const pulley = rod(0.9, 0.98, 0.18, 0.18, M.metal(0x2a2e37)); pulley.position.y = 0;
  const intake = new THREE.Group();
  for (let i = 0; i < 4; i++) intake.add(tube([[-0.66 + i * 0.44, 0.78, -0.36], [-0.66 + i * 0.44, 0.95, -0.62], [-0.66 + i * 0.44 * 0.7 - 0.1, 0.7, -0.78]], 0.055, M.plastic(0x3a3f4b), false, 16));
  const plenum = rod(-0.9, 0.7, 0.13, 0.13, M.plastic(0x3a3f4b)); plenum.position.set(-0.1, 0.62, -0.8); intake.add(plenum);
  const pistons = [];
  const pmat = M.metal(0xdfe4ec);
  for (let i = 0; i < 4; i++) { const p = rod(-0.13, 0.13, 0.16, 0.16, pmat, 20); p.rotation.set(0, 0, Math.PI / 2); p.position.set(-0.66 + i * 0.44, 0.35, 0); p.visible = false; g.add(p); pistons.push(p); }
  g.add(bl, head, cover, sump, pulley, intake);
  g.iron = iron;
  g.setXray = (on) => { iron.opacity = on ? 0.3 : 1; iron.depthWrite = !on; pistons.forEach((p) => { p.visible = on; }); };
  g.setCrank = (a) => pistons.forEach((p, i) => { p.position.y = 0.3 + 0.12 * Math.cos(a + (i === 1 || i === 2 ? Math.PI : 0)); });
  return g;
}

// ---------------------------------------------------------------- charts
// A dark board with plot axes. Returns helpers mapping data to canvas pixels.
export function plotFrame(g, w, h, { x0, x1, y0, y1, left = 86, right = 30, top = 70, bottom = 64, xTicks = [], yTicks = [], xFmt = String, yFmt = String, title = '', xLabel = '', yLabel = '', y2 = null }) {
  g.clearRect(0, 0, w, h);
  g.fillStyle = 'rgba(8,10,16,.86)'; g.fillRect(0, 0, w, h);
  const pw = w - left - right, ph = h - top - bottom;
  const X = (x) => left + ((x - x0) / (x1 - x0)) * pw, Y = (y) => top + ph - ((y - y0) / (y1 - y0)) * ph;
  g.lineWidth = 1.5; g.strokeStyle = 'rgba(255,255,255,.12)'; g.fillStyle = 'rgba(255,255,255,.6)'; g.font = '20px sans-serif';
  g.textAlign = 'right';
  for (const t of yTicks) { g.beginPath(); g.moveTo(left, Y(t)); g.lineTo(left + pw, Y(t)); g.stroke(); g.fillText(yFmt(t), left - 10, Y(t) + 7); }
  if (y2) { g.textAlign = 'left'; for (const t of y2.ticks) g.fillText(y2.fmt(t), left + pw + 8, top + ph - ((t - y2.y0) / (y2.y1 - y2.y0)) * ph + 7); }
  g.textAlign = 'center';
  for (const t of xTicks) { g.beginPath(); g.moveTo(X(t), top); g.lineTo(X(t), top + ph); g.stroke(); g.fillText(xFmt(t), X(t), top + ph + 28); }
  g.fillStyle = 'rgba(255,255,255,.5)'; g.font = '18px sans-serif';
  if (xLabel) g.fillText(xLabel, left + pw / 2, h - 8);
  g.textAlign = 'left';
  if (title) { g.fillStyle = '#e8eef8'; g.font = 'bold 26px sans-serif'; g.fillText(title, 22, 38); }
  if (yLabel) { g.fillStyle = 'rgba(255,255,255,.5)'; g.font = '18px sans-serif'; g.fillText(yLabel, 12, top - 14); }
  const line = (fn, from, to, color, width = 5, dash = null, n = 120) => {
    g.strokeStyle = color; g.lineWidth = width; g.setLineDash(dash || []); g.beginPath();
    let started = false;
    for (let i = 0; i <= n; i++) {
      const x = from + ((to - from) * i) / n, y = fn(x);
      if (y === null || !Number.isFinite(y)) { started = false; continue; }
      const px = X(x), py = Y(clamp(y, y0, y1));
      if (!started) { g.moveTo(px, py); started = true; } else g.lineTo(px, py);
    }
    g.stroke(); g.setLineDash([]);
  };
  const dot = (x, y, color, r = 11) => { g.fillStyle = color; g.beginPath(); g.arc(X(x), Y(clamp(y, y0, y1)), r, 0, TAU); g.fill(); g.strokeStyle = 'rgba(8,10,16,.9)'; g.lineWidth = 3; g.stroke(); };
  const vline = (x, color = 'rgba(255,255,255,.45)') => { g.strokeStyle = color; g.lineWidth = 2; g.setLineDash([8, 8]); g.beginPath(); g.moveTo(X(x), top); g.lineTo(X(x), top + ph); g.stroke(); g.setLineDash([]); };
  const text = (s, x, y, color, font = 'bold 20px sans-serif', align = 'left') => { g.fillStyle = color; g.font = font; g.textAlign = align; g.fillText(s, x, y); g.textAlign = 'left'; };
  return { X, Y, line, dot, vline, text, pw, ph, left, top };
}
// A board in the scene showing a canvas texture.
export function board(tex, w, h) {
  return new THREE.Mesh(new THREE.PlaneGeometry(w, h), new THREE.MeshBasicMaterial({ map: tex, transparent: true, toneMapped: false, side: THREE.DoubleSide }));
}

// ---------------------------------------------------------------- flowing dots
// A polyline sampled by arc length, so things can travel along it at a steady speed.
export function pathOf(points) {
  const P = points.map((p) => (p.isVector3 ? p : new THREE.Vector3(...p)));
  const L = [0];
  for (let i = 1; i < P.length; i++) L.push(L[i - 1] + P[i].distanceTo(P[i - 1]));
  const total = L[L.length - 1];
  return {
    points: P, total,
    at(u, out = new THREE.Vector3()) {
      const d = clamp(u, 0, 1) * total;
      let lo = 0, hi = L.length - 1;
      while (hi - lo > 1) { const m = (lo + hi) >> 1; if (L[m] <= d) lo = m; else hi = m; }
      return out.copy(P[lo]).lerp(P[hi], (d - L[lo]) / Math.max(1e-9, L[hi] - L[lo]));
    },
  };
}
// Dots that stream along a path (u from 0 to 1, wrapping). speed in scene units per second;
// negative runs them backwards.
export function flowDots(path, count, r, color) {
  const m = new THREE.InstancedMesh(new THREE.SphereGeometry(r, 10, 8), M.glow(color), count);
  m.instanceMatrix.setUsage(THREE.DynamicDrawUsage);
  const u = Float32Array.from({ length: count }, (_, i) => i / count), o = new THREE.Object3D(), p = new THREE.Vector3();
  m.step = (dt, speed) => {
    for (let i = 0; i < count; i++) {
      u[i] = (((u[i] + (dt * speed) / path.total) % 1) + 1) % 1;
      path.at(u[i], p); o.position.copy(p); o.scale.setScalar(Math.sin(Math.PI * u[i]) ** 0.3); o.updateMatrix(); m.setMatrixAt(i, o.matrix);
    }
    m.instanceMatrix.needsUpdate = true;
  };
  m.step(0, 0);
  return m;
}

export { lerp, clamp, spring, torus, tube, beam, box, rod };
