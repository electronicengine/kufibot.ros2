import { RobotConnection } from './connection.js';

const $ = id => document.getElementById(id);
const link = new RobotConnection();
const menu = $('menu');
let windowActive = true;
let imageLoaded = false;
const keys = new Set();
const canControl = () => link.ready && !menu.open && windowActive;

class Joystick {
  constructor(part) {
    this.part = part;
    this.element = $(`${part}-stick`);
    this.knob = this.element.querySelector('.knob');
    this.pointer = null;
    this.enabled = false;
    this.element.addEventListener('pointerdown', event => {
      if (!this.enabled || !canControl() || this.pointer !== null || event.button !== 0) return;
      event.preventDefault();
      this.pointer = event.pointerId;
      this.element.setPointerCapture(event.pointerId);
      this.element.classList.add('dragging');
      this.move(event);
    });
    this.element.addEventListener('pointermove', event => {
      if (this.pointer === event.pointerId) this.move(event);
    });
    for (const name of ['pointerup', 'pointercancel', 'lostpointercapture']) {
      this.element.addEventListener(name, event => {
        if (this.pointer === event.pointerId) {
          this.reset();
          link.input(this.part, 0, 0);
        }
      });
    }
    this.element.addEventListener('contextmenu', event => event.preventDefault());
  }
  move(event) {
    if (!this.enabled || !canControl()) return;
    const rect = this.element.getBoundingClientRect();
    const radius = rect.width * .32;
    const dx = event.clientX - rect.left - rect.width / 2;
    const dy = event.clientY - rect.top - rect.height / 2;
    const scale = Math.max(radius, Math.hypot(dx, dy));
    this.position(dx / scale, dy / scale);
    link.input(this.part, dx / scale, dy / scale);
  }
  position(x, y) {
    const radius = this.element.clientWidth * .32;
    this.knob.style.transform = `translate(${x * radius}px, ${y * radius}px)`;
  }
  reset() {
    const id = this.pointer;
    this.pointer = null;
    if (id !== null && this.element.hasPointerCapture(id)) this.element.releasePointerCapture(id);
    this.element.classList.remove('dragging');
    this.position(0, 0);
  }
  enable(enabled) {
    if (this.enabled && !enabled) this.reset();
    this.enabled = enabled;
    this.element.setAttribute('aria-disabled', String(!enabled));
  }
}

const drive = new Joystick('drive');
const head = new Joystick('head');
const reset = () => { keys.clear(); drive.reset(); head.reset(); };
link.addEventListener('reset', reset);

function camera() {
  const live = imageLoaded && !!link.state?.camera && link.frameVisible;
  $('camera').hidden = !live;
  $('camera-placeholder').hidden = live;
  $('live-dot').classList.toggle('active', live);
  $('camera-tag').textContent = live ? 'CANLI · ROBOT KAMERASI' : 'KAMERA BEKLENİYOR';
  $('camera-message').textContent = link.state ? 'Kamera görüntüsü bekleniyor' : 'Robot bağlantısı bekleniyor';
}

function render() {
  const state = link.state;
  const enabled = canControl();
  for (const [name, unit] of [['voltage', 'V'], ['current', 'A'], ['heading', '°'], ['distance', 'm']]) {
    const value = state?.sensors[name];
    $(name).textContent = Number.isFinite(value) ? `${value.toFixed(1)} ${unit}` : `— ${unit}`;
  }
  for (const mode of ['remote', 'ai']) {
    const button = $(`mode-${mode}`);
    button.disabled = !state?.owner || !!link.pendingMode;
    button.setAttribute('aria-pressed', String(state?.mode === mode && state?.appliedMode === mode));
  }
  $('mode-notice').hidden = !state || (state.mode === state.appliedMode && !link.pendingMode);
  $('claim').hidden = !state || state.owner;
  $('stop').disabled = !enabled;
  drive.enable(enabled && !!state?.driveAvailable);
  head.enable(enabled);
  $('drive-label').textContent = state?.driveAvailable ? 'HAREKET' : 'HAREKET · MOTOR YOK';
  for (const name of ['leftArm', 'rightArm']) {
    const input = $(name);
    input.disabled = !enabled;
    const value = state?.joints[name];
    if (input.dataset.editing !== 'true' && document.activeElement !== input) {
      if (Number.isFinite(value)) input.value = value;
      $(`${name}-value`).textContent = Number.isFinite(value) ? `${Math.round(value)} °` : '— °';
    }
  }
  for (const name of ['eyeLeft', 'eyeRight']) {
    $(name).disabled = !enabled;
    const value = state?.joints[name];
    $(name).checked = Number.isFinite(value) && (name === 'eyeLeft' ? value < 20 : value > 160);
  }
  $('control-hint').textContent = !state ? 'Kontrol bağlantısı bekleniyor'
    : !state.owner ? 'Başka bir cihaz kontrol ediyor'
    : state.mode === 'ai' ? 'YZ hareket kontrolü etkin'
    : 'Joystick bırakıldığında hareket durur';
  camera();
}

