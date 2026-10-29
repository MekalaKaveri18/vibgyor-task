// Vibgyor interactive viewer: loads the generated glb, lights it with an HDRI + shadow-casting sun.
import * as THREE from 'three';
import { GLTFLoader } from 'three/addons/loaders/GLTFLoader.js';
import { OrbitControls } from 'three/addons/controls/OrbitControls.js';
import { RGBELoader } from 'three/addons/loaders/RGBELoader.js';

const $ = (id) => document.getElementById(id);
const canvas = $('c');
const params = new URLSearchParams(location.search);

// ---------------------------------------------------------------- renderer / scene
const renderer = new THREE.WebGLRenderer({ canvas, antialias: true, powerPreference: 'high-performance', preserveDrawingBuffer: params.has('shot') });
renderer.setPixelRatio(Math.min(window.devicePixelRatio, 1.75));
renderer.outputColorSpace = THREE.SRGBColorSpace;
renderer.toneMapping = THREE.AgXToneMapping;          // same view transform as the Cycles renders
renderer.shadowMap.enabled = true;
renderer.shadowMap.type = THREE.PCFSoftShadowMap;

const scene = new THREE.Scene();
const camera = new THREE.PerspectiveCamera(38, 1, 0.5, 3000);
const controls = new OrbitControls(camera, canvas);
controls.enableDamping = true;
controls.dampingFactor = 0.07;
controls.maxPolarAngle = THREE.MathUtils.degToRad(87);
controls.minDistance = 6;
controls.maxDistance = 260;

const sun = new THREE.DirectionalLight(0xfff1dc, 3.2);
sun.castShadow = true;
sun.shadow.mapSize.set(2048, 2048);
sun.shadow.bias = -0.0004;
sun.shadow.normalBias = 0.06;
sun.shadow.radius = 3;
scene.add(sun, sun.target);

// ---------------------------------------------------------------- helpers
const meta = await (await fetch('model/meta.json')).json();
// Blender (x east, y north, z up) -> glTF/three (x, z, -y)
const toThree = (x, y, z = 0) => new THREE.Vector3(x, z, -y);
const centre = toThree(meta.centre[0], meta.centre[1], meta.total_height * 0.35);
const frontN = toThree(meta.front_normal[0], meta.front_normal[1]).normalize();   // from the plot towards the 18 m road

function setProgress(p, text) {
  $('fill').style.width = `${Math.round(p * 100)}%`;
  if (text) $('loadtext').textContent = text;
}

/** Direction (in the HDRI's own frame) of its brightest pixel = the sun. */
function sunInTexture(tex) {
  const { data, width: w, height: h } = tex.image;
  let best = -1, bi = 0;
  for (let i = 0; i < w * h; i++) {
    const l = THREE.DataUtils.fromHalfFloat(data[i * 4 + 1]);
    if (l > best) { best = l; bi = i; }
  }
  const c = bi % w, r = Math.floor(bi / w);
  const u = (c + 0.5) / w, v = 1 - (r + 0.5) / h;              // flipY: first data row is the top of the image
  const phi = (u - 0.5) * 2 * Math.PI, el = (v - 0.5) * Math.PI;
  return new THREE.Vector3(Math.cos(el) * Math.cos(phi), Math.sin(el), Math.cos(el) * Math.sin(phi));
}

const skies = {};
async function loadSky(name, file, azOffsetDeg) {
  const tex = await new RGBELoader().loadAsync(file);
  tex.mapping = THREE.EquirectangularReflectionMapping;
  const sunT = sunInTexture(tex);
  // rotate the sky about the vertical so the sun sits at azOffset from the road-facing direction
  const want = Math.atan2(frontN.z, frontN.x) + THREE.MathUtils.degToRad(azOffsetDeg);
  const have = Math.atan2(sunT.z, sunT.x);
  const rot = have - want;
  const pm = new THREE.PMREMGenerator(renderer);
  const env = pm.fromEquirectangular(tex).texture;
  const dir = new THREE.Vector3(Math.cos(want) * Math.cos(Math.asin(sunT.y)), sunT.y, Math.sin(want) * Math.cos(Math.asin(sunT.y))).normalize();
  skies[name] = { tex, env, rot, dir };
}

// ---------------------------------------------------------------- model
const gltf = await new Promise((res, rej) => new GLTFLoader().load('model/vibgyor.glb', res,
  (e) => e.total && setProgress(0.15 + 0.7 * e.loaded / e.total, `Loading model… ${(e.loaded / 1e6).toFixed(1)} MB`), rej));
setProgress(0.88, 'Preparing lighting…');
await loadSky('day', 'assets/sky_day.hdr', -38);
await loadSky('dusk', 'assets/sky_dusk.hdr', 35);

const root = gltf.scene;
scene.add(root);
const maxAniso = renderer.capabilities.getMaxAnisotropy();
const floors = {};          // key -> {node, meshes, label, flats}
const glowMats = [], signMats = [], ledMats = [];

