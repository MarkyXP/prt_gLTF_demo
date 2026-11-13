import * as THREE from 'three';
import { TrackballControls } from 'three/addons/controls/TrackballControls.js';
import { GLTFLoader } from 'three/addons/loaders/GLTFLoader.js';
import { applyProxyVisibility, initProxyStyling, styleProxies,
         updateProxyEdges } from './proxy_styling.js';
import { applyDisplayStyleToModel, initDisplayStyle } from './display_style.js';

const drop = document.getElementById('drop');
const fileInput = document.getElementById('file');
const stats = document.getElementById('stats');
const progress = document.getElementById('progress');
const reindex = document.getElementById('reindex');
const dl = document.getElementById('download');
const renderNote = document.getElementById('render');
const refs = document.getElementById('refs');

drop.addEventListener('click', () => fileInput.click());
fileInput.addEventListener('change', () => fileInput.files.length &&
  convert(Array.from(fileInput.files)));
drop.addEventListener('dragover', (e) => {
  e.preventDefault();
  drop.classList.add('over');
});
drop.addEventListener('dragleave', () => drop.classList.remove('over'));
drop.addEventListener('drop', (e) => {
  e.preventDefault();
  drop.classList.remove('over');
  const files = Array.from(e.dataTransfer.files);
  if (files.length) convert(files);
});

const scene = new THREE.Scene();
scene.background = new THREE.Color(0x151518);
const camera = new THREE.PerspectiveCamera(55, 1, 0.01, 5000);
const renderer = new THREE.WebGLRenderer({ antialias: true });
const view = document.getElementById('view');
let controls;

function resize() {
  const w = view.clientWidth, h = view.clientHeight;
  renderer.setSize(w, h, false);
  camera.aspect = w / h;
  camera.updateProjectionMatrix();
  if (controls) controls.handleResize();
}
window.addEventListener('resize', resize);
view.appendChild(renderer.domElement);
new ResizeObserver(resize).observe(view);
resize();

scene.add(new THREE.HemisphereLight(0xbfd0ff, 0x404040, 2.2));
const key = new THREE.DirectionalLight(0xffffff, 2.0);
scene.add(key);
scene.add(key.target);
scene.add(new THREE.GridHelper(50, 50, 0x444, 0x2a2a2a));

controls = new TrackballControls(camera, renderer.domElement);
controls.dynamicDampingFactor = 0.1;
camera.position.set(4, 3, 6);

const lightDirection = new THREE.Vector3();
const lightRight = new THREE.Vector3();
const lightUp = new THREE.Vector3();

function updateViewerLight() {
  camera.updateMatrixWorld();
  const distance = camera.position.distanceTo(controls.target);
  lightDirection.copy(camera.position).sub(controls.target).normalize();
  lightRight.setFromMatrixColumn(camera.matrixWorld, 0).normalize();
  lightUp.setFromMatrixColumn(camera.matrixWorld, 1).normalize();
  key.position.copy(controls.target)
    .addScaledVector(lightDirection, distance / Math.sqrt(2))
    .addScaledVector(lightRight, -distance / 2)
    .addScaledVector(lightUp, distance / 2);
  key.target.position.copy(controls.target);
}

const loader = new GLTFLoader();
initProxyStyling({ scene: scene, camera: camera });
initDisplayStyle();
let current, url;
let activeConversion;

function fitAndCenter(root) {
  const box = new THREE.Box3().setFromObject(root);
  if (box.isEmpty()) return;
  const size = box.getSize(new THREE.Vector3()).length() || 1;
  const center = box.getCenter(new THREE.Vector3());
  root.position.sub(center);
  const dist = size * 1.6;
  camera.position.set(dist * 0.7, size * 0.5, dist * 0.7);
  camera.near = Math.max(size / 1000, 0.001);
  camera.far = dist * 100;
  camera.updateProjectionMatrix();
  controls.target.set(0, 0, 0);
  controls.update();
}

function show(gltf, name) {
  if (current) scene.remove(current);
  current = gltf.scene || gltf.scenes[0];
  // GLTF BLEND materials are normally drawn after opaque geometry, but the
  // default depthWrite can still let a bounds proxy mask its recovered
  // components. Keep the proxy depth-tested while preventing depth-buffer
  // writes so it reads as context rather than as a solid replacement.
  current.traverse((object) => {
    if (!object.isMesh) return;
    const materials = Array.isArray(object.material)
      ? object.material : [object.material];
    for (const material of materials) {
      if (material && material.transparent) {
        material.depthWrite = false;
        material.needsUpdate = true;
      }
    }
  });
  styleProxies(current);
  applyDisplayStyleToModel(current);
  fitAndCenter(current);
  scene.add(current);
  applyProxyVisibility();
  renderNote.textContent = '';
  url = URL.createObjectURL(gltf.source);   // the raw .glb blob
  dl.href = url;
  dl.download = name;
  dl.style.display = 'block';
}

