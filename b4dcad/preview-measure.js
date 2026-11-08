import * as THREE from "three";

const NORMAL_TOLERANCE = 1e-8;
const SNAP_PIXELS = 8;

// STL vertices on different facets may carry different normals. Weld by position
// only, and keep the triangle order so raycast face indices address this topology.
function meshTopology(geometry) {
  const positions = geometry.attributes.position;
  const index = geometry.index;
  const vertexIds = new Map();
  const vertices = [];
  const incidentEdges = [];
  const remap = Array.from({ length: positions.count }, (_, i) => {
    const point = new THREE.Vector3().fromBufferAttribute(positions, i);
    const key = [point.x, point.y, point.z].join(",");
    if (!vertexIds.has(key)) {
      vertexIds.set(key, vertices.length);
      vertices.push(point);
      incidentEdges.push([]);
    }
    return vertexIds.get(key);
  });

  const edges = new Map();
  const triangles = [];
  const count = index ? index.count : positions.count;
  for (let i = 0; i < count; i += 3) {
    const ids = [0, 1, 2].map((offset) => remap[index ? index.getX(i + offset) : i + offset]);
    const [a, b, c] = ids.map((id) => vertices[id]);
    const normal = b.clone().sub(a).cross(c.clone().sub(a));
    const triangle = { ids, area: normal.length() / 2, normal: normal.normalize(), edges: [] };
    triangles.push(triangle);
    if (!triangle.area) continue;
    for (let j = 0; j < 3; j++) {
      const ends = [ids[j], ids[(j + 1) % 3]].sort((x, y) => x - y);
      const key = ends.join(",");
      if (!edges.has(key)) {
        const edge = { ends, triangles: [] };
        edges.set(key, edge);
        for (const id of ends) incidentEdges[id].push(edge);
      }
      const edge = edges.get(key);
      edge.triangles.push(triangle);
      triangle.edges.push(edge);
    }
  }
  return { vertices, triangles, incidentEdges };
}

function samePlane(a, b, vertices) {
  if (a.normal.dot(b.normal) < 1 - NORMAL_TOLERANCE) return false;
  const origin = vertices[a.ids[0]];
  return b.ids.every((id) => {
    const point = vertices[id];
    const offset = point.clone().sub(origin);
    // Account for the precision of STL's float32 coordinates, including when
    // the geometry has been translated far from the origin.
    const tolerance = Math.max(1e-6, 2 ** -22 * Math.max(origin.length(), point.length()));
    return Math.abs(a.normal.dot(offset)) <= tolerance;
  });
}

function isFeatureEdge(edge, vertices) {
  if (edge.feature === undefined) {
    const [a, b] = edge.triangles;
    edge.feature = edge.triangles.length !== 2 || !samePlane(a, b, vertices);
  }
  return edge.feature;
}

function planeFeature(seed, { vertices }) {
  if (seed.feature) return seed.feature;
  const triangles = [seed];
  const boundary = new Set();
  const feature = { kind: "face", normal: seed.normal, triangles, boundary, area: 0 };
  seed.feature = feature;
  for (const triangle of triangles) {
    feature.area += triangle.area;
    for (const edge of triangle.edges) {
      if (isFeatureEdge(edge, vertices)) {
        boundary.add(edge);
        continue;
      }
      const neighbor = edge.triangles.find((candidate) => candidate !== triangle);
      if (neighbor.feature === feature) continue;
      if (neighbor.feature || !samePlane(seed, neighbor, vertices)) {
        boundary.add(edge);
        continue;
      }
      neighbor.feature = feature;
      triangles.push(neighbor);
    }
  }
  return feature;
}

function otherEnd(edge, id) {
  return edge.ends[0] === id ? edge.ends[1] : edge.ends[0];
}

function continuation(edge, id, { vertices, incidentEdges }) {
  const origin = vertices[id];
  const direction = vertices[otherEnd(edge, id)].clone().sub(origin).normalize();
  const matches = incidentEdges[id].filter((candidate) =>
    candidate !== edge && isFeatureEdge(candidate, vertices) &&
    vertices[otherEnd(candidate, id)].clone().sub(origin).normalize().dot(direction) < -1 + NORMAL_TOLERANCE
  );
  if (matches.length === 1) return matches[0];
}

