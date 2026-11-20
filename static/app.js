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
const toggleAll = document.getElementById('toggle-all');
const side = document.getElementById('side');
const sideResizer = document.getElementById('side-resizer');
const partInfo = document.getElementById('part-info');
const partPreview = document.getElementById('part-preview');
const partPreviewDims = document.getElementById('part-preview-dims');
const partAttributes = document.getElementById('part-attributes');
let referenceRootName = '';
const referenceChildren = new Map();
const knownMissingComponents = new Set();

function componentKey(name) {
  return String(name || '').split(/[\\/]/).pop()
    .replace(/\.prt$/i, '').toLowerCase();
}

function componentNodeKey(name) {
  return componentKey(String(name || '').replace(/#\d+$/, ''));
}

function instanceOccurrenceKey(parent, component, index) {
  return componentKey(parent) + '/' + componentNodeKey(component)
    + '#' + index;
}

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
let titleTimer;
let resizingSide = false;

function setSideWidth(width) {
  const minimum = 260;
  const maximum = Math.max(minimum, window.innerWidth - 320);
  side.style.width = Math.max(minimum, Math.min(maximum, width)) + 'px';
  resize();
}

sideResizer.addEventListener('pointerdown', (event) => {
  event.preventDefault();
  resizingSide = true;
  sideResizer.setPointerCapture(event.pointerId);
  document.body.classList.add('resizing-side');
});

sideResizer.addEventListener('pointermove', (event) => {
  if (!resizingSide) return;
  setSideWidth(event.clientX);
});

const stopResizingSide = (event) => {
  if (!resizingSide) return;
  resizingSide = false;
  if (event && sideResizer.hasPointerCapture(event.pointerId)) {
    sideResizer.releasePointerCapture(event.pointerId);
  }
  document.body.classList.remove('resizing-side');
};

sideResizer.addEventListener('pointerup', stopResizingSide);
sideResizer.addEventListener('pointercancel', stopResizingSide);
sideResizer.addEventListener('keydown', (event) => {
  const step = event.shiftKey ? 50 : 10;
  if (event.key === 'ArrowLeft') {
    event.preventDefault();
    setSideWidth(side.offsetWidth - step);
  } else if (event.key === 'ArrowRight') {
    event.preventDefault();
    setSideWidth(side.offsetWidth + step);
  }
});

function startTitleAnimation(fileName, conversion) {
  clearInterval(titleTimer);
  let dots = 1;
  const update = () => {
    if (activeConversion !== conversion) return;
    document.title = 'glb | ' + fileName + '.'.repeat(dots);
    dots = dots % 3 + 1;
  };
  update();
  titleTimer = setInterval(update, 500);
}

function finishTitle(fileName, conversion) {
  if (activeConversion !== conversion) return;
  clearInterval(titleTimer);
  document.title = 'glb | ' + fileName;
}

refs.addEventListener('change', (event) => {
  const checkbox = event.target;
  if (!(checkbox instanceof HTMLInputElement)
      || !checkbox.classList.contains('component-visibility')
      || !activeConversion) return;
  const row = checkbox.closest('.component');
  if (!row) return;
  const occurrenceId = row.dataset.occurrenceId;
  if (!occurrenceId) return;
  if (checkbox.checked) activeConversion.hiddenOccurrences.delete(occurrenceId);
  else activeConversion.hiddenOccurrences.add(occurrenceId);
  applyComponentVisibility(current);
  updateToggleAllButton();
});

function updateToggleAllButton() {
  const checkboxes = Array.from(
    refs.querySelectorAll('.component-visibility'));
  const available = checkboxes.filter((checkbox) => !checkbox.disabled);
  const allShown = available.length > 0
    && available.every((checkbox) => checkbox.checked);
  toggleAll.disabled = available.length === 0;
  toggleAll.textContent = allShown ? 'Hide all' : 'Show all';
}

toggleAll.addEventListener('click', () => {
  if (!activeConversion) return;
  const checkboxes = Array.from(
    refs.querySelectorAll('.component-visibility'))
    .filter((checkbox) => !checkbox.disabled);
  const showAll = checkboxes.some((checkbox) => !checkbox.checked);
  refs.querySelectorAll('.component').forEach((row) => {
    const checkbox = row.querySelector('.component-visibility');
    const occurrenceId = row.dataset.occurrenceId;
    if (!checkbox || checkbox.disabled || !occurrenceId) return;
    checkbox.checked = showAll;
    if (showAll) activeConversion.hiddenOccurrences.delete(occurrenceId);
    else activeConversion.hiddenOccurrences.add(occurrenceId);
  });
  applyComponentVisibility(current);
  updateToggleAllButton();
});

function componentIsVisible(key) {
  const occurrences = activeConversion.occurrencesByComponent.get(key);
  return !occurrences || occurrences.some((id) =>
    !activeConversion.hiddenOccurrences.has(id));
}

function applyComponentVisibility(root) {
  if (!root || !activeConversion) return;
  if (typeof root.traverse !== 'function') {
    console.error('Cannot apply component visibility to a non-Three.js root',
      root);
    return;
  }
  root.traverse((object) => {
    const occurrenceId = object.userData.componentOccurrenceId;
    if (occurrenceId) {
      object.visible = !activeConversion.hiddenOccurrences.has(occurrenceId);
      return;
    }
    const key = object.userData.componentKey;
    if (key) object.visible = componentIsVisible(key);
  });
}

function registerComponentGroup(group, key, occurrenceId = null) {
  if (!activeConversion || !key) return;
  if (occurrenceId) {
    group.userData.componentOccurrenceId = occurrenceId;
    group.visible = !activeConversion.hiddenOccurrences.has(occurrenceId);
  } else {
    group.userData.componentKey = key;
    group.visible = componentIsVisible(key);
  }
}

function groupComponentNodes(root) {
  if (!activeConversion) return;
  const groups = new Map();
  const componentNodes = [];
  const collect = (node) => {
    const key = componentNodeKey(node.userData.component || node.name);
    if (activeConversion.componentKeys.has(key)) {
      const parent = node.userData.parentComponent;
      const index = node.userData.instanceIndex;
      const occurrenceId = parent && Number.isInteger(index)
        ? instanceOccurrenceKey(parent, node.userData.component || node.name,
          index)
        : null;
      const groupKey = occurrenceId || 'component:' + key;
      componentNodes.push({
        node: node, key: key, occurrenceId: occurrenceId, groupKey: groupKey,
      });
      return;
    }
    for (const child of node.children) collect(child);
  };
  for (const child of root.children) collect(child);
  for (const { node, key, occurrenceId, groupKey } of componentNodes) {
    let group = groups.get(groupKey);
    if (!group) {
      group = new THREE.Group();
      group.name = groupKey;
      registerComponentGroup(group, key, occurrenceId);
      groups.set(groupKey, group);
      root.add(group);
    }
    group.attach(node);
  }
}

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
  groupComponentNodes(current);
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
  applyComponentVisibility(current);
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
      const component = new THREE.Group();
      const key = componentKey(artifact.part);
      component.name = 'component:' + key;
      registerComponentGroup(component, key);
      component.add(gltf.scene || gltf.scenes[0]);
      current.add(component);
      styleProxies(current);
      applyProxyVisibility();
      applyComponentVisibility(current);
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
  activeConversion.hiddenOccurrences = new Set();
  activeConversion.componentKeys = new Set();
  activeConversion.occurrencesByComponent = new Map();
  const conversionState = activeConversion;
  startTitleAnimation(file.name, conversionState);
  referenceRootName = file.name;
  referenceChildren.clear();
  knownMissingComponents.clear();
  renderComponentTree();
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
  dl.style.display = 'none';
  if (url) URL.revokeObjectURL(url);
  if (current) {
    scene.remove(current);
    current = null;
  }
  clearPartInfo();
  const lookup = new FormData();
  for (const part of files) lookup.append('files', part);
  const lookupPromise = fetch('/references', { method: 'POST', body: lookup })
    .then(async (res) => res.ok ? res.json() : null)
    .then(tree => {
      if (tree && activeConversion === conversionState) renderReferences(tree);
    })
    .catch(() => {});
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
      activeConversion.jobId = job.job_id;
      void loadPartInfo(job.job_id);
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
      finishTitle(file.name, conversionState);
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
    .catch((err) => {
      finishTitle(file.name, conversionState);
      finish(String(err.message || err));
    });
  void lookupPromise;
}

function formatDuration(milliseconds) {
  const seconds = milliseconds / 1000;
  return seconds < 10 ? seconds.toFixed(1) + 's' : seconds.toFixed(0) + 's';
}

function formatBytes(bytes) {
  if (bytes < 1024) return bytes + ' B';
  return (bytes / 1024).toFixed(1) + ' kB';
}

function clearPartInfo() {
  partInfo.hidden = true;
  partPreview.hidden = true;
  partPreview.removeAttribute('src');
  partPreviewDims.textContent = '';
  partAttributes.replaceChildren();
}

function renderPartInfo(metadata) {
  partAttributes.replaceChildren();
  for (const attribute of metadata.attributes || []) {
    const row = document.createElement('div');
    row.className = 'attr';
    const key = document.createElement('span');
    key.className = 'key';
    key.textContent = attribute.title || '(untitled)';
    row.append(key, document.createTextNode(' = ' + attribute.value));
    partAttributes.appendChild(row);
  }
  const preview = metadata.preview;
  partPreview.hidden = true;
  partPreview.removeAttribute('src');
  if (preview && preview.url) {
    partPreview.src = preview.url;
    partPreview.hidden = false;
    partPreviewDims.textContent = preview.width + ' × ' + preview.height
      + ' · ' + formatBytes(preview.byte_size);
  } else {
    partPreviewDims.textContent = 'Preview: none';
  }
  partInfo.hidden = !preview && !(metadata.attributes || []).length;
}

async function loadPartInfo(jobId) {
  const conversion = activeConversion;
  try {
    const response = await fetch('/convert/' + jobId + '/metadata');
    if (!response.ok) return;
    const metadata = await response.json();
    if (activeConversion !== conversion || activeConversion.jobId !== jobId) return;
    renderPartInfo(metadata);
  } catch (error) {}
}

function renderReferences(tree) {
  referenceRootName = tree.name;
  const merge = (node, root = false) => {
    const key = componentKey(node.name);
    if (!root && !node.found) knownMissingComponents.add(key);
    else if (node.found) knownMissingComponents.delete(key);
    setReferenceChildren(node.name, (node.children || []).map((child) =>
      child.name));
    for (const child of node.children || []) merge(child);
  };
  merge(tree, true);
  renderComponentTree();
}

function setReferenceChildren(parentName, children) {
  const key = componentKey(parentName);
  const names = children.map(String);
  const previous = referenceChildren.get(key);
  if (previous && (previous.length > names.length
      || (previous.length === names.length
        && previous.every((name, index) => name === names[index])))) {
    return false;
  }
  referenceChildren.set(key, names);
  return true;
}

function mergeReferenceUpdates(updates) {
  let changed = false;
  for (const update of updates || []) {
    changed = setReferenceChildren(update.parent, update.children || [])
      || changed;
  }
  if (changed) renderComponentTree();
}

function renderComponentTree() {
  refs.replaceChildren();
  let count = 0;
  activeConversion.componentKeys = new Set();
  activeConversion.occurrencesByComponent = new Map();
  const add = (name, parent, depth, ancestors, occurrenceId) => {
    const row = document.createElement('div');
    count += 1;
    row.classList.add('component');
    const key = componentKey(name);
    activeConversion.componentKeys.add(key);
    let occurrences = activeConversion.occurrencesByComponent.get(key);
    if (!occurrences) {
      occurrences = [];
      activeConversion.occurrencesByComponent.set(key, occurrences);
    }
    occurrences.push(occurrenceId);
    const notFound = depth && knownMissingComponents.has(key);
    if (notFound) activeConversion.hiddenOccurrences.add(occurrenceId);
    row.dataset.partName = key;
    row.dataset.occurrenceId = occurrenceId;
    row.dataset.notFound = notFound ? 'true' : 'false';
    const checkbox = document.createElement('input');
    checkbox.type = 'checkbox';
    checkbox.className = 'component-visibility';
    checkbox.checked = !notFound
      && !activeConversion.hiddenOccurrences.has(occurrenceId);
    checkbox.disabled = Boolean(notFound);
    checkbox.setAttribute('aria-label', 'Show ' + name);
    row.appendChild(checkbox);
    const label = document.createElement('span');
    label.className = 'component-name';
    label.textContent = (depth ? '↳ ' : '') + name
      + (notFound ? ' (not found — upload with assembly)' : '');
    row.appendChild(label);
    if (notFound) row.classList.add('missing');
    parent.appendChild(row);
    const children = referenceChildren.get(key) || [];
    if (children.length && !ancestors.has(key)) {
      const childList = document.createElement('div');
      childList.className = 'component-children';
      row.after(childList);
      const nestedAncestors = new Set(ancestors);
      nestedAncestors.add(key);
      const siblingCounts = new Map();
      for (const child of children) {
        const childKey = componentKey(child);
        const index = siblingCounts.get(childKey) || 0;
        siblingCounts.set(childKey, index + 1);
        add(child, childList, depth + 1, nestedAncestors,
          instanceOccurrenceKey(name, child, index));
      }
    }
  };
  if (referenceRootName) {
    const key = componentKey(referenceRootName);
    add(referenceRootName, refs, 0, new Set(), 'root/' + key);
  }
  progress.max = Math.max(count, 1);
  if (activeConversion && activeConversion.latestState) {
    updateConversionProgress(activeConversion.latestState);
  }
  applyComponentVisibility(current);
  updateToggleAllButton();
}

function updateConversionProgress(state) {
  activeConversion.latestState = state;
  const completedParts = new Set((state.completed_parts || [])
    .map(componentKey));
  const missing = new Set((state.missing_parts || [])
    .map(componentKey));
  const failedParts = new Set((state.failed_parts || [])
    .map(componentKey));
  const currentComponent = componentKey(state.current);
  let passed = 0;
  let failed = 0;
  let processed = 0;
  let visibilityChanged = false;
  refs.querySelectorAll('.component').forEach((row) => {
    const name = row.dataset.partName;
    const isActive = name === currentComponent;
    const isDone = completedParts.has(name);
    const isFailed = failedParts.has(name);
    const isMissing = missing.has(name) || isFailed
      || (row.dataset.notFound === 'true' && !isDone && !isActive);
    const unavailable = !isActive && isMissing;
    const checkbox = row.querySelector('.component-visibility');
    const occurrenceId = row.dataset.occurrenceId;
    if (checkbox) {
      checkbox.disabled = unavailable;
      checkbox.checked = !unavailable
        && !activeConversion.hiddenOccurrences.has(occurrenceId);
    }
    if (unavailable
        && !activeConversion.hiddenOccurrences.has(occurrenceId)) {
      activeConversion.hiddenOccurrences.add(occurrenceId);
      visibilityChanged = true;
    }
    row.classList.toggle('active', isActive);
    row.classList.toggle('done',
      !isActive && !isMissing && !isFailed && isDone);
    row.classList.toggle('missing', !isActive && isMissing);
    row.classList.toggle('failed', !isActive && isFailed);
    if (isActive) {
      row.setAttribute('aria-label', name + ' — processing');
    } else if (isMissing || isFailed) {
      row.setAttribute('aria-label', name + (isFailed
        ? ' — failed' : ' — unresolved'));
      failed += 1;
      processed += 1;
    } else if (isDone) {
      row.setAttribute('aria-label', name + ' — passed');
      passed += 1;
      processed += 1;
    } else {
      row.setAttribute('aria-label', name + ' — pending');
    }
  });
  if (visibilityChanged) applyComponentVisibility(current);
  updateToggleAllButton();
  const total = refs.querySelectorAll('.component').length;
  progress.max = Math.max(total, 1);
  progress.value = Math.min(processed, progress.max);
  const detail = passed + ' passed'
    + (failed ? ', ' + failed + ' failed/unresolved' : '');
  const currentLabel = state.current
    ? ' "' + state.current + '"' : '';
  stats.textContent = 'Converting component' + currentLabel + ' — '
    + processed + '/' + total + ' processed (' + detail + ')'
    + ' — step ' + formatDuration(
      performance.now() - activeConversion.phaseStarted)
    + ', total ' + formatDuration(
      performance.now() - activeConversion.started);
}

async function waitForConversion(jobId) {
  while (true) {
    const response = await fetch('/convert/' + jobId + '/status');
    if (!response.ok) throw new Error('Conversion status unavailable');
    const state = await response.json();
    state.job_id = jobId;
    mergeReferenceUpdates(state.reference_updates);
    updateConversionProgress(state);
    await loadNewArtifacts(state);
    if (state.status === 'complete') progress.value = progress.max;
    if (state.status === 'complete' || state.status === 'failed') return state;
    await new Promise((resolve) => setTimeout(resolve, 400));
  }
}
