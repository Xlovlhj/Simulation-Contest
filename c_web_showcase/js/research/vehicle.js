// Shared research EV: extracted directly from CarClear's electric.js build().
// The original body, wheel geometry, battery grid, inverter, motor, cabling and
// particle paths are retained. Their geometry is an educational illustration,
// not the dimensional specification of the competition vehicle.
import { THREE, M, box, tube, swarm, exploder, clamp } from '../kit.js';
import { makeBody, makeWheels, CARU, pathOf, flowDots, rbox, zrod } from '../car.js';

// The same pack can be inspected on its own in the battery chapter.
export function makeResearchBattery() {
  const pack = new THREE.Group();
  const casing = rbox(5.4, 0.42, 3.3, 0.12, M.clear(0xcfe8ff, 0.2));
  pack.add(casing);
  const modules = swarm(24, new THREE.BoxGeometry(0.78, 0.3, 0.72),
    M.plastic(0x2f6f5a, {
      roughness: 0.5, emissive: new THREE.Color(0x0e3a2c), emissiveIntensity: 1,
    }));
  for (let i = 0; i < 24; i++) {
    modules.place(i, [-2.25 + (i % 6) * 0.9, 0, -1.2 + Math.floor(i / 6) * 0.8]);
  }
  modules.done();
  pack.add(modules);
  return { pack, modules, casing };
}

