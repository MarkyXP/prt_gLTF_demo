// Display style: shaded vs wireframe plus a global opacity slider.
//
// The slider starts at 100% and means "use each component's built-in glTF
// alpha" (Auto): a 100% opaque part and a 50% transparent part render exactly
// as converted. Dragging the slider below 100% hard-overrides every
// non-proxy material to the slider value, unifying mixed transparencies.
// Returning the slider to 100% restores the built-in values. Proxy
// placeholders and translucent envelopes are excluded: proxy_styling.js owns
// their materials and the sidebar toggles stay authoritative for them.
const PROXY_ROLES = new Set(['translucent-bounds', 'placeholder']);

const shadedRadio = document.getElementById('style-shaded');
const wireframeRadio = document.getElementById('style-wireframe');
const opacitySlider = document.getElementById('opacity-slider');
const opacityValue = document.getElementById('opacity-value');

let currentRoot = null;
let originals = new Map();

function isProxyMesh(object) {
  return Boolean(object.userData && PROXY_ROLES.has(object.userData.proxyRole));
}

function collectMaterials(root) {
  const materials = new Set();
  root.traverse((object) => {
    if (!object.isMesh || isProxyMesh(object)) return;
    const list = Array.isArray(object.material)
      ? object.material : [object.material];
    for (const material of list) {
      if (material) materials.add(material);
    }
  });
  return materials;
}

function snapshotOriginals(root) {
  originals = new Map();
  for (const material of collectMaterials(root)) {
    originals.set(material, {
      opacity: material.opacity,
      transparent: material.transparent,
      depthWrite: material.depthWrite,
      wireframe: material.wireframe,
    });
  }
}

function applyDisplayStyle() {
  if (!currentRoot) return;
  const wireframe = wireframeRadio.checked;
  const slider = Number(opacitySlider.value);
  const override = slider < 100;
  const opacity = slider / 100;
  for (const [material, original] of originals) {
    if (override) {
      material.opacity = opacity;
      material.transparent = true;
      material.depthWrite = false;
    } else {
      material.opacity = original.opacity;
      material.transparent = original.transparent;
      material.depthWrite = original.depthWrite;
    }
    material.wireframe = wireframe;
    material.needsUpdate = true;
  }
}

function updateOpacityReadout() {
  opacityValue.textContent = Number(opacitySlider.value) < 100
    ? opacitySlider.value + '%' : 'Auto';
}

export function initDisplayStyle() {
  opacitySlider.value = '100';
  updateOpacityReadout();
  shadedRadio.addEventListener('change', applyDisplayStyle);
  wireframeRadio.addEventListener('change', applyDisplayStyle);
  opacitySlider.addEventListener('input', () => {
    updateOpacityReadout();
    applyDisplayStyle();
  });
}

export function applyDisplayStyleToModel(root) {
  currentRoot = root;
  snapshotOriginals(root);
  applyDisplayStyle();
}