function straightFeature(seed, topology) {
  if (seed.line) return seed.line;
  const { vertices } = topology;
  const direction = vertices[seed.ends[1]].clone().sub(vertices[seed.ends[0]]).normalize();
  const chain = new Set([seed]);
  const ends = seed.ends.map((start, side) => {
    let edge = seed;
    let id = start;
    for (let next = continuation(edge, id, topology); next && !chain.has(next); next = continuation(edge, id, topology)) {
      const nextId = otherEnd(next, id);
      const alignment = vertices[nextId].clone().sub(vertices[id]).normalize().dot(direction);
      if ((side ? alignment : -alignment) < 1 - NORMAL_TOLERANCE) break;
      chain.add(next);
      edge = next;
      id = nextId;
    }
    return vertices[id];
  });
  const line = { kind: "edge", ends };
  for (const edge of chain) edge.line = line;
  return line;
}

function screenPoint(point, camera, width, height) {
  const projected = point.clone().project(camera);
  return projected.set((projected.x + 1) * width / 2, (1 - projected.y) * height / 2, projected.z);
}

export class PreviewMeasure {
  constructor({ renderer, camera, panel, button, label, format, requestRender, onEnable }) {
    this.renderer = renderer;
    this.camera = camera;
    this.panel = panel;
    this.button = button;
    this.label = label;
    this.format = format;
    this.requestRender = requestRender;
    this.onEnable = onEnable;
    this.output = panel.querySelector("output");
    this.pointMode = panel.querySelector("select");
    this.scene = new THREE.Scene();
    this.hoverLayer = new THREE.Group();
    this.resultLayer = new THREE.Group();
    this.scene.add(this.hoverLayer, this.resultLayer);
    this.ray = new THREE.Raycaster();
    this.selections = [];
    this.enabled = false;

    button.addEventListener("click", () => this.setEnabled(!this.enabled));
    panel.querySelector("button").addEventListener("click", () => this.clear());
    this.pointMode.addEventListener("change", () => this.clearHover());
    const canvas = renderer.domElement;
    canvas.addEventListener("pointerdown", (event) => {
      if (this.press) this.press.moved = true;
      else if (this.enabled && event.button === 0 && event.isPrimary) {
        this.press = { id: event.pointerId, x: event.clientX, y: event.clientY, moved: false };
      }
      this.clearHover();
    });
    canvas.addEventListener("pointermove", (event) => {
      if (this.press) {
        if (Math.hypot(event.clientX - this.press.x, event.clientY - this.press.y) > 4) this.press.moved = true;
      } else if (this.enabled && this.surface && !event.buttons && event.pointerType !== "touch") {
        this.pendingHover = [event.clientX, event.clientY];
        this.requestRender();
      }
    });
    canvas.addEventListener("pointerup", (event) => {
      if (this.press?.id !== event.pointerId) return;
      const { moved, x, y } = this.press;
      this.press = null;
      if (!moved && Math.hypot(event.clientX - x, event.clientY - y) <= 4) {
        this.select(this.pick(event.clientX, event.clientY));
      }
    });
    canvas.addEventListener("lostpointercapture", (event) => {
      if (this.press?.id === event.pointerId) this.press = null;
    });
    canvas.addEventListener("pointerleave", () => this.clearHover());
  }

  setGeometry(geometry) {
    if (this.surface) {
      this.scene.remove(this.surface);
      this.surface.material.dispose();
    }
    this.topology = null;
    this.surface = null;
    if (geometry?.attributes.position.count) {
      // Write the model's depth before painting face highlights. This also
      // works after AO compositing, where the canvas lacks the model's depth.
      const material = new THREE.MeshBasicMaterial({
        colorWrite: false, polygonOffset: true, polygonOffsetFactor: 1, polygonOffsetUnits: 1,
      });
      this.surface = new THREE.Mesh(geometry, material);
      this.surface.renderOrder = -1;
      this.scene.add(this.surface);
    }
    this.button.disabled = !this.surface && !this.enabled;
    this.clear();
  }

  setEnabled(enabled) {
    if (enabled === this.enabled) return;
    this.enabled = enabled;
    this.button.disabled = !this.surface && !enabled;
    this.button.setAttribute("aria-pressed", String(enabled));
    this.panel.hidden = !enabled;
    this.renderer.domElement.classList.toggle("measuring", enabled);
    this.clear();
    if (enabled) this.onEnable();
  }

  clearLayer(layer) {
    for (const object of [...layer.children]) {
      layer.remove(object);
      object.geometry.dispose();
      object.material.dispose();
    }
  }

  clearHover() {
    this.pendingHover = null;
    if (!this.hover) return;
    this.hover = null;
    this.clearLayer(this.hoverLayer);
    this.requestRender();
  }

  clear() {
    this.press = null;
    this.clearHover();
    this.selections = [];
    this.updateResult();
  }

