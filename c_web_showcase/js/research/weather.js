// Scene dressing for the research chapters. The road geometry and movement follow
// CarClear's drive.js. Snow, sun and heat shimmer communicate ambient conditions;
// they are visual cues, never weather observations or an additional thermal model.
import { THREE, M, box, swarm, clamp, canvasTexture } from '../kit.js';
import { CARU } from '../car.js';

export function makeResearchRoad(stage, parent = stage.root) {
  const root = new THREE.Group(); root.name = 'research-road'; parent.add(root);
  const material = M.matte(0x30343d, { roughness: 0.95 });
  const road = box(60, 0.2, 7, material); road.position.y = -0.1;
  road.castShadow = false; root.add(road);
  const dashes = swarm(24, new THREE.BoxGeometry(1.6, 0.02, 0.18), M.glow(0xf5d547));
  dashes.castShadow = false; root.add(dashes);
  const edgeL = box(60, 0.02, 0.1, M.glow(0xe8eef8)); edgeL.position.set(0, 0.01, -3.2);
  const edgeR = edgeL.clone(); edgeR.position.z = 3.2; root.add(edgeL, edgeR);
  const shoulders = new THREE.Group(); root.add(shoulders);
  const shoulderMat = M.matte(0xb6d7e6, { transparent: true, opacity: 0, depthWrite: false });
  for (const z of [-4.1, 4.1]) {
    const bank = box(60, 0.07, 1.6, shoulderMat); bank.position.set(0, -0.005, z);
    bank.castShadow = false; shoulders.add(bank);
  }
  const air = swarm(60, new THREE.BoxGeometry(1, 0.025, 0.025), M.ghost(0xbfe4ff, 0.32));
  air.castShadow = false; root.add(air);
  const airSeed = Array.from({ length: 60 }, (_, i) => [((i * 0.6180339) % 1) * 24 - 12, 0.4 + ((i * 0.37) % 1) * 4.2, ((i * 0.7548776) % 1) * 5 - 2.5]);
  let travel = 0;
  return {
    root, road, material, shoulders,
    update(dt, speedMps, cold = 0, hot = 0) {
      travel += Math.max(0, speedMps) * Math.max(0, dt) / CARU.u / 4;
      for (let i = 0; i < 24; i++) dashes.place(i, [((i * 3 - travel) % 72 + 72) % 72 - 36, 0.012, 0]);
      dashes.done();
      for (let i = 0; i < 60; i++) {
        const [x0, y, z] = airSeed[i];
        air.place(i, [((x0 - travel * 1.3) % 24 + 24) % 24 - 12, y, z < 0 ? z - 2.2 : z + 2.2]);
      }
      air.visible = speedMps > 2; air.done();
      shoulderMat.opacity = cold * 0.9; shoulders.visible = cold > 0.01;
      material.color.set(0x30343d).lerp(new THREE.Color(0x8ca9ba), cold * 0.58).lerp(new THREE.Color(0x59372c), hot * 0.65);
    },
  };
}