link.addEventListener('state', render);
link.addEventListener('connection', ({ detail }) => {
  $('connection').textContent = detail.text;
  $('connection-dot').classList.toggle('active', detail.connected);
});
link.addEventListener('error', ({ detail }) => {
  $('error').textContent = detail;
  $('error').hidden = !detail;
});
link.addEventListener('frame', ({ detail }) => {
  if (detail) $('camera').src = detail;
  else { imageLoaded = false; $('camera').removeAttribute('src'); camera(); }
});
$('camera').addEventListener('load', () => { imageLoaded = true; camera(); });
$('camera').addEventListener('error', () => { imageLoaded = false; camera(); });

for (const mode of ['remote', 'ai']) $(`mode-${mode}`).addEventListener('click', () => link.setMode(mode));
$('stop').addEventListener('click', () => link.stop());
$('claim').addEventListener('click', () => link.claim());
for (const name of ['leftArm', 'rightArm']) {
  $(name).addEventListener('input', () => {
    $(name).dataset.editing = 'true';
    $(`${name}-value`).textContent = `${$(name).value} °`;
  });
  $(name).addEventListener('change', () => {
    if (canControl()) link.joint(name, Number($(name).value));
    $(name).dataset.editing = 'false';
  });
  $(name).addEventListener('blur', () => { $(name).dataset.editing = 'false'; });
}
$('eyeLeft').addEventListener('change', () => link.joint('eyeLeft', $('eyeLeft').checked ? 0 : 30));
$('eyeRight').addEventListener('change', () => link.joint('eyeRight', $('eyeRight').checked ? 170 : 150));

$('menu-open').addEventListener('click', () => { link.stop(); menu.showModal(); render(); });
$('menu-close').addEventListener('click', () => menu.close());
menu.addEventListener('close', render);
$('reconnect').addEventListener('click', () => { menu.close(); link.connect(); });
$('endpoint').textContent = location.host;
$('menu-address').textContent = location.origin;
$('fullscreen').hidden = !document.fullscreenEnabled;
$('fullscreen').addEventListener('click', async () => {
  link.stop();
  try {
    if (document.fullscreenElement) await document.exitFullscreen();
    else await document.documentElement.requestFullscreen();
  } catch { link.emit('error', 'Tarayıcı tam ekrana geçemedi'); }
});
document.addEventListener('fullscreenchange', () => {
  $('fullscreen').textContent = document.fullscreenElement ? '⛶ Tam ekrandan çık' : '⛶ Tam ekran';
});

function keyboard() {
  for (const [part, stick, left, right, up, down] of [
    ['drive', drive, 'KeyA', 'KeyD', 'KeyW', 'KeyS'],
    ['head', head, 'ArrowLeft', 'ArrowRight', 'ArrowUp', 'ArrowDown'],
  ]) {
    if (!stick.enabled || stick.pointer !== null) continue;
    const x = Number(keys.has(right)) - Number(keys.has(left));
    const y = Number(keys.has(down)) - Number(keys.has(up));
    const scale = Math.max(1, Math.hypot(x, y));
    stick.position(x / scale, y / scale);
    link.input(part, x / scale, y / scale);
  }
}
const movementKeys = ['KeyW', 'KeyA', 'KeyS', 'KeyD', 'ArrowUp', 'ArrowLeft', 'ArrowDown', 'ArrowRight'];
window.addEventListener('keydown', event => {
  if (event.target.closest('input, dialog') || !canControl()) return;
  if (event.code === 'Space' && event.target.closest('button')) return;
  if (event.code === 'Space') { event.preventDefault(); link.stop(); return; }
  if (!movementKeys.includes(event.code)) return;
  event.preventDefault();
  if (event.repeat) return;
  keys.add(event.code);
  keyboard();
});
window.addEventListener('keyup', event => {
  if (!keys.has(event.code)) return;
  keys.delete(event.code);
  event.preventDefault();
  keyboard();
});
window.addEventListener('blur', () => { windowActive = false; link.stop(); render(); });
window.addEventListener('focus', () => { windowActive = true; render(); });
window.addEventListener('resize', () => link.stop());
render();
link.connect();