async function loadArtifact(artifact) {
  const response = await fetch(artifact.url);
  if (!response.ok) throw new Error('Progressive artifact unavailable');
  const bytes = await response.arrayBuffer();
  await new Promise((resolve, reject) => {
    loader.parse(bytes, '', (gltf) => {
      if (!current) {
        current = new THREE.Group();
        scene.add(current);
      }
      current.add(gltf.scene || gltf.scenes[0]);
      styleProxies(current);
      applyProxyVisibility();
      applyDisplayStyleToModel(current);
      if (!activeConversion.hasRenderedArtifact) {
        activeConversion.hasRenderedArtifact = true;
        fitAndCenter(current);
      }
      resolve();
    }, reject);
  });
}

async function loadNewArtifacts(state) {
  const known = activeConversion.loadedArtifacts;
  const pending = (state.artifacts || []).filter(
    (artifact) => !known.has(artifact.id));
  await Promise.all(pending.map(async (artifact) => {
    await loadArtifact(artifact);
    known.add(artifact.id);
  }));
}

renderer.setAnimationLoop(() => {
  controls.update();
  updateViewerLight();
  updateProxyEdges();
  renderer.render(scene, camera);
});

reindex.addEventListener('click', () => {
  reindex.disabled = true;
  stats.textContent = 'Re-indexing assembly components...';
  fetch('/reindex', { method: 'POST' })
    .then(async (res) => {
      if (!res.ok) throw new Error('Re-index failed: ' + res.statusText);
      return res.json();
    })
    .then((result) => {
      stats.textContent = 'Indexed ' + result.file_count
        + ' .prt file(s) (' + result.unique_names + ' unique names).';
    })
    .catch((error) => { stats.textContent = String(error.message || error); })
    .finally(() => { reindex.disabled = false; });
});

function convert(files) {
  const file = files[0];
  const started = performance.now();
  let phaseStarted = started;
  activeConversion = { started: started, phaseStarted: phaseStarted };
  activeConversion.loadedArtifacts = new Set();
  activeConversion.hasRenderedArtifact = false;
  const setPhase = (label) => {
    phaseStarted = performance.now();
    activeConversion.phaseStarted = phaseStarted;
    stats.textContent = label + ' — step 0.0s, total 0.0s';
  };
  const updateTimer = () => {
    stats.textContent = stats.textContent.replace(
      / — step .*$/, ' — step ' + formatDuration(performance.now() - phaseStarted)
        + ', total ' + formatDuration(performance.now() - started));
  };
  const timer = setInterval(updateTimer, 250);
  const finish = (message) => {
    clearInterval(timer);
    stats.textContent = message;
  };
  setPhase('Finding referenced components');
  progress.hidden = false;
  progress.value = 0;
  progress.max = 1;
  progress.dataset.totalKnown = 'false';
  refs.textContent = 'Finding referenced components...';
  dl.style.display = 'none';
  if (url) URL.revokeObjectURL(url);
  if (current) {
    scene.remove(current);
    current = null;
  }
  const lookup = new FormData();
  for (const part of files) lookup.append('files', part);
  const lookupPromise = fetch('/references', { method: 'POST', body: lookup })
    .then(async (res) => res.ok ? res.json() : null)
    .then(tree => { if (tree) renderReferences(tree); })
    .catch(() => { refs.textContent = ''; });
  const form = new FormData();
  for (const part of files) form.append('files', part);
  setPhase('Converting on server');
  fetch('/convert', { method: 'POST', body: form })
    .then(async (res) => {
      if (!res.ok) {
        let detail = res.statusText;
        try {
          const body = await res.json();
          detail = typeof body.detail === 'string'
            ? body.detail : JSON.stringify(body.detail || body);
        } catch (e) {}
        throw new Error('Conversion failed: ' + detail);
      }
      const job = await res.json();
      setPhase('Converting components');
      return waitForConversion(job.job_id);
    })
    .then(async (job) => {
      if (job.status === 'failed') {
        throw new Error('Conversion failed: ' + job.error);
      }
      setPhase('Loading converted model');
      const result = await fetch('/convert/' + job.job_id + '/result');
      if (!result.ok) throw new Error('Conversion result unavailable');
      const info = job.stats || {};
      const blob = new Blob([await result.arrayBuffer()], { type: 'model/gltf-binary' });
      const name = file.name.replace(/\.prt$/i, '') + '.glb';
      loader.load(
        URL.createObjectURL(blob),
        (gltf) => {
          gltf.source = blob;
          show(gltf, name);
          const timings = info.timings_ms || {};
          const serverSteps = ['prepare_ms', 'decode_ms', 'serialize_ms']
            .filter((key) => timings[key] !== undefined)
            .map((key) => key.replace('_ms', '') + ' '
              + formatDuration(timings[key])).join(', ');
          finish('"' + file.name + '" converted: '
            + info.meshes + ' mesh(es), ' + info.points + ' points, '
            + info.triangles + ' triangles. Total '
            + formatDuration(performance.now() - started)
            + ' (server ' + formatDuration(timings.total_ms || 0)
            + '; ' + serverSteps + ').'
            + (info.assemblies || []).map((assembly) => {
                          const found = assembly.resolved_count;
                          const total = assembly.reference_count;
                          if (assembly.proxy_display === 'placeholder')
                            return ' Assembly: ' + found + '/' + total
                              + ' components found; showing placeholder bounds.';
                          if (assembly.proxy_display === 'translucent-bounds')
                            return ' Assembly: ' + found + '/' + total
                              + ' direct components found; translucent bounds mark missing geometry.'
                              + (assembly.nested_missing_count
                                ? ' Nested assemblies are incomplete.' : '');
                          if (assembly.proxy_display === 'suppressed')
                            return ' Assembly: all ' + total
                              + ' components found; proxy bounds hidden.';
                          if (assembly.proxy_display === 'no-proxy'
                              && (assembly.missing_count
                                  || assembly.nested_missing_count))
                            return ' Assembly geometry included; '
                              + (assembly.missing_count + assembly.nested_missing_count)
                              + ' referenced components remain unresolved.';
                          return '';
                        }).join(''));
        },
        undefined,
        (err) => { finish('Conversion finished but three.js could not render: ' + err); }
      );
    })
    .catch((err) => { finish(String(err.message || err)); });
  void lookupPromise;
}

