import { RobotConnection } from './connection.js';

const $ = id => document.getElementById(id);
const link = new RobotConnection();
const menu = $('menu');
const calibrationModal = $('calibration-modal');
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
    let x = dx / scale;
    let y = dy / scale;
    // The drive control has four full-power sectors. Select the dominant
    // axis so diagonal touches always resolve to one unambiguous direction.
    if (this.part === 'drive') [x, y] = Math.abs(x) >= Math.abs(y)
      ? [Math.sign(x), 0] : [0, Math.sign(y)];
    this.position(x, y);
    link.input(this.part, x, y);
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

function normalizeDegrees(value) {
  return (value % 360 + 360) % 360;
}

function renderDirection(state) {
  const heading = state?.sensors.heading;
  const headAngle = state?.joints.headLeftRight;
  const direction = Number.isFinite(heading) && Number.isFinite(headAngle)
    ? normalizeDegrees(heading + 90 - headAngle) : null;
  $('direction-arrow').style.transform = direction === null ? 'rotate(0deg)' : `rotate(${direction}deg)`;
  $('direction-value').textContent = direction === null ? '—°' : `${Math.round(direction)}°`;
  $('head-direction').setAttribute('aria-label', direction === null
    ? 'Kafanın pusulaya göre baktığı yön bilinmiyor'
    : `Kafanın pusulaya göre baktığı yön ${Math.round(direction)} derece`);
}

function renderCalibration(state) {
  const calibration = state?.calibration;
  const active = !!calibration?.active;
  const message = calibration?.message || 'Pusula sensörü bekleniyor';
  const progress = active && Number.isInteger(calibration.samples) && Number.isInteger(calibration.target)
    ? ` (${calibration.samples}/${calibration.target})` : '';
  $('calibration-info').textContent = `${message}${progress}`;
  $('calibration-modal-message').textContent = `${message}${progress}`;
  for (const [name, value] of Object.entries({
    'raw-x': calibration?.raw?.x, 'raw-y': calibration?.raw?.y,
    'min-x': calibration?.minimum?.x, 'max-x': calibration?.maximum?.x,
    'min-y': calibration?.minimum?.y, 'max-y': calibration?.maximum?.y,
  })) $(`calibration-${name}`).textContent = Number.isFinite(value) ? Math.round(value) : '—';
  $('calibrate-compass').disabled = !state?.owner || state.mode !== 'remote' ||
    state.appliedMode !== 'remote' || active;
}

function renderAiWorkflow(state) {
  const input = $('ai-trigger-uuid');
  if (document.activeElement !== input && state?.aiTriggerUuid) input.value = state.aiTriggerUuid;
  const valid = /^[0-9a-f]{8}-[0-9a-f]{4}-[1-5][0-9a-f]{3}-[89ab][0-9a-f]{3}-[0-9a-f]{12}$/i.test(input.value.trim());
  $('start-ai-workflow').disabled = !state?.owner || !valid;
  $('ai-trigger-info').textContent = state?.aiTriggerUuid
    ? `Seçili UUID: ${state.aiTriggerUuid}` : 'YZ iş akışı UUID bekleniyor';
}