  pick(clientX, clientY) {
    if (!this.enabled || !this.surface) return null;
    const { left, top, width, height } = this.renderer.domElement.getBoundingClientRect();
    const x = clientX - left;
    const y = clientY - top;
    if (x < 0 || x > width || y < 0 || y > height || !width || !height) return null;
    this.camera.updateMatrixWorld();
    this.ray.setFromCamera(new THREE.Vector2(2 * x / width - 1, 1 - 2 * y / height), this.camera);
    const hit = this.ray.intersectObject(this.surface, false)[0];
    if (!hit) return null;
    if (this.pointMode.value === "point") {
      return { feature: { kind: "point", point: hit.point }, point: hit.point };
    }

    this.topology ??= meshTopology(this.surface.geometry);
    const { vertices, triangles } = this.topology;
    const face = planeFeature(triangles[hit.faceIndex], this.topology);
    const cursor = new THREE.Vector2(x, y);
    let nearestVertex = null;
    let nearestEdge = null;
    let vertexDistance = SNAP_PIXELS;
    let edgeDistance = SNAP_PIXELS;
    const projected = new Map();
    for (const edge of face.boundary) {
      const ends = edge.ends.map((id) => {
        if (!projected.has(id)) {
          const p = screenPoint(vertices[id], this.camera, width, height);
          const point = new THREE.Vector2(p.x, p.y);
          projected.set(id, p.z >= -1 && p.z <= 1 ? point : null);
          const distance = point.distanceTo(cursor);
          if (projected.get(id) && distance < vertexDistance) {
            nearestVertex = id;
            vertexDistance = distance;
          }
        }
        return projected.get(id);
      });
      if (!ends.every(Boolean)) continue;
      const [a, b] = ends;
      const segment = b.clone().sub(a);
      if (!segment.lengthSq()) continue;
      const t = THREE.MathUtils.clamp(cursor.clone().sub(a).dot(segment) / segment.lengthSq(), 0, 1);
      const screen = a.clone().addScaledVector(segment, t);
      const distance = screen.distanceTo(cursor);
      if (distance < edgeDistance) {
        nearestEdge = { edge, screen };
        edgeDistance = distance;
      }
    }
    if (nearestVertex !== null) {
      const point = vertices[nearestVertex];
      if (this.snapIsVisible(projected.get(nearestVertex), point, point, width, height)) {
        return { feature: { kind: "point", point }, point };
      }
    }
    if (nearestEdge) {
      const { edge, screen } = nearestEdge;
      const [a, b] = edge.ends.map((id) => vertices[id]);
      if (this.snapIsVisible(screen, a, b, width, height)) {
        return { feature: straightFeature(edge, this.topology), point: hit.point };
      }
    }
    return { feature: face, point: hit.point };
  }

  snapIsVisible(screen, a, b, width, height) {
    this.ray.setFromCamera(new THREE.Vector2(2 * screen.x / width - 1, 1 - 2 * screen.y / height), this.camera);
    const occluder = this.ray.intersectObject(this.surface, false)[0];
    if (!occluder) return true;
    const point = a === b ? a : new THREE.Vector3();
    if (a !== b) this.ray.ray.distanceSqToSegment(a, b, null, point);
    const distance = this.ray.ray.origin.distanceTo(point);
    return distance - occluder.distance <= Math.max(1e-6, distance * 1e-7);
  }

  select(pick) {
    if (!pick) {
      this.clear();
      return;
    }
    this.clearHover();
    const index = this.selections.findIndex(({ feature }) =>
      feature === pick.feature || (feature.kind === "point" && feature.point === pick.point)
    );
    if (index >= 0) {
      this.selections.splice(index, 1);
    } else {
      if (this.selections.length === 2) this.selections = [];
      this.selections.push(pick);
    }
    this.updateResult();
  }

  markPoint(layer, point, color, size = 8) {
    const geometry = new THREE.BufferGeometry().setFromPoints([point]);
    const material = new THREE.PointsMaterial({ color, size, sizeAttenuation: false, transparent: true, depthTest: false, depthWrite: false });
    const marker = new THREE.Points(geometry, material);
    marker.renderOrder = 3;
    layer.add(marker);
  }

  drawLine(layer, ends, color) {
    const geometry = new THREE.BufferGeometry().setFromPoints(ends);
    const material = new THREE.LineBasicMaterial({ color, transparent: true, depthTest: false, depthWrite: false });
    const line = new THREE.Line(geometry, material);
    line.renderOrder = 2;
    layer.add(line);
  }

