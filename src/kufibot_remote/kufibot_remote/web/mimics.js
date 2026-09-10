import { evaluateMotion } from "./mimic-math.js";
import * as THREE from "./three.module.js";
import { GLTFLoader } from "./GLTFLoader.js";
import { OrbitControls } from "./OrbitControls.js";
const $ = (id) => document.getElementById(id);
const controls = [
  ...document.querySelectorAll(
    "main button,main input,main select,main textarea",
  ),
];
controls.forEach((control) => {
  control.disabled = true;
});
const copy = (value) => JSON.parse(JSON.stringify(value));
const labels = {
  rightArm: "Sağ kol",
  leftArm: "Sol kol",
  neck: "Boyun",
  headLeftRight: "Kafa sağ / sol",
  eyeLeft: "Sol göz",
  eyeRight: "Sağ göz",
};
let rig,
  model,
  motion,
  selected = 0,
  dirty = false,
  playing = false,
  playStarted = 0,
  currentTime = 0,
  state = null;
let needsRender = true,
  lastRender = 0;
let joint = "headLeftRight",
  pose = {},
  records = [],
  socket,
  reconnectTimer;
const embedded = window.parent !== window;
const native = !!window.ReactNativeWebView;
const scene = new THREE.Scene();
const camera = new THREE.PerspectiveCamera(38, 1, 0.005, 10);
camera.position.set(0.5, 0.36, -0.65);
let renderer;
try {
  renderer = new THREE.WebGLRenderer({ antialias: true, alpha: true });
} catch (error) {
  $("notice").textContent =
    "3D görünüm başlatılamadı. WebGL destekli bir tarayıcı kullanın.";
  throw error;
}
renderer.setPixelRatio(Math.min(devicePixelRatio, 2));
$("viewport").append(renderer.domElement);
const orbit = new OrbitControls(camera, renderer.domElement);
orbit.target.set(0, 0.19, 0);
orbit.minDistance = 0.25;
orbit.maxDistance = 2;
orbit.enablePan = false;
orbit.update();
orbit.addEventListener("change", () => {
  needsRender = true;
});
scene.add(new THREE.HemisphereLight(0xffffff, 0x69717c, 1.8));
const light = new THREE.DirectionalLight(0xffefd2, 2);
light.position.set(-1, 2, -2);
scene.add(light);
const grid = new THREE.GridHelper(1, 20, 0x4d6b7d, 0x304454);
scene.add(grid);
const ring = new THREE.Mesh(
  new THREE.TorusGeometry(0.06, 0.003, 8, 80),
  new THREE.MeshBasicMaterial({ color: 0x62ead5, depthTest: false }),
);
ring.renderOrder = 100;
scene.add(ring);
const ray = new THREE.Raycaster();
const pointer = new THREE.Vector2();
let drag = null;
function notice(text) {
  $("notice").textContent = text;
}
async function api(path, options) {
  const response = await fetch(path, options);
  if (!response.ok) throw new Error(await response.text());
  return response.json();
}
function send(command) {
  if (native)
    window.ReactNativeWebView.postMessage(
      JSON.stringify({ type: "mimicCommand", command }),
    );
  else if (embedded)
    window.parent.postMessage(
      { type: "mimicCommand", command },
      location.origin,
    );
  else if (socket?.readyState === WebSocket.OPEN)
    socket.send(JSON.stringify(command));
}
function updateState(value) {
  state = value;
  const ready =
    state?.owner && state.mode === "remote" && state.appliedMode === "remote";
  $("run").disabled = !ready || dirty || !motion;
  $("claim").disabled = !!state?.owner;
  const m = state?.mimic;
  const phase = {
    playing: "Çalışıyor",
    completed: "Tamamlandı",
    stopped: "Durduruldu",
    manual_override: "Elle kontrol",
    idle: "Hazır",
  };
  $("robot-status").textContent = state
    ? `${state.owner ? "Kontrol sizde" : "İzleyici"} · ${state.mode === "remote" ? "Kumanda modu" : "YZ / araç modu"}${m?.id ? " · " + (phase[m.state] || m.state) + " " + (m.elapsed_ms / 1000).toFixed(2) + " s" : ""}`
    : "Bağlantı kesildi";
}
window.kufibotMimicState = updateState;
window.addEventListener("message", (event) => {
  if (event.origin === location.origin && event.source === parent) {
    if (event.data?.type === "mimicState") updateState(event.data.state);
    if (event.data?.type === "mimicError") notice(event.data.message);
  }
});
function connect() {
  socket = new WebSocket(
    `${location.protocol === "https:" ? "wss" : "ws"}://${location.host}/control`,
  );
  socket.onmessage = (e) => {
    const data = JSON.parse(e.data);
    if (data.type === "state") updateState(data);
    if (data.type === "error") notice(data.message);
  };
  socket.onclose = () => {
    updateState(null);
    reconnectTimer = setTimeout(connect, 2000);
  };
}
if (!embedded && !native) connect();
const heartbeat = setInterval(() => {
  if (!embedded && !native && state?.owner) send({ type: "heartbeat" });
}, 400);
window.addEventListener("pagehide", () => {
  send({ type: "stopMimic" });
  clearInterval(heartbeat);
  clearTimeout(reconnectTimer);
  if (socket) {
    socket.onclose = null;
    socket.close();
  }
});
document.addEventListener("visibilitychange", () => {
  if (document.hidden) {
    playing = false;
    send({ type: "stopMimic" });
  }
});
function changed() {
  dirty = true;
  playing = false;
  $("saved").textContent = "Kaydedilmedi";
  updateState(state);
}
function showPose(value) {
  needsRender = true;
  pose = { ...value };
  if (!model) return;
  for (const [name, spec] of Object.entries(rig.joints)) {
    const angle = Math.max(
      spec.limits_deg[0],
      Math.min(spec.limits_deg[1], pose[name]),
    );
    model
      .getObjectByName(name)
      .quaternion.setFromAxisAngle(
        new THREE.Vector3(...spec.axis),
        THREE.MathUtils.degToRad((angle - spec.assembly_deg) * spec.multiplier),
      );
  }
  model.updateMatrixWorld(true);
  const node = model.getObjectByName(joint);
  node.getWorldPosition(ring.position);
  const axis = new THREE.Vector3(...rig.joints[joint].axis).applyQuaternion(
    node.getWorldQuaternion(new THREE.Quaternion()),
  );
  ring.quaternion.setFromUnitVectors(new THREE.Vector3(0, 0, 1), axis);
  $("angle").value = pose[joint].toFixed(1);
  $("angle-range").value = pose[joint];
}
function chooseJoint(name) {
  joint = name;
  $("joint").value = name;
  const [low, high] = rig.joints[name].limits_deg;
  for (const id of ["angle", "angle-range"]) {
    $(id).min = low;
    $(id).max = high;
  }
  showPose(pose);
}
function setAngle(value) {
  if (!motion || !Number.isFinite(value)) return;
  const [low, high] = rig.joints[joint].limits_deg;
  motion.keyframes[selected].joints[joint] = Math.max(
    low,
    Math.min(high, value),
  );
  changed();
  selectFrame(selected);
}