root.traverse((o) => {
  if (o.name === 'Stilt') floors.stilt = { node: o, label: o.userData.label || 'Stilt parking', flats: [] };
  if (o.name === 'Terrace') floors.terrace = { node: o, label: 'Terrace & stair cabin', flats: [] };
  const m = /^Floor_(\d)$/.exec(o.name);
  if (m) {
    let flats = [];
    try { flats = JSON.parse(o.userData.flats || '[]'); } catch (e) { /* ignore */ }
    floors[m[1]] = { node: o, label: o.userData.label, flats };
  }
});

const isGround = (o) => o.name === 'Site';
root.traverse((o) => {
  if (!o.isMesh) return;
  o.castShadow = !isGround(o);
  o.receiveShadow = true;
  const mats = Array.isArray(o.material) ? o.material : [o.material];
  mats.forEach((mt) => {
    for (const k of ['map', 'normalMap', 'roughnessMap', 'metalnessMap']) if (mt[k]) mt[k].anisotropy = maxAniso;
    if (mt.name === 'glass_web') { mt.transparent = true; mt.depthWrite = false; mt.metalness = 0.1; mt.envMapIntensity = 1.4; o.castShadow = false; }
    if (mt.name === 'interior_glow') { mt.emissiveIntensity = 0; mt.side = THREE.DoubleSide; glowMats.push(mt); o.castShadow = false; }
    if (mt.name === 'sign') signMats.push(mt);
    if (mt.name === 'led') { ledMats.push(mt); mt.emissiveIntensity = 0; }
  });
});
// every floor gets its own material copies so highlighting one floor never touches another
for (const f of Object.values(floors)) {
  f.meshes = [];
  f.node.traverse((o) => {
    if (!o.isMesh) return;
    o.material = Array.isArray(o.material) ? o.material.map((m) => m.clone()) : o.material.clone();
    (Array.isArray(o.material) ? o.material : [o.material]).forEach((m) => { m.userData.baseEmissive = m.emissive.clone(); m.userData.baseEI = m.emissiveIntensity; m.userData.baseOpacity = m.opacity; m.userData.baseTransparent = m.transparent; if (m.name === 'interior_glow') glowMats.push(m); });
    f.meshes.push(o);
  });
}

// ---------------------------------------------------------------- time of day
let mode = 'day';
function applySky(name) {
  mode = name;
  const s = skies[name];
  scene.background = s.tex;
  scene.environment = s.env;
  scene.backgroundRotation.set(0, s.rot, 0);
  scene.environmentRotation.set(0, s.rot, 0);
  const dusk = name === 'dusk';
  scene.backgroundIntensity = dusk ? 0.9 : 1.0;
  scene.environmentIntensity = dusk ? 0.8 : 0.95;
  renderer.toneMappingExposure = dusk ? 1.1 : 1.0;
  sun.color.set(dusk ? 0xffa468 : 0xfff1dc);
  sun.intensity = dusk ? 1.4 : 2.6;
  const d = s.dir.clone();
  if (dusk) d.y = Math.max(d.y, 0.16);
  sun.position.copy(centre).addScaledVector(d.normalize(), 120);
  sun.target.position.copy(centre);
  glowMats.forEach((m) => { m.emissiveIntensity = dusk ? 2.6 : 0.0; });
  signMats.forEach((m) => { m.emissiveIntensity = dusk ? 1.6 : 0.0; });
  ledMats.forEach((m) => { m.emissiveIntensity = dusk ? 2.4 : 0.0; });
  $('btn-time').textContent = dusk ? 'Daytime' : 'Evening';
  $('btn-time').classList.toggle('on', dusk);
}
const sc = sun.shadow.camera;
sc.left = -48; sc.right = 48; sc.top = 48; sc.bottom = -48; sc.near = 10; sc.far = 300; sc.updateProjectionMatrix();
$('btn-time').onclick = () => applySky(mode === 'day' ? 'dusk' : 'day');
applySky(params.get('time') === 'dusk' ? 'dusk' : 'day');

// ---------------------------------------------------------------- camera
function homeView() {
  const d = Math.max(60, meta.total_height * 2.9);
  const dir = frontN.clone().applyAxisAngle(new THREE.Vector3(0, 1, 0), THREE.MathUtils.degToRad(-32));
  camera.position.copy(centre).addScaledVector(dir, d).add(new THREE.Vector3(0, d * 0.38, 0));
  controls.target.copy(centre);
  controls.update();
}
homeView();
$('btn-reset').onclick = homeView;