let aiDraft = null;
let aiDirty = false;
let aiSaved = null;
function renderAiSettings(state) {
  const config = state?.aiConfig;
  if (!aiDirty && config?.settings) aiDraft = {...config.settings};
  if (aiSaved && JSON.stringify(config?.settings) === aiSaved) {
    aiDirty = false; aiSaved = null;
  }
  const draft = aiDraft || {provider: 'verasist', language: 'tr', stt: '', llm: '', tts: '', system_prompt: ''};
  const local = draft.provider === 'local';
  $('ai-provider').value = draft.provider;
  $('ai-provider').disabled = !state?.owner || !config;
  $('local-model-settings').hidden = !local;
  $('verasist-settings').hidden = local;
  const models = config?.models || [];
  const languages = [...new Set(models.filter(m => m.available).flatMap(m => m.languages))].sort();
  function options(id, values, current) {
    const select = $(id);
    select.replaceChildren(new Option('Seçin', ''));
    for (const [value, label] of values) select.add(new Option(label, value));
    select.value = current;
    select.disabled = !state?.owner;
  }
  options('ai-language', languages.map(l => [l, l]), draft.language);
  for (const kind of ['stt', 'llm', 'tts']) {
    options(`ai-${kind}`, models.filter(m => m.kind === kind && m.available && m.languages.includes(draft.language))
      .map(m => [m.id, m.label || m.id]), draft[kind]);
  }
  const prompt = $('ai-system-prompt');
  if (document.activeElement !== prompt) prompt.value = draft.system_prompt || '';
  prompt.disabled = !state?.owner;
  const valid = !local || ['stt', 'llm', 'tts'].every(kind => models.some(m =>
    m.kind === kind && m.id === draft[kind] && m.available && m.languages.includes(draft.language)));
  $('save-ai-settings').disabled = !state?.owner || !config || !valid;
  const saved = config && ['provider', 'language', 'stt', 'llm', 'tts', 'system_prompt'].every(k => draft[k] === config.settings[k]);
  $('ai-settings-status').textContent = [config?.error, state?.voiceStatus?.detail,
    state?.voiceStatus?.state, aiSaved ? 'Uygulanması bekleniyor…' : '',
    local && !valid ? 'Bu dil için robotta STT, LLM ve TTS modellerini kurup seçin.' : '',
    !config ? 'Sesli ajan ayarları bekleniyor' : !saved ? 'Önce ayarları kaydedin.' : state.mode !== 'ai' ? 'Ayarlar kayıtlı. Ana ekrandan YZ modu seçildiğinde ajan başlar.' : ''].filter(Boolean).join(' · ');
}
for (const key of ['provider', 'language', 'stt', 'llm', 'tts']) {
  $(`ai-${key}`).addEventListener('change', event => {
    aiDraft = {...(aiDraft || link.state?.aiConfig?.settings), [key]: event.target.value};
    if (key === 'language') for (const kind of ['stt', 'llm', 'tts']) aiDraft[kind] = '';
    aiDirty = true; aiSaved = null;
    renderAiSettings(link.state);
  });
}
$('ai-system-prompt').addEventListener('input', event => {
  aiDraft = {...(aiDraft || link.state?.aiConfig?.settings), system_prompt: event.target.value};
  aiDirty = true; aiSaved = null;
  renderAiSettings(link.state);
});
$('save-ai-settings').addEventListener('click', () => {
  if (link.send({type: 'setAiSettings', settings: aiDraft})) aiSaved = JSON.stringify(aiDraft);
  renderAiSettings(link.state);
});

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
  $('stop').disabled = !state?.owner;
  const nav = state?.navigation;
  const navLabels = {disabled: 'Kapalı', idle: 'Komut bekleniyor', aligning: 'Kafa hizalanıyor',
    scanning: 'Taranıyor', advancing: 'İlerleniyor', turning: 'Dönülüyor',
    waiting_llm: 'LLM bekleniyor', blocked: 'Engellendi', completed: 'Tamamlandı'};
  const navToggle = $('navigation-toggle');
  navToggle.hidden = state?.mode !== 'ai';
  navToggle.disabled = !state?.owner || state?.aiConfig?.settings?.provider !== 'verasist' ||
    state?.voiceStatus?.state !== 'connected' || state?.appliedMode !== 'ai' || !nav;
  navToggle.setAttribute('aria-pressed', String(!!nav?.enabled));
  navToggle.textContent = nav?.enabled ? 'Serbest gezinme · Açık' : 'Serbest gezinme';
  $('navigation-status').hidden = state?.mode !== 'ai';
  $('navigation-status').textContent = state?.aiConfig?.settings?.provider !== 'verasist'
    ? 'Gezinme yalnızca Verasist ile kullanılabilir'
    : !nav ? 'Gezinme düğümü bekleniyor'
    : `${navLabels[nav.state] || nav.state}${!nav.calibrated ? ' · Hareket kalibrasyonu gerekli' : ''}`;
  drive.enable(enabled && !!state?.driveAvailable);
  head.enable(enabled);
  $('drive-label').textContent = state?.driveAvailable ? 'HAREKET · TAM GÜÇ' : 'HAREKET · MOTOR YOK';
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
    : state.mode === 'ai' ? 'Gezinme için serbest gezinmeyi açıp sesli görev verin'
    : 'Joystick bırakıldığında hareket durur';
  renderDirection(state);
  renderCalibration(state);
  renderAiWorkflow(state);
  renderAiSettings(state);
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
link.addEventListener('frame', () => { imageLoaded = false; camera(); });
link.addEventListener('stream', ({ detail }) => {
  $('camera').srcObject = detail;
  imageLoaded = false;
  camera();
});
$('camera').addEventListener('loadeddata', () => { imageLoaded = true; link.frameReceived(); camera(); });
$('camera').addEventListener('timeupdate', () => {
  if ($('camera').readyState >= 2) { imageLoaded = true; link.frameReceived(); camera(); }
});
$('camera').addEventListener('error', () => { imageLoaded = false; camera(); });

for (const mode of ['remote', 'ai']) $(`mode-${mode}`).addEventListener('click', () => link.setMode(mode));
$('stop').addEventListener('click', () => link.stop());
$('navigation-toggle').addEventListener('click', () => {
  link.send({type: 'setNavigationEnabled', enabled: !link.state?.navigation?.enabled});
});
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
$('ai-trigger-uuid').addEventListener('input', () => renderAiWorkflow(link.state));
$('start-ai-workflow').addEventListener('click', () => {
  link.startAiWorkflow($('ai-trigger-uuid').value.trim());
  menu.close();
});
$('calibrate-compass').addEventListener('click', () => {
  link.calibrateCompass();
  menu.close();
  calibrationModal.showModal();
});
$('calibration-close').addEventListener('click', () => calibrationModal.close());
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
  if (!drive.enabled || drive.pointer !== null) return;
  const held = (...codes) => codes.some(code => keys.has(code));
  let x = Number(held('KeyD', 'ArrowRight')) - Number(held('KeyA', 'ArrowLeft'));
  let y = Number(held('KeyS', 'ArrowDown')) - Number(held('KeyW', 'ArrowUp'));
  if (x) y = 0; // Four-way drive: turning takes priority.
  drive.position(x, y);
  link.input('drive', x, y);
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