function scrub(time) {
  currentTime = time;
  $("scrub").value = time;
  $("time-label").textContent = (time / 1000).toFixed(3) + " s";
  showPose(evaluateMotion(motion, time));
}
function frames() {
  const list = $("frames");
  list.replaceChildren();
  motion.keyframes.forEach((frame, i) => {
    const button = document.createElement("button");
    button.textContent = (frame.time_ms / 1000).toFixed(3) + " s";
    button.className = i === selected ? "selected" : "";
    button.onclick = () => {
      playing = false;
      selectFrame(i);
    };
    list.append(button);
  });
  $("duration").value = motion.duration_ms / 1000;
  $("scrub").max = motion.duration_ms;
}
function selectFrame(i) {
  selected = i;
  frames();
  $("frame-time").value = motion.keyframes[i].time_ms / 1000;
  scrub(motion.keyframes[i].time_ms);
}
function openMotion(value) {
  motion = copy(value);
  dirty = false;
  playing = false;
  $("name").value = motion.name;
  $("description").value = motion.description;
  $("interpolation").value = motion.interpolation;
  $("saved").textContent = "Sürüm " + motion.revision;
  selectFrame(0);
  updateState(state);
}
async function refresh() {
  records = await api("/api/mimics");
  $("library").replaceChildren();
  for (const record of records) {
    const option = document.createElement("option");
    option.value = record.id;
    option.textContent = record.name;
    $("library").append(option);
  }
  if (motion && records.some((r) => r.id === motion.id))
    $("library").value = motion.id;
}
function discard() {
  return !dirty || confirm("Kaydedilmemiş değişiklikler silinsin mi?");
}
function addFrame(duplicate) {
  let time = duplicate
    ? motion.keyframes[selected].time_ms + 500
    : Math.round(currentTime);
  while (motion.keyframes.some((f) => f.time_ms === time)) time += 500;
  if (time > 300000) return notice("En fazla 300 saniye");
  const frame = {
    time_ms: time,
    joints: copy(duplicate ? motion.keyframes[selected].joints : pose),
  };
  motion.keyframes.push(frame);
  motion.keyframes.sort((a, b) => a.time_ms - b.time_ms);
  motion.duration_ms = Math.max(motion.duration_ms, time);
  changed();
  selectFrame(motion.keyframes.indexOf(frame));
}
$("joint").onchange = () => chooseJoint($("joint").value);
$("angle").onchange = () => setAngle(Number($("angle").value));
$("angle-range").oninput = () => setAngle(Number($("angle-range").value));
$("name").oninput = () => {
  motion.name = $("name").value;
  changed();
};
$("description").oninput = () => {
  motion.description = $("description").value;
  changed();
};
$("interpolation").onchange = () => {
  motion.interpolation = $("interpolation").value;
  changed();
};
$("new").onclick = () => {
  if (!discard()) return;
  openMotion({
    id:
      "mimic_" +
      Date.now().toString(36) +
      "_" +
      Math.random().toString(36).slice(2, 8),
    name: "Yeni mimik",
    description: "",
    revision: 0,
    duration_ms: 1000,
    interpolation: "linear",
    keyframes: [
      {
        time_ms: 0,
        joints: Object.fromEntries(
          Object.entries(rig.joints).map(([name, s]) => [name, s.neutral_deg]),
        ),
      },
    ],
  });
  changed();
};
$("load").onclick = () => {
  if (discard()) openMotion(records.find((r) => r.id === $("library").value));
};
$("refresh").onclick = () => refresh().catch((e) => notice(e.message));
$("save").onclick = async () => {
  try {
    const saved = await api("/api/mimics/" + motion.id, {
      method: "PUT",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify(motion),
    });
    openMotion(saved);
    await refresh();
    notice("Mimik kaydedildi. Web, mobil ve YZ kitaplığı güncellendi.");
  } catch (e) {
    notice(e.message);
  }
};
$("add").onclick = () => addFrame(false);
$("duplicate").onclick = () => addFrame(true);
$("delete").onclick = () => {
  if (selected === 0) return notice("Sıfırıncı adım silinemez");
  motion.keyframes.splice(selected, 1);
  changed();
  selectFrame(selected - 1);
};
$("frame-time").onchange = () => {
  const time = Math.round(Number($("frame-time").value) * 1000);
  if (
    !Number.isFinite(time) ||
    time < 0 ||
    time > 300000 ||
    (selected === 0 && time !== 0) ||
    (selected !== 0 && time === 0) ||
    motion.keyframes.some((f, i) => i !== selected && f.time_ms === time)
  ) {
    notice("Geçerli ve benzersiz bir zaman girin. İlk adım 0 s olmalı.");
    selectFrame(selected);
    return;
  }
  const frame = motion.keyframes[selected];
  frame.time_ms = time;
  motion.keyframes.sort((a, b) => a.time_ms - b.time_ms);
  motion.duration_ms = Math.max(motion.duration_ms, time);
  changed();
  selectFrame(motion.keyframes.indexOf(frame));
};
$("duration").onchange = () => {
  const time = Math.round(Number($("duration").value) * 1000);
  if (
    !Number.isFinite(time) ||
    time < motion.keyframes.at(-1).time_ms ||
    time > 300000
  ) {
    notice("Süre son adımdan kısa veya 300 saniyeden uzun olamaz");
    frames();
    return;
  }
  motion.duration_ms = time;
  changed();
  frames();
};
$("scrub").oninput = () => {
  playing = false;
  scrub(Number($("scrub").value));
};
$("preview").onclick = () => {
  if (currentTime >= motion.duration_ms) currentTime = 0;
  playStarted = performance.now() - currentTime;
  playing = true;
};
$("pause").onclick = () => {
  playing = false;
};
$("claim").onclick = () => send({ type: "claim" });
$("remote").onclick = () => send({ type: "mode", mode: "remote" });
$("run").onclick = () =>
  send({ type: "playMimic", id: motion.id, revision: motion.revision });
