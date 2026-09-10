import { RobotConnection } from './connection.js';

const $ = id => document.getElementById(id);
const link = new RobotConnection();
const menu = $('menu');
const calibrationModal = $('calibration-modal');
let windowActive = true;
let imageLoaded = false;
const keys = new Set();
const canControl = () => link.ready && !menu.open && !$('mimics-modal').open && windowActive;

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
  $('body-direction').style.transform = `rotate(${Number.isFinite(heading) ? heading : 0}deg)`;
  $('body-direction').hidden = !Number.isFinite(heading);
  let reference = $('head-reference');
  if (!reference) {
    reference = document.createElement('button'); reference.id = 'head-reference';
    reference.style.cssText = 'font-size:11px;background:#17233dcc;padding:6px;border-radius:6px';
    $('head-direction').after(reference);
    reference.addEventListener('click', () => link.joint('headLeftRight', 90));
  }
  reference.disabled = !canControl();
  reference.textContent = `Gövde ${Number.isFinite(heading) ? Math.round(heading) : '—'}° · Kafa ${Number.isFinite(headAngle) ? Math.round(90-headAngle) : '—'}° · Öne bak`;
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

function drawDistanceMap(canvas, mapping) {
  const ratio = devicePixelRatio || 1;
  const width = Math.max(1, Math.round(canvas.clientWidth * ratio));
  const height = Math.max(1, Math.round(canvas.clientHeight * ratio));
  if (canvas.width !== width || canvas.height !== height) { canvas.width = width; canvas.height = height; }
  const ctx = canvas.getContext('2d');
  ctx.clearRect(0, 0, width, height);
  ctx.fillStyle = '#101824d9'; ctx.fillRect(0, 0, width, height);
  const points = mapping?.obstacle_points || [];
  const robot = mapping?.robot_pose || [0, 0];
  const extent = Math.max(2, ...points.flatMap(p => [Math.abs(p[0]), Math.abs(p[1])]),
                          Math.abs(robot[0]), Math.abs(robot[1])) * 1.15;
  const scale = Math.min(width, height) / (2 * extent);
  const origin = [width / 2, height / 2];
  const point = p => [origin[0] + p[0] * scale, origin[1] - p[1] * scale];
  ctx.strokeStyle = '#78919e55'; ctx.lineWidth = Math.max(1, ratio);
  for (let metre = 1; metre < extent; metre++) {
    ctx.beginPath(); ctx.arc(...origin, metre * scale, 0, Math.PI * 2); ctx.stroke();
  }
  ctx.strokeStyle = '#ff9981'; ctx.fillStyle = '#ff695f30';
  ctx.lineWidth = 1.5 * ratio; ctx.lineJoin = 'round';
  for (const path of mapping?.boundary_paths || []) {
    if (path.length < 2) continue;
    ctx.beginPath(); ctx.moveTo(...point(path[0]));
    for (const vertex of path.slice(1)) ctx.lineTo(...point(vertex));
    ctx.stroke();
  }
  const p = point(robot), heading = (mapping?.robot_heading_deg || 0) * Math.PI / 180;
  ctx.fillStyle = '#ffd66e'; ctx.beginPath(); ctx.arc(...p, 4 * ratio, 0, Math.PI * 2); ctx.fill();
  ctx.strokeStyle = '#ffd66e'; ctx.lineWidth = 2 * ratio; ctx.beginPath();
  ctx.moveTo(...p); ctx.lineTo(p[0] + Math.sin(heading) * 15 * ratio, p[1] - Math.cos(heading) * 15 * ratio); ctx.stroke();
  ctx.fillStyle = '#67d9ed'; ctx.beginPath(); ctx.arc(...origin, 3 * ratio, 0, Math.PI * 2); ctx.fill();
  const barMetres = Math.max(1, Math.ceil(50 * ratio / scale));
  ctx.strokeStyle = '#ffffff'; ctx.lineWidth = 2*ratio;
  ctx.beginPath(); ctx.moveTo(10*ratio, height-15*ratio);
  ctx.lineTo(10*ratio+barMetres*scale, height-15*ratio); ctx.stroke();
  ctx.font = `${10*ratio}px sans-serif`; ctx.fillStyle = '#ffffff';
  ctx.fillText(`${barMetres} m`, 10*ratio, height-20*ratio);
  const nearest = points.length ? Math.min(...points.map(q => Math.hypot(q[0]-robot[0], q[1]-robot[1]))) : null;
  ctx.fillText(nearest === null ? 'Sınır ölçümü bekleniyor' : `En yakın kayıtlı sınır ≈ ${nearest.toFixed(2)} m`, 8*ratio, 13*ratio);
  const liveRange = link.state?.sensors.distance;
  ctx.fillText(Number.isFinite(liveRange) ? `Lidar baktığı yön: ${liveRange.toFixed(2)} m` : 'Lidar: — m', 8*ratio, 26*ratio);
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
  for (const mode of ['remote', 'ai', 'tools']) {
    const button = $(`mode-${mode}`);
    button.disabled = !state?.owner || !!link.pendingMode;
    button.setAttribute('aria-pressed', String(state?.mode === mode && state?.appliedMode === mode));
  }
  $('mode-notice').hidden = !state || (state.mode === state.appliedMode && !link.pendingMode);
  // The bridge waits for the servo mode acknowledgement itself. Keeping this
  // enabled after the user selects the mode avoids a dead-looking modal.
  $('tool-call').disabled = state?.mode !== 'tools' || !state?.owner;
  $('claim').hidden = !state || state.owner;
  drawDistanceMap($('distance-map-canvas'), state?.distanceMap);
  if ($('map-dialog').open) drawDistanceMap($('distance-map-full'), state?.distanceMap);
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

for (const mode of ['remote', 'ai', 'tools']) $(`mode-${mode}`).addEventListener('click', () => {
  link.setMode(mode);
  if (mode === 'tools') $('tool-modal').showModal();
});
function configureTool(reset = false) {
  const name = $('tool-name').value;
  const scanning = name === 'read_sensor_values';
  if (reset) {
    $('tool-angle').value = '0';
    $('tool-sweep').value = '180';
  }
  $('tool-distance-label').hidden = name !== 'goto';
  $('tool-sweep-label').hidden = !scanning;
  const sweep = Number($('tool-sweep').value);
  const limit = name === 'goto' ? 180 : scanning ? Math.max(0, 90 - sweep / 2) : 90;
  $('tool-angle').min = String(-limit);
  $('tool-angle').max = String(limit);
  $('tool-angle').value = String(Math.max(-limit, Math.min(limit, Number($('tool-angle').value))));
}
$('tool-name').addEventListener('change', () => configureTool(true));
$('tool-sweep').addEventListener('input', () => configureTool());
configureTool();
$('tool-close').addEventListener('click', () => $('tool-modal').close());
$('tool-call').addEventListener('click', () => {
  if (link.state?.mode !== 'tools') return;
  const name = $('tool-name').value;
  const angle = Number($('tool-angle').value);
  const toolArguments = name === 'goto' ? {distance_m: Number($('tool-distance').value), angle_deg: angle}
    : name === 'look_at' ? {angle_deg: angle}
      : {angle_deg: angle, sweep_deg: Number($('tool-sweep').value)};
  const fields = ['tool-angle', ...(name === 'goto' ? ['tool-distance'] :
    name === 'read_sensor_values' ? ['tool-sweep'] : [])];
  for (const id of fields) {
    const input = $(id);
    if (!input.value.trim() || !Number.isFinite(Number(input.value)) || !input.reportValidity()) {
      $('tool-result').textContent = 'Lütfen belirtilen aralıkta geçerli bir sayı girin.';
      return;
    }
  }
  $('tool-result').textContent = 'Robot görevi yürütüyor…';
  $('tool-call').disabled = true;
  link.send({type: 'tool', name, arguments: toolArguments});
  $('tool-modal').close();
});
link.addEventListener('toolResult', ({detail}) => {
  $('tool-call').disabled = false;
  $('tool-result').textContent = JSON.stringify(detail.result, null, 2);
  const image = $('tool-image');
  image.hidden = !detail.result?.image_url;
  if (detail.result?.image_url) image.src = detail.result.image_url;
  if (!$('tool-modal').open) $('tool-modal').showModal();
});
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
$('distance-map').addEventListener('click', () => {
  $('map-dialog').showModal(); drawDistanceMap($('distance-map-full'), link.state?.distanceMap);
});
$('map-close').addEventListener('click', () => $('map-dialog').close());

function keyboard() {
  const held = (...codes) => codes.some(code => keys.has(code));
  if (drive.enabled && drive.pointer === null) {
    let x = Number(held('KeyD')) - Number(held('KeyA'));
    let y = Number(held('KeyS')) - Number(held('KeyW'));
    if (x) y = 0; // Four-way drive: turning takes priority.
    drive.position(x, y);
    link.input('drive', x, y);
  }
  if (head.enabled && head.pointer === null) {
    const x = Number(held('ArrowRight')) - Number(held('ArrowLeft'));
    const y = Number(held('ArrowDown')) - Number(held('ArrowUp'));
    head.position(x, y);
    link.input('head', x, y);
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
window.addEventListener('blur', () => { windowActive = false; link.stopManualInput(); render(); });
window.addEventListener('focus', () => { windowActive = true; render(); });
window.addEventListener('resize', () => link.stopManualInput());
render();
link.connect();

// Reuse this controller's ownership; the embedded editor has no second socket.
const mimicModal = $('mimics-modal');
const mimicFrame = $('mimics-frame');
$('mimics-open').addEventListener('click', () => {
  link.stop(); menu.close(); mimicModal.showModal();
  mimicFrame.src = '/mimics?embedded=1';
});
const closeMimics = () => {
  if (link.state?.owner) link.send({type:'stopMimic'});
  mimicModal.close(); mimicFrame.src = 'about:blank';
};
mimicModal.addEventListener('cancel', event => {event.preventDefault(); closeMimics();});
const editorCommands = new Set(['playMimic','stopMimic','stop','claim','mode']);
window.addEventListener('message', event => {
  if (event.origin !== location.origin || event.source !== mimicFrame.contentWindow || !mimicModal.open) return;
  if (event.data?.type === 'mimicClose') closeMimics();
  if (event.data?.type === 'mimicCommand' && editorCommands.has(event.data.command?.type)) link.send(event.data.command);
});
link.addEventListener('state', () => {
  if (mimicModal.open) mimicFrame.contentWindow?.postMessage({type:'mimicState',state:link.state},location.origin);
});
link.addEventListener('error', ({detail}) => {
  if (mimicModal.open) mimicFrame.contentWindow?.postMessage({type:'mimicError',message:typeof detail === 'string' ? detail : detail.message},location.origin);
});
