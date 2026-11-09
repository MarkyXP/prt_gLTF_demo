import * as THREE from 'three';

// Assembly-proxy styling. GLTFLoader merges the per-mesh glTF `extras` into
// Mesh.userData (assignExtrasToUserData()), so every Mesh carries
// mesh.userData.proxyRole and each per-instance node clone keeps a deep copy
// of it. Proxy bodies are restyled and get a wireframe-outline child per mesh
// node; normal parts are never touched.
const chkPlaceholders = document.getElementById('tplace');
const chkEnvelopes = document.getElementById('ttranslucent');
const PROXY_EDGE_COLOR = {
  'translucent-bounds': 0x4a90d9,   // blue envelope outline
  'placeholder': 0xd98f4a,          // amber outline, body hidden
};
let styledProxies = [];
let styledEdges = [];   // scene-level LineSegments overlays with baked coords
let sceneRef;
let cameraRef;

export function initProxyStyling({ scene, camera }) {
  sceneRef = scene;
  cameraRef = camera;
}

// --- hidden-edge removal for the proxy envelopes ------------------------
// A proxy body is always an axis-aligned box, so each of its 12 edges is
// shared by exactly 2 faces with fixed outward normals. An edge is visible
// iff at least one of its two faces faces the camera (outward normal has
// a positive component towards the camera); edges between two back faces
// are the "hidden edges" a paper drawing would draw dashed.
function boxEdges(geometry) {
  geometry.computeBoundingBox();
  const bounds = geometry.boundingBox;
  if (!bounds || !(bounds.max.x - bounds.min.x > 0)) return null;
  const axes = ['x', 'y', 'z'];
  const lows = axes.map((a) => bounds.min[a]);
  const highs = axes.map((a) => bounds.max[a]);
  const corners = [];
  for (let index = 0; index < 8; index += 1) {
    corners.push(new THREE.Vector3(
      (index & 1) ? lows[0] : highs[0],
      (index & 2) ? lows[1] : highs[1],
      (index & 4) ? lows[2] : highs[2]));
  }
  // Edges of a cube: pairs of corners differing in exactly one axis. The
  // two adjacent faces are those fixed on that axis' high/low sides.
  const edges = [];
  for (let a = 0; a < corners.length; a += 1) {
    for (let b = a + 1; b < corners.length; b += 1) {
      const differing = [0, 1, 2].filter((axis) =>
        Math.abs(corners[a].getComponent(axis)
          - corners[b].getComponent(axis)) > 1e-12);
      if (differing.length !== 1) continue;
      const moving = differing[0];
      const faceAxis = (moving + 1) % 3;
      const otherAxis = (moving + 2) % 3;
      const faces = [faceAxis, otherAxis].map((axis) => {
        const normal = new THREE.Vector3();
        const side = corners[a].getComponent(axis)
          > lows[axis] + 1e-12 ? 1 : -1;
        normal.setComponent(axis, side);
        return normal;
      });
      edges.push({ a: corners[a], b: corners[b], faces: faces });
    }
  }
  if (edges.length !== 12) return null;
  return { corners, edges };
}

function addEdgeOverlay(mesh, color, checkEl) {
  if (!mesh.geometry || !mesh.geometry.attributes.position) return;
  let overlay;
  const box = boxEdges(mesh.geometry);
  if (box) {
    // Hidden-edge removal: only silhouette-visible edges are drawn, with
    // positions rewritten in world space each frame. The overlay is added
    // to the scene so its baked world coordinates stay authoritative.
    const geometry = new THREE.BufferGeometry();
    geometry.setAttribute('position',
      new THREE.BufferAttribute(new Float32Array(12 * 2 * 3), 3)
        .setUsage(THREE.DynamicDrawUsage));
    overlay = new THREE.LineSegments(
      geometry, new THREE.LineBasicMaterial({ color: color }));
    overlay.renderOrder = 1;
    overlay.frustumCulled = false;
    overlay.userData = {
      mesh: mesh,
      edges: box.edges,
      checked: checkEl,
    };
    styledEdges.push(overlay);
    sceneRef.add(overlay);
  } else {
    overlay = new THREE.LineSegments(
      new THREE.EdgesGeometry(mesh.geometry, 1),
      new THREE.LineBasicMaterial({ color: color }));
    overlay.name = mesh.name + ' wireframe';
    overlay.renderOrder = 1;
    mesh.add(overlay);
  }
}

export function updateProxyEdges() {
  const cameraPosition = cameraRef.position;
  const normalMatrix = new THREE.Matrix3();
  const point = new THREE.Vector3();
  const normal = new THREE.Vector3();
  for (const edgeData of styledEdges) {
    const data = edgeData.userData;
    if (!data) continue;
    data.mesh.updateMatrixWorld(true);
    const matrix = data.mesh.matrixWorld;
    normalMatrix.getNormalMatrix(matrix);
    const positions = edgeData.geometry.attributes.position.array;
    let count = 0;
    for (const edge of data.edges) {
      const visible = edge.faces.some((localNormal) => {
        normal.copy(localNormal).applyMatrix3(normalMatrix);
        point.copy(edge.a).applyMatrix4(matrix);
        return normal.dot(cameraPosition.clone().sub(point)) > 0;
      });
      if (!visible) continue;
      for (const corner of [edge.a, edge.b]) {
        point.copy(corner).applyMatrix4(matrix);
        positions[count * 3 + 0] = point.x;
        positions[count * 3 + 1] = point.y;
        positions[count * 3 + 2] = point.z;
        count += 1;
      }
    }
    edgeData.geometry.setDrawRange(0, count);
    edgeData.geometry.attributes.position.needsUpdate = true;
    edgeData.geometry.computeBoundingSphere();
  }
}

export function styleProxies(root) {
  styledProxies = [];
  for (const overlay of styledEdges) sceneRef.remove(overlay);
  styledEdges = [];
  const proxies = [];
  root.traverse((object) => {
    if (object.isMesh && object.userData && object.userData.proxyRole
        && object.userData.proxyRole in PROXY_EDGE_COLOR) {
      proxies.push(object);
    }
  });
  for (const mesh of proxies) {
    const role = mesh.userData.proxyRole;
    // GLTF material dedup can share one material object across proxy
    // instances (and in principle with non-proxy meshes); clone before
    // restyling so nothing else inherits these overrides.
    mesh.material = mesh.material.clone();
    if (role === 'placeholder') {
      mesh.material.visible = false;      // only the wireframe remains
    } else {
      mesh.material.transparent = true;   // faint see-through envelope
      mesh.material.opacity = 0.12;
      mesh.material.depthWrite = false;
      mesh.material.side = THREE.DoubleSide;
    }
    addEdgeOverlay(mesh, PROXY_EDGE_COLOR[role],
      role === 'placeholder' ? chkPlaceholders : chkEnvelopes);
    styledProxies.push({ mesh: mesh, role: role });
  }
}

export function applyProxyVisibility() {
  for (const entry of styledProxies) {
    entry.mesh.visible = entry.role === 'placeholder'
      ? chkPlaceholders.checked
      : chkEnvelopes.checked;             // child wireframes hide with it
  }
  for (const overlay of styledEdges) {
    overlay.visible = overlay.userData.checked?.checked ?? true;
  }
}

chkPlaceholders.addEventListener('change', applyProxyVisibility);
chkEnvelopes.addEventListener('change', applyProxyVisibility);