  drawFeature(layer, { kind, point, ends, triangles }, color) {
    if (kind === "point") {
      this.markPoint(layer, point, color);
    } else if (kind === "edge") {
      this.drawLine(layer, ends, color);
      for (const end of ends) this.markPoint(layer, end, color, 5);
    } else {
      const positions = [];
      for (const triangle of triangles) {
        for (const id of triangle.ids) {
          const { x, y, z } = this.topology.vertices[id];
          positions.push(x, y, z);
        }
      }
      const geometry = new THREE.BufferGeometry().setAttribute("position", new THREE.Float32BufferAttribute(positions, 3));
      const material = new THREE.MeshBasicMaterial({ color, transparent: true, opacity: 0.48, depthWrite: false, side: THREE.DoubleSide });
      layer.add(new THREE.Mesh(geometry, material));
    }
  }

  updateResult() {
    this.clearLayer(this.resultLayer);
    this.labelAnchor = null;
    this.label.hidden = true;
    const [first, second] = this.selections;
    const format = this.format;
    const length = (n) => `${format(n)} mm`;
    const coords = ({ x, y, z }) => `X ${format(x)} · Y ${format(y)} · Z ${format(z)} mm`;
    const colors = [0x006bc8, 0x9839c7];
    const rows = this.selections.map(({ feature, point }, index) => {
      this.drawFeature(this.resultLayer, feature, colors[index]);
      if (feature.kind === "point") return `Point ${index + 1} · ${coords(point)}`;
      if (feature.kind === "face") return `Face ${index + 1} · ${format(feature.area)} mm²`;
      return `Edge ${index + 1} · ${length(feature.ends[0].distanceTo(feature.ends[1]))}`;
    });
    let ends;
    let label;
    if (second) {
      const a = first.feature;
      const b = second.feature;
      if (a.kind === "point" && b.kind === "point") {
        ends = [first.point, second.point];
        label = length(first.point.distanceTo(second.point));
        rows.push(`Distance · ${label}`, `Δ ${coords(second.point.clone().sub(first.point))}`);
      } else if (a.kind === "face" && b.kind === "face" && Math.abs(a.normal.dot(b.normal)) >= 1 - NORMAL_TOLERANCE) {
        const offset = second.point.clone().sub(first.point).dot(b.normal);
        ends = [first.point, first.point.clone().addScaledVector(b.normal, offset)];
        label = length(Math.abs(offset));
        rows.push(`Plane spacing · ${label}`);
      } else {
        rows.push("Select two points or two parallel faces to measure distance.");
      }
    } else if (first?.feature.kind === "edge") {
      ends = first.feature.ends;
      label = length(ends[0].distanceTo(ends[1]));
    } else if (first?.feature.kind === "face") {
      this.labelAnchor = first.point;
      this.label.textContent = `${format(first.feature.area)} mm²`;
    }
    if (ends) {
      const color = 0x173047;
      this.drawLine(this.resultLayer, ends, color);
      for (const end of ends) this.markPoint(this.resultLayer, end, color, 5);
      this.labelAnchor = ends[0].clone().lerp(ends[1], 0.5);
      this.label.textContent = label;
    }
    this.output.textContent = rows.join("\n") || (this.surface
      ? "Click up to two features. Esc clears the selection."
      : "Waiting for model geometry.");
    this.requestRender();
  }

  render() {
    if (!this.enabled || !this.surface) return;
    if (this.pendingHover) {
      const pick = this.pick(...this.pendingHover);
      this.pendingHover = null;
      const previous = this.hover?.feature;
      const next = pick?.feature;
      if (next !== previous && (!next?.point || next.point !== previous?.point)) {
        this.clearLayer(this.hoverLayer);
        if (next) this.drawFeature(this.hoverLayer, next, 0x13a092);
      }
      this.hover = pick;
    }
    if (this.hoverLayer.children.length || this.resultLayer.children.length) {
      const { renderer } = this;
      renderer.autoClear = false;
      renderer.clearDepth();
      renderer.render(this.scene, this.camera);
      renderer.autoClear = true;
    }
    if (this.labelAnchor) {
      const canvas = this.renderer.domElement;
      const p = screenPoint(this.labelAnchor, this.camera, canvas.clientWidth, canvas.clientHeight);
      this.label.hidden = p.z < -1 || p.z > 1 || p.x < 0 || p.x > canvas.clientWidth || p.y < 0 || p.y > canvas.clientHeight;
      if (!this.label.hidden) {
        this.label.style.left = `${p.x}px`;
        this.label.style.top = `${p.y}px`;
      }
    }
  }
}