// ---------------------------------------------------------------- floor selection
let selected = null, hovered = null, isolate = false;
const HIGHLIGHT = new THREE.Color(0x3d7bff);
function paint(key, on, strength) {
  const f = floors[key];
  if (!f) return;
  f.meshes.forEach((o) => (Array.isArray(o.material) ? o.material : [o.material]).forEach((m) => {
    if (m.name === 'glass_web') return;
    if (m.name === 'interior_glow') return;
    m.emissive.copy(on ? HIGHLIGHT : m.userData.baseEmissive);
    m.emissiveIntensity = on ? strength : m.userData.baseEI;
  }));
}
function fade(key, faded) {
  const f = floors[key];
  if (!f) return;
  f.meshes.forEach((o) => (Array.isArray(o.material) ? o.material : [o.material]).forEach((m) => {
    m.transparent = faded || m.userData.baseTransparent;
    m.opacity = faded ? 0.18 : m.userData.baseOpacity;
    m.depthWrite = !faded;
  }));
  f.node.traverse((o) => { if (o.isMesh) o.castShadow = !faded && o.material.name !== 'glass_web'; });
}
function refresh() {
  for (const k of Object.keys(floors)) {
    paint(k, k === selected, 0.28);
    if (k !== selected && k === hovered) paint(k, true, 0.1);
    fade(k, isolate && selected !== null && k !== selected);
  }
}
function select(key) {
  selected = key;
  const f = key && floors[key];
  $('floor-title').textContent = f ? (/\d/.test(key) ? `Floor ${key}` : f.label) : 'Click a floor';
  $('floor-sub').textContent = f ? (/\d/.test(key) ? `${f.label} · level +${(meta.floor_h * Number(key)).toFixed(2)} m` : 'Click a numbered floor to see its flats') : 'Orbit with the left mouse button, pan with the right, zoom with the wheel.';
  $('flats').innerHTML = f ? f.flats.map((u) => `<div class="flat ${u.type.toLowerCase()}"><span class="tag">Type ${u.type}</span><div><b>Flat ${u.flat}</b> · 4BHK<small>${u.position === 'front' ? 'Front, on the 18 m road' : 'Rear'} · ${u.carpet_sqm.toFixed(2)} m² carpet</small></div></div>`).join('') : '';
  document.querySelectorAll('#floor-list button').forEach((b) => b.classList.toggle('on', b.dataset.k === key));
  refresh();
}
const list = $('floor-list');
for (const k of ['terrace', '7', '6', '5', '4', '3', '2', '1', 'stilt']) {
  const b = document.createElement('button'); b.textContent = { terrace: 'Roof', stilt: 'Stilt' }[k] || k; b.dataset.k = k;
  b.title = floors[k] ? floors[k].label : `Floor ${k}`;
  b.onclick = () => select(selected === k ? null : k);
  list.appendChild(b);
}
$('btn-isolate').onclick = () => { isolate = !isolate; $('btn-isolate').classList.toggle('on', isolate); refresh(); };

const ray = new THREE.Raycaster();
const ndc = new THREE.Vector2();
function pick(ev) {
  const r = canvas.getBoundingClientRect();
  ndc.set(((ev.clientX - r.left) / r.width) * 2 - 1, -((ev.clientY - r.top) / r.height) * 2 + 1);
  ray.setFromCamera(ndc, camera);
  const objs = [];
  for (const f of Object.values(floors)) objs.push(...f.meshes.filter((o) => o.visible && !(o.material.name === 'interior_glow')));
  const hits = ray.intersectObjects(objs, false).filter((h) => !(h.object.material.name === 'glass_web' && false));
  if (!hits.length) return null;
  const o = hits[0].object;
  return Object.keys(floors).find((k) => floors[k].meshes.includes(o)) || null;
}
let down = null;
canvas.addEventListener('pointerdown', (e) => { down = [e.clientX, e.clientY]; });
canvas.addEventListener('pointerup', (e) => {
  if (!down || Math.hypot(e.clientX - down[0], e.clientY - down[1]) > 4) return;      // a drag is an orbit, not a click
  const k = pick(e);
  select(k ? (k === selected ? null : k) : null);
});
let lastHover = 0;
canvas.addEventListener('pointermove', (e) => {
  const now = performance.now();
  if (now - lastHover < 90 || e.buttons) return;
  lastHover = now;
  const k = pick(e);
  if (k !== hovered) { hovered = k; canvas.style.cursor = k ? 'pointer' : 'grab'; refresh(); }
});

// ---------------------------------------------------------------- loop
function resize() {
  const w = window.innerWidth, h = window.innerHeight;
  renderer.setSize(w, h, false);
  camera.aspect = w / h;
  camera.updateProjectionMatrix();
}
window.addEventListener('resize', resize);
resize();

let frames = 0, t0 = performance.now();
function loop() {
  controls.update();
  renderer.render(scene, camera);
  frames++;
  const now = performance.now();
  if (now - t0 > 1000) { $('fps').textContent = `${Math.round(frames * 1000 / (now - t0))} fps · ${renderer.info.render.triangles.toLocaleString()} tris`; frames = 0; t0 = now; }
  if (params.has('shot') && params.get('shot') === 'slow') setTimeout(loop, 2500);
  else requestAnimationFrame(loop);
}
setProgress(1, 'Ready');
$('loading').classList.add('done');
if (params.has('floor')) select(params.get('floor'));
if (params.has('isolate')) { isolate = true; $('btn-isolate').classList.add('on'); refresh(); }
window.__viewer = { scene, camera, controls, renderer, select, applySky, floors };
loop();