$("stop").onclick = () => {
  playing = false;
  send({ type: "stop" });
};
$("close").onclick = () => {
  if (!discard()) return;
  send({ type: "stopMimic" });
  if (native)
    window.ReactNativeWebView.postMessage(
      JSON.stringify({ type: "mimicClose" }),
    );
  else if (embedded)
    parent.postMessage({ type: "mimicClose" }, location.origin);
  else location.href = "/";
};
function hit(event) {
  const rect = renderer.domElement.getBoundingClientRect();
  pointer.set(
    ((event.clientX - rect.left) / rect.width) * 2 - 1,
    (-(event.clientY - rect.top) / rect.height) * 2 + 1,
  );
  ray.setFromCamera(pointer, camera);
}
renderer.domElement.addEventListener(
  "pointerdown",
  (event) => {
    if (!model) return;
    hit(event);
    if (ray.intersectObject(ring).length) {
      drag = {
        x: event.clientX,
        y: event.clientY,
        angle: motion.keyframes[selected].joints[joint],
      };
      orbit.enabled = false;
      renderer.domElement.setPointerCapture(event.pointerId);
      event.stopImmediatePropagation();
      return;
    }
    const intersections = ray.intersectObject(model, true);
    if (intersections.length) {
      let node = intersections[0].object;
      while (node && !rig.joints[node.name]) node = node.parent;
      if (node) chooseJoint(node.name);
    }
  },
  true,
);
renderer.domElement.addEventListener("pointermove", (event) => {
  if (drag)
    setAngle(
      drag.angle + (event.clientX - drag.x - event.clientY + drag.y) * 0.4,
    );
});
for (const event of ["pointerup", "pointercancel", "lostpointercapture"])
  renderer.domElement.addEventListener(event, () => {
    drag = null;
    orbit.enabled = true;
  });