export function makeWeather(stage) {
  const root = new THREE.Group(); root.name = 'research-weather'; stage.root.add(root);
  const wrapper = stage.host.closest('.stage-wrap') || stage.host;
  const oldBackground = wrapper.style.background;
  const oldAccent = document.body.style.getPropertyValue('--c');
  const oldSceneBackground = stage.scene.background;
  const lights = stage.lights.children.map((light) => ({ light, color: light.color.clone(), intensity: light.intensity }));
  const flake = canvasTexture(48, 48, (g, w, h) => {
    const halo = g.createRadialGradient(w / 2, h / 2, 0, w / 2, h / 2, w / 2);
    halo.addColorStop(0, 'rgba(255,255,255,1)');
    halo.addColorStop(0.3, 'rgba(228,246,255,.95)');
    halo.addColorStop(1, 'rgba(228,246,255,0)');
    g.fillStyle = halo; g.fillRect(0, 0, w, h);
  });
  const count = 420;
  const positions = new Float32Array(count * 3);
  const seed = Array.from({ length: count }, (_, i) => ({
    x: ((i * 0.61803398875) % 1) * 32 - 16,
    y: ((i * 0.41421356237) % 1) * 13,
    z: ((i * 0.73205080757) % 1) * 21 - 10.5,
    fall: 0.65 + ((i * 0.271828) % 1) * 1.15,
  }));
  const snowGeometry = new THREE.BufferGeometry();
  snowGeometry.setAttribute('position', new THREE.BufferAttribute(positions, 3));
  const snowMaterial = new THREE.PointsMaterial({ color: 0xeef9ff, map: flake.tex, size: 0.14, transparent: true, opacity: 0, depthWrite: false, blending: THREE.AdditiveBlending, sizeAttenuation: true });
  const snow = new THREE.Points(snowGeometry, snowMaterial); snow.name = 'snow'; snow.frustumCulled = false; root.add(snow);
  const sun = new THREE.Group(); sun.name = 'sun'; sun.position.set(-8, 7.8, -8); root.add(sun);
  const disk = new THREE.Mesh(new THREE.SphereGeometry(0.65, 24, 12), M.glow(0xffca72)); sun.add(disk);
  const haloMat = M.ghost(0xff7c3d, 0.13);
  for (let i = 1; i <= 3; i++) sun.add(new THREE.Mesh(new THREE.SphereGeometry(0.7 + i * 0.25, 24, 12), haloMat.clone()));
  const heat = new THREE.Group(); heat.name = 'heat-waves'; root.add(heat);
  const heatWaves = [];
  for (let i = 0; i < 11; i++) {
    const vertices = new Float32Array(26 * 3);
    const geometry = new THREE.BufferGeometry(); geometry.setAttribute('position', new THREE.BufferAttribute(vertices, 3));
    const material = new THREE.LineBasicMaterial({ color: 0xffb078, transparent: true, opacity: 0, depthWrite: false });
    const line = new THREE.Line(geometry, material); heat.add(line);
    heatWaves.push({ line, vertices, x: -8 + i * 1.55, z: i % 2 ? -3.8 : 3.9, phase: i * 1.33 });
  }
  let time = 0, drawn = '';
  return {
    root,
    update(dt, ambientC, speedMps = 0) {
      time += Math.max(0, dt);
      const cold = clamp((8 - ambientC) / 18, 0, 1);
      const hot = clamp((ambientC - 29) / 11, 0, 1);
      snow.visible = cold > 0.01;
      snowMaterial.opacity = cold * 0.9;
      snowMaterial.size = 0.105 + cold * 0.065;
      if (snow.visible) {
        seed.forEach((s, i) => {
          positions[i * 3] = ((s.x - time * (0.5 + speedMps * 0.035)) % 32 + 48) % 32 - 16;
          positions[i * 3 + 1] = ((s.y - time * s.fall) % 13 + 13) % 13;
          positions[i * 3 + 2] = s.z + Math.sin(time * 0.55 + i) * 0.26;
        });
        snowGeometry.attributes.position.needsUpdate = true;
      }
      sun.visible = heat.visible = hot > 0.01;
      sun.scale.setScalar(0.7 + hot * 0.3);
      if (heat.visible) heatWaves.forEach(({ line, vertices, x, z, phase }) => {
        for (let k = 0; k < 26; k++) {
          const y = k / 25 * 3.5;
          vertices[k * 3] = x + Math.sin(y * 2.8 - time * 2.2 + phase) * (0.08 + y * 0.065);
          vertices[k * 3 + 1] = y + 0.1;
          vertices[k * 3 + 2] = z;
        }
        line.geometry.attributes.position.needsUpdate = true;
        line.material.opacity = hot * (0.14 + 0.1 * Math.sin(time * 0.6 + phase) ** 2);
      });
      const key = `${cold.toFixed(3)}|${hot.toFixed(3)}`;
      if (drawn !== key) {
        drawn = key;
        wrapper.style.background = cold > 0
          ? `radial-gradient(ellipse at 50% 8%, rgba(66,112,151,${0.5 * cold + 0.08}), transparent 75%), #0c101a`
          : hot > 0 ? `radial-gradient(ellipse at 35% 8%, rgba(189,62,23,${0.62 * hot}), transparent 76%), #160e0d` : oldBackground;
        lights.forEach(({ light, color, intensity }, i) => {
          light.color.copy(color).lerp(new THREE.Color(0x91caff), cold * 0.35).lerp(new THREE.Color(0xff9959), hot * (i === 0 ? 0.5 : 0.3));
          light.intensity = intensity * (1 + hot * 0.08);
        });
        wrapper.dataset.weather = cold > 0 ? 'snow' : hot > 0 ? 'heat' : 'mild';
        const accent = new THREE.Color(oldAccent || '#ff7a59');
        accent.lerp(new THREE.Color(0x8dcfff), cold).lerp(new THREE.Color(0xff6548), hot);
        document.body.style.setProperty('--c', `#${accent.getHexString()}`);
      }
      return { cold, hot };
    },
    dispose() {
      wrapper.style.background = oldBackground;
      if (oldAccent) document.body.style.setProperty('--c', oldAccent);
      else document.body.style.removeProperty('--c');
      delete wrapper.dataset.weather;
      lights.forEach(({ light, color, intensity }) => { light.color.copy(color); light.intensity = intensity; });
      stage.scene.background = oldSceneBackground;
      // Stage.clear owns scene geometry/material disposal.
    },
  };
}