export function makeResearchVehicle(stage, options = {}) {
  const root = new THREE.Group();
  (options.parent || stage.root).add(root);
  const body = makeBody({ color: 0xdfe6ee });
  root.add(body.group);
  body.setOpacity(options.opacity ?? 0.1);
  const wheels = makeWheels();
  wheels.forEach((w) => root.add(w.group));

  // Original CarClear layout, dimensions and materials.
  const { pack, modules } = makeResearchBattery();
  pack.position.set(-0.2, 0.72, 0);
  root.add(pack);
  const charge = box(0.06, 0.3, 0.3, M.plastic(0x23262f));
  charge.position.set(-4.1, 1.9, -2.2);
  root.add(charge);

  // Grouped fins preserve the original pose while allowing a single exploded part.
  const inverter = new THREE.Group();
  inverter.position.set(-2.55, 1.55, 0);
  root.add(inverter);
  inverter.add(rbox(0.9, 0.36, 1.1, 0.08, M.metal(0x9aa3b2, { roughness: 0.4 })));
  for (let k = 0; k < 6; k++) {
    const fin = box(0.8, 0.12, 0.03, M.metal(0x7c8594));
    fin.position.set(0, 0.23, -0.42 + k * 0.17);
    inverter.add(fin);
  }
  const motor = new THREE.Group();
  motor.position.set(-2.6, CARU.R + 0.1, -0.35);
  root.add(motor);
  const housing = zrod(-0.55, 0.55, 0.42, 0.42,
    M.metal(0x6c7484, { transparent: true, opacity: 0.45 }));
  motor.add(housing);
  const rotor = new THREE.Group();
  motor.add(rotor);
  rotor.add(zrod(-0.45, 0.45, 0.26, 0.26, M.metal(0xd07a45, { roughness: 0.3 })));
  const stripe = box(0.06, 0.06, 0.92, M.glow(0x8ef0ff));
  stripe.position.y = 0.27;
  rotor.add(stripe);
  const gearCase = rbox(0.9, 0.95, 0.35, 0.12,
    M.metal(0x8a93a3, { transparent: true, opacity: 0.8 }));
  gearCase.position.set(-2.95, CARU.R, 0.45);
  root.add(gearCase);
  const axle = zrod(-CARU.track + 0.3, CARU.track - 0.3, 0.06, 0.06,
    M.metal(0xc9ced8));
  axle.position.set(-CARU.axle, CARU.R, 0);
  root.add(axle);
  const cables = new THREE.Group();
  root.add(cables);
  const hv = M.plastic(0xff7a3d, { roughness: 0.5 });
  cables.add(tube([[-2.4, 0.95, 0.3], [-2.5, 1.2, 0.3], [-2.55, 1.4, 0.2]],
    0.045, hv, false, 20));
  cables.add(tube([[-2.4, 0.95, -0.3], [-2.5, 1.2, -0.3], [-2.55, 1.4, -0.2]],
    0.045, hv, false, 20));
  cables.add(tube([[-2.9, 1.5, -0.4], [-2.9, 1.35, -0.4], [-2.75, 1.05, -0.35]],
    0.04, hv, false, 20));
  const path = pathOf([
    [1.6, 0.98, 0], [-2.2, 0.98, 0], [-2.55, 1.35, 0],
    [-2.6, 1.2, -0.35], [-2.9, CARU.R + 0.1, 0.45],
    [-CARU.axle, CARU.R, 0.45], [-CARU.axle, CARU.R, CARU.track - 0.3],
  ]);
  const path2 = pathOf([
    [-CARU.axle, CARU.R, 0.45], [-CARU.axle, CARU.R, -CARU.track + 0.3],
  ]);
  const flowA = flowDots(path, 44, 0.075, 0xffb547);
  const flowB = flowDots(path2, 14, 0.075, 0xffb547);
  root.add(flowA, flowB);

  const labels = [];
  if (options.labels !== false) {
    labels.push(stage.label('动力电池', [2.2, 0.2, 1.9], pack, 'hot'));
    labels.push(stage.label('逆变器', [0, 0.55, 0], inverter));
    labels.push(stage.label('驱动电机', [0.2, -0.75, 1.4], motor));
    labels.push(stage.label('单级减速器', [0.5, -0.8, 1.2], gearCase));
    labels.push(stage.label('充电接口', [0, 0.45, -0.3], charge));
  }
  const parts = { battery: pack, inverter, motor, reducer: gearCase, charging: charge,
    cabin: body.cabin, wheels: wheels[1].group };
  Object.entries(parts).forEach(([id, obj]) => {
    obj.traverse((child) => { child.userData.partId = id; });
    // The transparent cabin shell must not intercept clicks on the pack below it.
    if (options.pickable && id !== 'cabin') stage.pickables.push(obj);
  });
  const explode = exploder([
    { obj: body.shell, off: [0, 3.3, 0] },
    { obj: body.cabin, off: [0, 4.1, 0] },
    { obj: pack, off: [0, 1.25, 0] },
    { obj: inverter, off: [-0.4, 2.6, -0.4] },
    { obj: motor, off: [-0.6, 1.1, -1.5] },
    { obj: gearCase, off: [-0.8, 0.8, 1.0] },
    { obj: charge, off: [-0.7, 1.5, -0.8] },
    ...wheels.map((w) => ({
      obj: w.group, off: [0, 0, Math.sign(w.group.position.z) * 1.5],
    })),
  ]);
  let wheelAngle = 0, rotorAngle = 0, flowPower = 0, exploded = 0, showLabels = true;

  const api = {
    root, body, wheels, pack, modules, mods: modules, motor, motorG: motor,
    rotor, inverter, gearCase, axle, charge, cables, flowA, flowB, parts, labels,
    updatePose(dt, speedMps = 0, motorRpm = null) {
      dt = Math.max(0, Number.isFinite(dt) ? dt : 0);
      const speed = Number.isFinite(speedMps) ? speedMps : 0;
      // Slow visual motion. This does not publish a measured wheel/rotor speed.
      wheelAngle += speed * dt * 0.4;
      rotorAngle += (Number.isFinite(motorRpm) ? motorRpm * Math.PI / 30 / 32 : speed) * dt;
      wheels.forEach((w) => { w.spin.rotation.z = -wheelAngle; });
      rotor.rotation.z = -rotorAngle;
      axle.rotation.z = -wheelAngle;
      const kw = flowPower / 1000;
      const show = Math.abs(kw) > 0.02 && exploded < 0.08;
      flowA.visible = flowB.visible = show;
      if (show) {
        const regen = kw < 0;
        flowA.material.color.set(regen ? 0x5ce1a9 : 0xffb547);
        flowB.material.color.copy(flowA.material.color);
        const rate = Math.sign(kw) * (0.4 + Math.min(4, Math.abs(kw) / 20));
        flowA.step(dt, rate);
        flowB.step(dt, rate);
        modules.material.emissive.set(regen ? 0x1f7a52 : 0x0e3a2c);
      }
      labels.forEach((label, i) => {
        label.visible = showLabels && (i < 3 || stage.host.clientWidth >= 560);
      });
    },
    setFlow(powerW = 0) {
      flowPower = Number.isFinite(powerW) ? powerW : 0;
    },
    setExplode(value = 0) {
      exploded = clamp(value, 0, 1);
      explode(exploded);
      cables.visible = exploded < 0.08;
    },
    setXray(on = true) { body.setOpacity(on ? 0.1 : 1); },
    setLabels(on = true) { showLabels = !!on; },
    dispose() {
      // Stage.clear owns geometry/material cleanup when a chapter is replaced.
      const owned = new Set(Object.values(parts));
      stage.pickables = stage.pickables.filter((o) => !owned.has(o));
    },
  };
  api.updatePose(0, 0);
  return api;
}