const resize = new ResizeObserver(() => {
  const box = $("viewport");
  renderer.setSize(box.clientWidth, box.clientHeight);
  camera.aspect = box.clientWidth / box.clientHeight;
  camera.updateProjectionMatrix();
  needsRender = true;
});
resize.observe($("viewport"));
renderer.setAnimationLoop(() => {
  const now = performance.now();
  if (playing && motion && now - lastRender >= 33) {
    const time = Math.min(motion.duration_ms, performance.now() - playStarted);
    scrub(time);
    if (time >= motion.duration_ms) playing = false;
  }
  if (needsRender) {
    renderer.render(scene, camera);
    needsRender = false;
    lastRender = now;
  }
});
try {
  rig = await api("/model/rig.json");
  const gltf = await new GLTFLoader().loadAsync("/model/robot.glb");
  model = gltf.scene;
  scene.add(model);
  for (const [name, label] of Object.entries(labels)) {
    const option = document.createElement("option");
    option.value = name;
    option.textContent = label;
    $("joint").append(option);
  }
  await refresh();
  controls.forEach((control) => {
    control.disabled = false;
  });
  if (records.length) openMotion(records[0]);
  else $("new").click();
  chooseJoint(joint);
  notice("Model hazır · Düzenleme yalnızca önizlemeyi hareket ettirir.");
} catch (error) {
  notice("Editör yüklenemedi: " + error.message);
}