function formatDuration(milliseconds) {
  const seconds = milliseconds / 1000;
  return seconds < 10 ? seconds.toFixed(1) + 's' : seconds.toFixed(0) + 's';
}

function renderReferences(tree) {
  refs.replaceChildren();
  let count = 0;
  const add = (node, parent, depth) => {
    const row = document.createElement('div');
    count += 1;
    row.classList.add('component');
    row.dataset.partName = node.name.toLowerCase();
    row.textContent = (depth ? '↳ ' : '') + node.name +
      (node.found || !depth ? '' : ' (not found — upload with assembly)');
    if (!node.found && depth) row.classList.add('missing');
    if (depth) row.classList.add('child');
    parent.appendChild(row);
    for (const child of node.children) add(child, parent, depth + 1);
  };
  add(tree, refs, 0);
  progress.max = Math.max(count, 1);
  progress.dataset.totalKnown = 'true';
  return count;
}

function updateConversionProgress(state) {
  const completed = Number(state.completed) || 0;
  const total = Number(progress.max) || 1;
  const totalKnown = progress.dataset.totalKnown === 'true';
  if (totalKnown) {
    progress.value = Math.min(completed, total);
  } else if (completed > 1) {
    progress.removeAttribute('value');
  } else {
    progress.value = completed;
  }
  const current = state.current
    ? ' "' + state.current + '"' : '';
  const completion = totalKnown
    ? completed + '/' + total + ' complete'
    : completed + ' processed';
  stats.textContent = 'Converting component' + current + ' — '
    + completion
    + ' — step ' + formatDuration(
      performance.now() - activeConversion.phaseStarted)
    + ', total ' + formatDuration(
      performance.now() - activeConversion.started);
  const completedParts = new Set((state.completed_parts || [])
    .map((part) => part.toLowerCase()));
  const missing = new Set((state.missing_parts || [])
    .map((part) => part.toLowerCase()));
  refs.querySelectorAll('.component').forEach((row) => {
    row.classList.toggle('done', completedParts.has(row.dataset.partName));
    row.classList.toggle('missing', missing.has(row.dataset.partName));
    row.classList.toggle('active', row.dataset.partName
      === (state.current || '').toLowerCase());
  });
}

async function waitForConversion(jobId) {
  while (true) {
    const response = await fetch('/convert/' + jobId + '/status');
    if (!response.ok) throw new Error('Conversion status unavailable');
    const state = await response.json();
    state.job_id = jobId;
    updateConversionProgress(state);
    await loadNewArtifacts(state);
    if (state.status === 'complete') progress.value = progress.max;
    if (state.status === 'complete' || state.status === 'failed') return state;
    await new Promise((resolve) => setTimeout(resolve, 400));
  }
}
