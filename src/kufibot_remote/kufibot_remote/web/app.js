import { RobotConnection } from './connection.js';

const $ = id => document.getElementById(id);
const link = new RobotConnection();
const menu = $('menu');
const aiTriggerModal = $('ai-trigger-modal');
const AI_TRIGGER_UUID_KEY = 'kufibot.aiTriggerUuid';
const AI_TRIGGER_UUID_PATTERN = /^[0-9a-f]{8}-[0-9a-f]{4}-[1-5][0-9a-f]{3}-[89ab][0-9a-f]{3}-[0-9a-f]{12}$/i;
let windowActive = true;
const pageTitles = {connection: 'Bağlantı Ayarları', voice: 'Ses Ajanı', 'verasist-audio': 'Verasist Ses', workflows: 'Workflowlar', recordings: 'Kayıtlar', calibration: 'Kalibrasyon'};
let activePage = location.hash.slice(1) || 'control';
if (!['control', 'connection', 'voice', 'verasist-audio', 'workflows', 'recordings', 'calibration'].includes(activePage)) activePage = 'control';
let imageLoaded = false;
const keys = new Set();
let driveSpeed = 1;
const canControl = () => link.ready && !menu.classList.contains('open') && activePage === 'control' && !$('mimics-modal').open && windowActive;

function openDrawer() { link.stopManualInput(); menu.classList.add('open'); menu.setAttribute('aria-hidden', 'false'); $('drawer-backdrop').hidden = false; render(); }
function closeDrawer() { menu.classList.remove('open'); menu.setAttribute('aria-hidden', 'true'); $('drawer-backdrop').hidden = true; render(); }
function selectPage(page, replace = false) {
  link.stopManualInput(); closeDrawer();
  if (page === 'mimics') { openMimics(); return; }
  if (page === 'recordings' && workflowDialog.open) workflowDialog.close();
  activePage = page;
  if (page === 'workflows') refreshWorkflowOptions();
  $('settings-page').hidden = page === 'control';
  $('settings-page').classList.toggle('workflows-page', page === 'workflows');
  $('settings-page').classList.toggle('recordings-page', page === 'recordings');
  const recordings = $('recordings-frame');
  if (page === 'recordings') {
    showRecordings();
  } else if (recordings.getAttribute('src') !== 'about:blank') recordings.src = 'about:blank';
  for (const article of document.querySelectorAll('[data-page-content]')) article.classList.toggle('active', article.dataset.pageContent === page);
  for (const button of document.querySelectorAll('[data-page]')) button.setAttribute('aria-current', String(button.dataset.page === page));
  if (page !== 'control') $('page-title').textContent = pageTitles[page];
  const hash = page === 'control' ? '' : `#${page}`;
  if (replace) history.replaceState(null, '', `${location.pathname}${location.search}${hash}`); else if (location.hash !== hash) history.pushState(null, '', hash || location.pathname);
  render();
}

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
    // Select the dominant axis so diagonal touches always resolve to one
    // unambiguous direction.
    if (this.part === 'drive') [x, y] = Math.abs(x) >= Math.abs(y)
      ? [Math.sign(x), 0] : [0, Math.sign(y)];
    this.position(x, y);
    link.input(this.part, this.part === 'drive' ? x * driveSpeed : x,
      this.part === 'drive' ? y * driveSpeed : y);
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

function calibrationChart(container, data, title) {
  const signature = JSON.stringify(data ?? null);
  if (container.dataset.signature === signature) return;
  container.dataset.signature = signature;
  container.replaceChildren();
  const heading = document.createElement('h4');
  heading.textContent = title;
  container.append(heading);
  if (!data?.bin_counts || data.bin_counts.length !== 36) {
    const empty = document.createElement('p');
    empty.textContent = 'Kayıtlı açı kapsamı verisi yok. Yeni kalibrasyon yapın.';
    if (data?.parameters) {
      const p = data.parameters;
      empty.textContent += ` Mevcut kayıt: Ofset X/Y ${p.offset_x.toFixed(2)} / ${p.offset_y.toFixed(2)}; Ölçek X/Y ${p.scale_x.toFixed(4)} / ${p.scale_y.toFixed(4)}. Eski kayıtta tarih ve açı ölçümleri bulunmuyor.`;
    }
    container.append(empty);
    return;
  }
  const counts = data.bin_counts;
  const info = document.createElement('p');
  info.textContent = `${counts.filter(n => n >= 3).length}/36 dilim · ${data.samples} ölçüm / en az ${data.target}` +
    (Number.isFinite(data.angle_deg) ? ` · Son açı: ${data.angle_deg.toFixed(1)}°` : ' · Açı bekleniyor');
  container.append(info);
  const svg = document.createElementNS('http://www.w3.org/2000/svg', 'svg');
  svg.setAttribute('viewBox', '0 0 280 280');
  svg.classList.add('calibration-chart');
  svg.setAttribute('aria-label', 'Açı başına üç ölçüm göstergesi');
  const detail = document.createElement('p');
  detail.textContent = 'Bir dilime dokunarak ölçüm sayısını görün.';
  const selected = Number(container.dataset.selected);
  counts.forEach((count, index) => {
    const group = document.createElementNS(svg.namespaceURI, 'g');
    const label = `${index * 10}–${(index + 1) * 10}°: ${count} ölçüm (en az 3)`;
    group.setAttribute('role', 'button'); group.setAttribute('tabindex', '0');
    group.setAttribute('aria-label', label);
    const select = () => { container.dataset.selected = String(index); detail.textContent = label; };
    group.addEventListener('click', select);
    group.addEventListener('keydown', event => { if (event.key === 'Enter' || event.key === ' ') {event.preventDefault(); select();} });
    if (container.dataset.selected !== undefined && selected === index) detail.textContent = label;
    for (let slot = 0; slot < 3; slot++) {
      const angle = (index * 10 + 5) * Math.PI / 180;
      const radius = 96 + slot * 13;
      const dot = document.createElementNS(svg.namespaceURI, 'circle');
      dot.setAttribute('cx', String(140 + Math.sin(angle) * radius));
      dot.setAttribute('cy', String(140 - Math.cos(angle) * radius));
      dot.setAttribute('r', '5');
      dot.setAttribute('fill', count > slot ? (count >= 3 ? '#b8e75c' : '#ffc27a') : '#405064');
      group.append(dot);
    }
    svg.append(group);
  });
  if (Number.isFinite(data.angle_deg)) {
    const arrow = document.createElementNS(svg.namespaceURI, 'path');
    arrow.setAttribute('d', 'M 140 62 L 134 77 L 146 77 Z M 140 77 L 140 115');
    arrow.setAttribute('fill', '#79d6ff'); arrow.setAttribute('stroke', '#79d6ff');
    arrow.setAttribute('transform', `rotate(${data.angle_deg} 140 140)`); svg.append(arrow);
  }
  const label = document.createElementNS(svg.namespaceURI, 'text');
  label.setAttribute('x', '140'); label.setAttribute('y', '148'); label.setAttribute('text-anchor', 'middle');
  label.setAttribute('fill', '#eef5f7'); label.textContent = `${counts.filter(n => n >= 3).length}/36`;
  svg.append(label); container.append(svg, detail);
  if (data.completed_at) {
    const saved = document.createElement('p');
    const p = data.parameters;
    saved.textContent = `Kaydedildi: ${new Date(data.completed_at).toLocaleString('tr-TR')}` +
      (p ? ` · Ofset X/Y: ${p.offset_x.toFixed(2)} / ${p.offset_y.toFixed(2)} · Ölçek X/Y: ${p.scale_x.toFixed(4)} / ${p.scale_y.toFixed(4)}` : '') +
      (data.minimum && data.maximum ? ` · X aralığı: ${data.minimum.x}…${data.maximum.x} · Y aralığı: ${data.minimum.y}…${data.maximum.y}` : '');
    container.append(saved);
  }
}

function renderCalibration(state) {
  const calibration = state?.calibration;
  const active = !!calibration?.active;
  $('calibration-live').hidden = !active;
  if (active) calibrationChart($('calibration-live'), calibration, 'Canlı açı kapsamı');
  calibrationChart($('calibration-saved'), calibration?.last_result, 'Son kaydedilen kalibrasyon');
  const message = calibration?.message || 'Pusula sensörü bekleniyor';
  const progress = active && Number.isInteger(calibration.samples) && Number.isInteger(calibration.target)
    ? ` (${calibration.samples}/${calibration.target})` : '';
  $('calibration-info').textContent = `${message}${progress}`;
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
  if (document.activeElement !== input) {
    input.value = state?.aiTriggerUuid || localStorage.getItem(AI_TRIGGER_UUID_KEY) || '';
  }
  const valid = AI_TRIGGER_UUID_PATTERN.test(input.value.trim());
  $('start-ai-workflow').disabled = !state?.owner || !valid;
  $('ai-trigger-info').textContent = state?.aiTriggerUuid
    ? `Seçili UUID: ${state.aiTriggerUuid}` : 'YZ iş akışı UUID bekleniyor';
}

let aiDraft = null;
let aiDirty = false;
let aiSaved = null;
let toolsetPublishing = false;
let toolsetStatus = '';
let verasistAudioDraft = null;
let verasistAudioSaved = false;
let localWorkflows = [];
let aiWorkflowError = '';
const activationDefaults = {enabled:true, phrase:'Kufi', language:'tr', stt:'', tts:'', greeting_text:'Evet, seni dinliyorum.', greeting_mimic:'', farewell_text:'Görüşmek üzere.', farewell_mimic:''};
const voiceLabels = {preparing:'Hazırlanıyor', wake_listening:'Uyanma kelimesi bekleniyor', greeting:'Karşılıyor', connecting:'Bağlanıyor', connected:'Görüşmede', listening:'Görüşmede', farewell:'Kapanış', disabled:'Dinleme kapalı', error:'Hata', suspended:'Dinleme askıda', idle:'Oturum kapalı'};
function renderActivation(state, draft) {
  const value = {...activationDefaults, ...draft.activation};
  value.language = ({eng:'en', tur:'tr'})[value.language] || value.language;
  const models = state?.aiConfig?.models || [];
  const languages = [...new Set(['tr', 'en', ...models.filter(m => m.available && ['stt','tts'].includes(m.kind)).flatMap(m => m.languages)])].sort();
  for (const key of Object.keys(activationDefaults)) {
    const input = $(`activation-${key}`);
    input.disabled = !state?.owner || !state?.aiConfig;
    if (input.tagName === 'SELECT') {
      const items = key === 'language' ? languages.map(id => [id, ({tr:'Türkçe',en:'English'})[id] || id]) : key.endsWith('mimic') ? (state?.aiConfig?.mimics || []).map(m => [m.id, m.name]) :
        (state?.aiConfig?.models || []).filter(m => m.kind === key && m.available && m.languages.includes(value.language)).map(m => [m.id, m.label || m.id]);
      const options = key === 'language' ? items : [['', key.endsWith('mimic') ? 'Mimik yok' : 'Otomatik (kurulu model)'], ...items];
      if (value[key] && !options.some(([id]) => id === value[key])) options.push([value[key], `${value[key]} · kullanılamıyor`]);
      input.replaceChildren(...options.map(([id, name]) => {const option = document.createElement('option'); option.value=id; option.textContent=name; return option;}));
    }
    if (key === 'enabled') input.checked = value[key]; else if (input.tagName === 'SELECT' || document.activeElement !== input) input.value = value[key];
  }
  const missing = ['stt','tts'].filter(kind => (kind === 'stt' ? value.enabled : value.greeting_text || value.farewell_text) &&
    !(state?.aiConfig?.models || []).some(m => m.kind === kind && m.available && m.languages.includes(value.language) && (!value[kind] || value[kind] === m.id)));
  $('activation-status').textContent = [missing.length ? `Yerel ${missing.join(' / ').toUpperCase()} modeli kurup seçin.` : '', state?.aiConfig?.activation_status?.error, voiceLabels[state?.voiceStatus?.state] || state?.voiceStatus?.state].filter(Boolean).join(' · ');
  const heard = state?.aiConfig?.activation_status?.last_heard;
  for (const id of ['activation-heard', 'control-heard']) $(id).textContent = heard?.text || 'Henüz konuşma algılanmadı.';
  for (const id of ['activation-heard-match', 'control-heard-match']) $(id).textContent = heard ? `${new Date(heard.timestamp_ms).toLocaleTimeString('tr-TR')} · ${heard.matched ? 'Uyanma kelimesi eşleşti' : 'Uyanma kelimesi eşleşmedi'}` : '';
  $('stop-voice').disabled = !state?.owner || state?.mode !== 'ai';
}
function renderAiSettings(state) {
  const config = state?.aiConfig;
  if (!aiDirty && config?.settings) aiDraft = {...config.settings};
  if (aiSaved && JSON.stringify(config?.settings) === aiSaved) {
    aiDirty = false; aiSaved = null;
  }
  const draft = aiDraft || {provider: 'verasist', language: 'tr', stt: '', llm: '', embedding: '', tts: '', system_prompt: '', camera_attach_to_every_user_turn: false};
  renderActivation(state, draft);
  const local = draft.provider === 'local';
  $('ai-provider').value = draft.provider;
  $('ai-provider').disabled = !state?.owner || !config;
  $('ai-camera-context').checked = !!draft.camera_attach_to_every_user_turn;
  $('ai-camera-context').disabled = !state?.owner || !config || local;
  $('ai-camera-context').parentElement.hidden = local;
  $('ai-camera-help').hidden = local;
  $('ai-camera-help').textContent = local ? 'Yerel sağlayıcı görüntü desteklemiyor.' :
    'Seçim hemen kaydedilir ve uygulanır. Açıkken her konuşmanızın başındaki güncel robot kamera fotoğrafı ajana gönderilir. Kamera görüntüsü alınamazsa hata gösterilir.';
  $('local-model-settings').hidden = !local;
  $('verasist-settings').hidden = local;
  $('ai-workflow').disabled = !state?.owner;
  const valid = !local || localWorkflows.some(w => w.id === draft.workflow_id);
  $('save-ai-settings').disabled = !state?.owner || !config || !valid;
  $('save-ai-settings').textContent = local ? 'Seçili workflow’u bağla ve uygula' : 'Ayarları kaydet ve uygula';
  const saved = config && ['provider', 'workflow_id', 'camera_attach_to_every_user_turn', 'activation'].every(k => JSON.stringify(draft[k]) === JSON.stringify(config.settings[k]));
  $('ai-settings-status').textContent = [aiWorkflowError, config?.error, state?.voiceStatus?.detail,
    state?.voiceStatus?.state, aiSaved ? 'Uygulanması bekleniyor…' : '',
    local && !valid ? 'Bir workflow seçin veya workflow editöründen oluşturun.' : '',
    !config ? 'Sesli ajan ayarları bekleniyor' : !saved ? 'Önce ayarları kaydedin.' : state.mode !== 'ai' ? 'Ayarlar kayıtlı. Ana ekrandan YZ modu seçildiğinde ajan başlar.' : ''].filter(Boolean).join(' · ');
}

const verasistAudioDefaults = {aec_mode: 'system', mute_mic_during_playback: false,
  noise_gate_rms: 0, noise_gate_hangover_sec: .5, noise_suppression_db: -25,
  barge_in_rms: 0, barge_in_start_sec: .18, playback_echo_tail_sec: 1.2,
  aec_play_delay_ms: 180, aec_high_pass_filter: true, aec_noise_suppression: true,
  aec_gain_control: false, aec_extended_filter: true, aec_delay_agnostic: true};
function renderVerasistAudio(state) {
  const available = state?.aiConfig?.settings?.provider === 'verasist';
  $('verasist-audio-unavailable').hidden = available;
  $('verasist-audio-controls').hidden = !available;
  $('publish-verasist-tools').disabled = !available || !state?.owner || !state?.workflowToken || toolsetPublishing || !/^[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}$/i.test($('verasist-workflow-uuid').value.trim());
  $('verasist-workflow-uuid').disabled = !state?.owner || toolsetPublishing;
  $('publish-verasist-tools').textContent = toolsetPublishing ? 'Yayımlanıyor…' : 'Mevcut araçları yayımla';
  $('verasist-toolset-status').textContent = toolsetStatus;
  if (!available) return;
  if (!verasistAudioDraft) verasistAudioDraft = {...verasistAudioDefaults,
    ...(state.aiConfig.settings.verasist_audio || {})};
  const audio = verasistAudioDraft;
  const inputs = {aec_mode: 'verasist-aec-mode', mute_mic_during_playback: 'verasist-mute-playback',
    noise_suppression_db: 'verasist-noise-suppression', noise_gate_rms: 'verasist-noise-gate',
    noise_gate_hangover_sec: 'verasist-noise-hangover', barge_in_rms: 'verasist-barge-rms',
    barge_in_start_sec: 'verasist-barge-start', playback_echo_tail_sec: 'verasist-echo-tail',
    aec_play_delay_ms: 'verasist-aec-delay', aec_high_pass_filter: 'verasist-aec-high-pass',
    aec_noise_suppression: 'verasist-aec-noise-suppression', aec_gain_control: 'verasist-aec-gain-control',
    aec_extended_filter: 'verasist-aec-extended-filter', aec_delay_agnostic: 'verasist-aec-delay-agnostic'};
  const halfDuplex = !!audio.mute_mic_during_playback;
  for (const [key, id] of Object.entries(inputs)) {
    const input = $(id);
    const aecSetting = key === 'aec_mode' || key.startsWith('aec_');
    input.disabled = !state.owner || (halfDuplex && aecSetting);
    if (key === 'mute_mic_during_playback') input.checked = !!audio[key];
    else if (document.activeElement !== input) input.value = key === 'aec_mode' && halfDuplex
      ? 'disabled' : String(audio[key]);
  }
  $('verasist-aec-settings').setAttribute('aria-disabled', String(halfDuplex));
  $('save-verasist-audio').disabled = !state.owner;
  $('verasist-audio-status').textContent = verasistAudioSaved
    ? 'Kaydedildi; sonraki Verasist görüşmesinde uygulanacak.'
    : state.owner ? 'Yapılan değişiklikleri kaydedin.' : 'Kumandayı devralarak değiştirin.';
}

function drawDistanceMap(canvas, mapping, routePlan) {
  const ratio = devicePixelRatio || 1;
  const width = Math.max(1, Math.round(canvas.clientWidth * ratio));
  const height = Math.max(1, Math.round(canvas.clientHeight * ratio));
  if (canvas.width !== width || canvas.height !== height) { canvas.width = width; canvas.height = height; }
  const ctx = canvas.getContext('2d');
  ctx.clearRect(0, 0, width, height);
  ctx.fillStyle = '#101824d9'; ctx.fillRect(0, 0, width, height);
  const points = mapping?.obstacle_points || [];
  const robot = mapping?.robot_pose || [0, 0];
  const route = routePlan?.map_id === mapping?.map_id ? routePlan : null;
  const routePoints = route ? [route.start_pose, ...route.waypoints.map(p => [p.x_m, p.y_m])] : [];
  canvas.dataset.routeId = route?.route_id || '';
  canvas.dataset.routeStatus = route?.status || '';
  canvas.dataset.waypointCount = String(route?.waypoints.length || 0);
  canvas.setAttribute('aria-label', route ? `Rota: ${route.status}, ${route.completed_count}/${route.waypoints.length} nokta` : 'Mesafe haritası');
  const extent = Math.max(2, ...[...points, ...routePoints].flatMap(p => [Math.abs(p[0]), Math.abs(p[1])]),
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
  for (const path of mapping?.wall_paths ?? mapping?.boundary_paths ?? []) {
    if (path.length < 2) continue;
    ctx.beginPath(); ctx.moveTo(...point(path[0]));
    for (const vertex of path.slice(1)) ctx.lineTo(...point(vertex));
    ctx.stroke();
  }
  ctx.fillStyle = '#ff5454';
  for (const obstacle of mapping?.dynamic_obstacle_points || []) {
    ctx.beginPath(); ctx.arc(...point(obstacle), 4 * ratio, 0, Math.PI * 2); ctx.fill();
  }
  if (route) {
    routePoints.slice(1).forEach((target, index) => {
      const done = index < route.completed_count;
      const active = index === route.active_index && ['following', 'waiting_obstacle'].includes(route.status);
      const color = done ? '#69d49a' : active ? '#ffd66e' : '#67d9ed';
      ctx.strokeStyle = color; ctx.lineWidth = 2.5 * ratio;
      ctx.setLineDash(done ? [] : [5 * ratio, 3 * ratio]);
      ctx.beginPath(); ctx.moveTo(...point(routePoints[index])); ctx.lineTo(...point(target)); ctx.stroke();
      ctx.setLineDash([]);
      ctx.fillStyle = color; ctx.beginPath(); ctx.arc(...point(target), (active ? 9 : 7) * ratio, 0, Math.PI * 2); ctx.fill();
      ctx.font = `bold ${10 * ratio}px sans-serif`; ctx.textAlign = 'center'; ctx.textBaseline = 'middle';
      ctx.fillStyle = '#101824'; ctx.fillText(String(index + 1), ...point(target));
    });
    ctx.textAlign = 'left'; ctx.textBaseline = 'alphabetic';
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
  if (route) {
    const labels = {waiting_obstacle: 'Engel', following: 'İlerliyor', completed: 'Tamamlandı', blocked: 'Engellendi', cancelled: 'İptal', error: 'Durdu'};
    ctx.fillText(`Rota · ${labels[route.status] || route.status}${route.status === 'waiting_obstacle' ? ` (${Math.ceil(route.obstacle_wait_remaining_sec || 0)} sn)` : ''} · ${route.completed_count}/${route.waypoints.length}`, 8*ratio, 39*ratio);
  }
  const liveRange = link.state?.sensors.distance;
  ctx.fillText(Number.isFinite(liveRange) ? `Lidar baktığı yön: ${liveRange.toFixed(2)} m` : 'Lidar: — m', 8*ratio, 26*ratio);
}
for (const key of ['provider']) {
  $(`ai-${key}`).addEventListener('change', event => {
    aiDraft = {...(aiDraft || link.state?.aiConfig?.settings), [key]: event.target.value};
    if (key === 'language') for (const kind of ['stt', 'tts']) aiDraft[kind] = '';
    aiDirty = true; aiSaved = null;
    renderAiSettings(link.state);
  });
}
for (const key of Object.keys(activationDefaults)) {
  $(`activation-${key}`).addEventListener('input', event => {
    aiDraft = {...(aiDraft || link.state?.aiConfig?.settings)};
    aiDraft.activation = {...activationDefaults, ...aiDraft.activation, [key]: key === 'enabled' ? event.target.checked : event.target.value};
    if (key === 'language') {aiDraft.activation.stt=''; aiDraft.activation.tts='';}
    aiDirty = true; aiSaved = null;
    renderActivation(link.state, aiDraft);
  });
}
$('stop-voice').addEventListener('click', () => link.send({type:'stopVoice'}));
$('ai-camera-context').addEventListener('change', event => {
  aiDraft = {...(aiDraft || link.state?.aiConfig?.settings), camera_attach_to_every_user_turn: event.target.checked};
  aiDirty = true; aiSaved = null;
  // Apply the camera preference without saving unrelated edits in this form.
  const settings = {...link.state.aiConfig.settings, provider: 'verasist',
    camera_attach_to_every_user_turn: event.target.checked};
  if (link.send({type: 'setAiSettings', settings}) && JSON.stringify(settings) === JSON.stringify(aiDraft)) {
    aiSaved = JSON.stringify(settings);
  }
  renderAiSettings(link.state);
});
$('save-ai-settings').addEventListener('click', () => {
  if (aiDraft?.provider === 'local') {
    aiWorkflowError = '';
    const workflow = localWorkflows.find(w => w.id === aiDraft.workflow_id);
    if (workflow && link.send({type:'workflow',action:'activate',request_id:'voice-workflow-activate',id:workflow.id,activation:aiDraft.activation})) {
      $('ai-settings-status').textContent = 'Workflow doğrulanıyor ve bağlanıyor…';
    }
    return;
  }
  if (link.send({type: 'setAiSettings', settings: aiDraft})) aiSaved = JSON.stringify(aiDraft);
  renderAiSettings(link.state);
});
for (const [key, id] of Object.entries({aec_mode: 'verasist-aec-mode', mute_mic_during_playback: 'verasist-mute-playback',
  noise_suppression_db: 'verasist-noise-suppression', noise_gate_rms: 'verasist-noise-gate',
  noise_gate_hangover_sec: 'verasist-noise-hangover', barge_in_rms: 'verasist-barge-rms',
  barge_in_start_sec: 'verasist-barge-start', playback_echo_tail_sec: 'verasist-echo-tail',
  aec_play_delay_ms: 'verasist-aec-delay', aec_high_pass_filter: 'verasist-aec-high-pass',
  aec_noise_suppression: 'verasist-aec-noise-suppression', aec_gain_control: 'verasist-aec-gain-control',
  aec_extended_filter: 'verasist-aec-extended-filter', aec_delay_agnostic: 'verasist-aec-delay-agnostic'})) {
  $(id).addEventListener('input', event => {
    const booleanKeys = new Set(['mute_mic_during_playback', 'aec_high_pass_filter',
      'aec_noise_suppression', 'aec_gain_control', 'aec_extended_filter', 'aec_delay_agnostic']);
    verasistAudioDraft = {...(verasistAudioDraft || verasistAudioDefaults), [key]: booleanKeys.has(key) ? event.target.checked
      : key === 'aec_mode' ? event.target.value : Number(event.target.value)};
    verasistAudioSaved = false;
    renderVerasistAudio(link.state);
  });
}
$('verasist-workflow-uuid').addEventListener('input', () => {
  toolsetStatus = ''; renderVerasistAudio(link.state);
});
$('publish-verasist-tools').addEventListener('click', async () => {
  if (toolsetPublishing || !link.state?.owner || !link.state?.workflowToken) return;
  const workflowUuid = $('verasist-workflow-uuid').value.trim();
  toolsetPublishing = true; toolsetStatus = ''; renderVerasistAudio(link.state);
  try {
    const response = await fetch('/api/verasist/toolset', {
      method: 'POST', headers: {'Content-Type': 'application/json', Authorization: `Bearer ${link.state.workflowToken}`},
      body: JSON.stringify({workflow_uuid: workflowUuid}), signal: AbortSignal.timeout(40000),
    });
    const text = await response.text();
    let result; try { result = JSON.parse(text); } catch { throw new Error('Yayın yapılamadı. Robot bağlantısını ve kumanda sahipliğini kontrol edin.'); }
    if (!response.ok) throw new Error(result.error || 'Araç yayını başarısız');
    toolsetStatus = `${result.count} araç ${result.workflow_uuid} workflow’una yayımlandı. Araçları görmek için workflow sayfasını yenileyin.`;
  } catch (error) {
    toolsetStatus = error.name === 'TimeoutError' || error.name === 'AbortError'
      ? 'Yayın sonucu alınamadı. Yeniden denemeden önce workflow kataloğunu kontrol edin.' : error.message;
  } finally { toolsetPublishing = false; renderVerasistAudio(link.state); }
});
$('save-verasist-audio').addEventListener('click', () => {
  const settings = {...link.state.aiConfig.settings, verasist_audio: verasistAudioDraft};
  if (link.send({type: 'setAiSettings', settings})) verasistAudioSaved = true;
  renderVerasistAudio(link.state);
});

const workflowDialog = document.createElement('dialog');
workflowDialog.className = 'workflow-dialog';
workflowDialog.addEventListener('cancel', event => {
  event.preventDefault();
  workflowFrame.contentWindow?.postMessage({type:'workflowRequestClose'}, location.origin);
});
const workflowFrame = document.createElement('iframe');
workflowFrame.title = 'Local Agent Workflow';
workflowFrame.style.cssText = 'width:100%;height:100%;border:0';
workflowDialog.append(workflowFrame); document.body.append(workflowDialog);
workflowDialog.addEventListener('close', () => {
  link.send({type:'workflow',action:'testStop'});
  workflowFrame.src='about:blank';
  refreshWorkflowOptions();
});
let workflowLoading = false;
let workflowLoaded = false;
let renderedWorkflowLink;
function renderWorkflowList() {
  const query = $('workflow-search').value.trim().toLocaleLowerCase('tr');
  const workflows = localWorkflows.filter(w => w.name.toLocaleLowerCase('tr').includes(query))
    .sort((a, b) => a.name.localeCompare(b.name, 'tr'));
  const linked = link.state?.aiConfig?.settings?.workflow_id;
  renderedWorkflowLink = linked;
  $('workflow-list').replaceChildren(...workflows.map(w => {
    const card = document.createElement('div');
    card.className = 'workflow-card';
    const top = document.createElement('div'); top.className = 'workflow-card-top';
    const icon = document.createElement('span'); icon.className = 'workflow-card-icon';
    icon.innerHTML = '<svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="1.5" aria-hidden="true"><rect x="3" y="3" width="6" height="6" rx="2"/><rect x="15" y="15" width="6" height="6" rx="2"/><path d="M6 9v6a3 3 0 0 0 3 3h6M9 6h6a3 3 0 0 1 3 3v6"/></svg>';
    top.append(icon);
    if (w.id === linked) { const badge = document.createElement('span'); badge.className = 'workflow-linked'; badge.textContent = 'Ses ajanına bağlı'; top.append(badge); }
    const title = document.createElement('h4'); title.textContent = w.name;
    const meta = document.createElement('p'); meta.textContent = `${w.nodes?.length || 0} düğüm · Revizyon ${w.revision}`;
    const button = document.createElement('button'); button.className = 'workflow-card-open';
    button.textContent = 'Düzenle →'; button.setAttribute('aria-label', `${w.name} · Düzenle`);
    button.addEventListener('click', () => openWorkflowEditor(w.id));
    card.append(top, title, meta, button); return card;
  }));
  $('workflow-list-status').textContent = workflowLoading ? 'Workflowlar yükleniyor…' : query ? `${workflows.length} / ${localWorkflows.length} workflow` : `${localWorkflows.length} kayıtlı workflow`;
  $('workflow-empty').hidden = workflows.length > 0 || !workflowLoaded || workflowLoading;
  $('workflow-empty').querySelector('h4').textContent = query ? 'Eşleşen workflow bulunamadı' : 'İlk akışınızı oluşturun';
  $('workflow-empty').querySelector('p').textContent = query ? 'Başka bir ad arayın veya aramayı temizleyin.' : 'Yeni workflow ile başlayın; ajanınızı ekleyip araçlara bağlayın.';
}
async function refreshWorkflowOptions() {
  if (workflowLoading) return;
  workflowLoading = true;
  $('refresh-workflows').disabled = true;
  $('workflow-error').hidden = true;
  $('workflow-list').setAttribute('aria-busy', 'true');
  renderWorkflowList();
  try {
    const response = await fetch('/api/workflows', {cache:'no-store'});
    if (!response.ok) throw new Error('Workflow servisine erişilemiyor. Bağlantınızı kontrol edip yeniden deneyin.');
    localWorkflows = await response.json();
    workflowLoaded = true;
    $('ai-workflow').replaceChildren(new Option('Workflow seçin', ''), ...localWorkflows.map(w => new Option(w.name, w.id)));
    $('ai-workflow').value = aiDraft?.workflow_id || link.state?.aiConfig?.settings?.workflow_id || '';
    renderAiSettings(link.state);
  } catch (error) {
    $('workflow-error').hidden = false;
    $('workflow-error').querySelector('span').textContent = error.message;
  } finally {
    workflowLoading = false;
    $('refresh-workflows').disabled = false;
    $('workflow-list').setAttribute('aria-busy', 'false');
    renderWorkflowList();
    if (!workflowLoaded) $('workflow-list-status').textContent = 'Liste yüklenemedi';
  }
}
$('workflow-search').addEventListener('input', renderWorkflowList);
$('workflow-retry').addEventListener('click', refreshWorkflowOptions);
$('ai-workflow').addEventListener('change', event => {
  aiDraft = {...(aiDraft || link.state?.aiConfig?.settings), workflow_id:event.target.value};
  aiDirty = true; aiSaved = null;
  renderAiSettings(link.state);
});
function openWorkflowEditor(id = '') {
  link.stopManualInput(); closeDrawer();
  workflowFrame.src=`/workflows?embedded=1${id ? `&workflow=${encodeURIComponent(id)}` : ''}`;
  workflowDialog.showModal();
}
$('open-workflows').addEventListener('click', () => openWorkflowEditor());
$('new-workflow').addEventListener('click', () => openWorkflowEditor());
$('refresh-workflows').addEventListener('click', refreshWorkflowOptions);
let recordingsBusy = false;
let recordingsLoadedAt = 0;
let recordingsToken = '';
function showRecordings() {
  const id = new URLSearchParams(location.search).get('recording');
  $('recordings-list').hidden = !!id;
  $('recordings-detail').hidden = !id;
  const frame = $('recordings-frame');
  const src = id ? `/recordings?embedded=1&recording=${encodeURIComponent(id)}` : 'about:blank';
  if (frame.getAttribute('src') !== src) frame.src = src;
  if (!id) refreshRecordings();
}
function openRecording(id) {
  const url = new URL(location.href);
  if (id) url.searchParams.set('recording', id);
  else url.searchParams.delete('recording');
  url.hash = 'recordings';
  history.pushState(null, '', url);
  showRecordings();
}
async function refreshRecordings(force = false) {
  if (activePage !== 'recordings' || $('recordings-list').hidden || recordingsBusy) return;
  const token = link.state?.workflowToken;
  const status = $('recordings-status');
  if (!token) {
    $('recordings-rows').replaceChildren();
    recordingsToken = '';
    status.textContent = 'Kayıtları görüntülemek için robot bağlantısı ve kumanda sahipliği gerekli.';
    return;
  }
  if (!force && token === recordingsToken && Date.now() - recordingsLoadedAt < 5000) return;
  recordingsBusy = true;
  $('recordings-refresh').disabled = true;
  status.textContent = 'Kayıtlar yükleniyor…';
  try {
    const response = await fetch('/api/recordings', {headers: {Authorization: `Bearer ${token}`}, signal: AbortSignal.timeout(15000)});
    if (!response.ok) throw new Error('recordings');
    const records = await response.json();
    if (link.state?.workflowToken !== token) return;
    const rows = records.map(item => {
      const row = document.createElement('tr');
      const date = new Date(item.created_at * 1000).toLocaleString('tr-TR');
      const seconds = Math.max(0, Math.floor(item.duration_sec || 0));
      const duration = `${String(Math.floor(seconds / 60)).padStart(2, '0')}:${String(seconds % 60).padStart(2, '0')}`;
      for (const value of [date, item.workflow_name || 'Yerel görüşme', duration]) {
        const cell = document.createElement('td'); cell.textContent = value; row.append(cell);
      }
      const cell = document.createElement('td');
      const anchor = document.createElement('a');
      anchor.textContent = 'Sonuçları görüntüle ↗';
      anchor.href = `/?recording=${encodeURIComponent(item.id)}#recordings`;
      anchor.addEventListener('click', event => {
        event.stopPropagation();
        if (!event.ctrlKey && !event.metaKey && !event.shiftKey && !event.altKey) {event.preventDefault(); openRecording(item.id);}
      });
      cell.append(anchor); row.append(cell);
      row.addEventListener('click', () => openRecording(item.id));
      return row;
    });
    $('recordings-rows').replaceChildren(...rows);
    status.textContent = records.length ? `${records.length} görüşme kaydı` : 'Henüz tamamlanmış ses kaydı yok.';
    recordingsToken = token;
  } catch {
    status.textContent = 'Kayıtlar yüklenemedi. Bağlantıyı kontrol edip yeniden deneyin.';
  } finally {
    recordingsLoadedAt = Date.now();
    recordingsBusy = false;
    $('recordings-refresh').disabled = false;
  }
}
$('recordings-refresh').addEventListener('click', () => refreshRecordings(true));
$('recordings-back').addEventListener('click', () => openRecording(''));
setInterval(() => refreshRecordings(), 5000);
$('recordings-frame').addEventListener('load', () => {
  if (activePage === 'recordings' && link.state) $('recordings-frame').contentWindow?.postMessage(link.state, location.origin);
});
workflowFrame.addEventListener('load', () => {
  if (link.state) workflowFrame.contentWindow?.postMessage(link.state, location.origin);
});
window.addEventListener('message', event => {
  if (event.origin !== location.origin || event.source !== workflowFrame.contentWindow || !workflowDialog.open) return;
  if (event.data?.type === 'workflowClose') {workflowDialog.close();}
  if (event.data?.type === 'workflowCommand' && event.data.command?.type === 'workflow') link.send(event.data.command);
});
link.addEventListener('workflow', ({detail}) => {if(workflowDialog.open)workflowFrame.contentWindow?.postMessage(detail,location.origin)});
link.addEventListener('workflow', ({detail}) => {
  if (detail.request_id !== 'voice-workflow-activate') return;
  if (detail.error) { aiWorkflowError = detail.error; renderAiSettings(link.state); return; }
  aiWorkflowError = '';
  aiDirty = false; aiSaved = null;
  refreshWorkflowOptions();
});
link.addEventListener('state', ({detail}) => {
  if(workflowDialog.open)workflowFrame.contentWindow?.postMessage(detail,location.origin);
  if(activePage === 'recordings') {
    $('recordings-frame').contentWindow?.postMessage(detail, location.origin);
    refreshRecordings();
  }
  if (activePage === 'workflows' && renderedWorkflowLink !== detail?.aiConfig?.settings?.workflow_id) renderWorkflowList();
  if(!aiDirty) $('ai-workflow').value=detail?.aiConfig?.settings?.workflow_id || '';
});
refreshWorkflowOptions();

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
  drawDistanceMap($('distance-map-canvas'), state?.distanceMap, state?.navigation?.route_plan);
  if ($('map-dialog').open) drawDistanceMap($('distance-map-full'), state?.distanceMap, state?.navigation?.route_plan);
  const nav = state?.navigation;
  const navLabels = {disabled: 'Kapalı', idle: 'Komut bekleniyor', aligning: 'Kafa hizalanıyor',
    scanning: 'Taranıyor', advancing: 'İlerleniyor', turning: 'Dönülüyor', waiting_obstacle: 'Engelin kalkması bekleniyor',
    following_route: 'Rota izleniyor', waiting_llm: 'LLM bekleniyor', blocked: 'Engellendi', completed: 'Tamamlandı'};
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
  $('drive-speed').disabled = !enabled || !state?.driveAvailable;
  head.enable(enabled);
  $('prepare-mapping').disabled = !enabled;
  $('drive-label').textContent = state?.driveAvailable
    ? `HAREKET · %${Math.round(driveSpeed * 100)} HIZ` : 'HAREKET · MOTOR YOK';
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
  renderVerasistAudio(state);
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

function openAiTriggerModal() {
  const input = $('ai-trigger-modal-input');
  input.value = localStorage.getItem(AI_TRIGGER_UUID_KEY) || $('ai-trigger-uuid').value.trim();
  $('ai-trigger-modal-error').hidden = true;
  aiTriggerModal.showModal();
  input.focus();
  input.select();
}
function confirmAiTrigger() {
  const input = $('ai-trigger-modal-input');
  const value = input.value.trim();
  if (!AI_TRIGGER_UUID_PATTERN.test(value)) {
    $('ai-trigger-modal-error').hidden = false;
    return;
  }
  localStorage.setItem(AI_TRIGGER_UUID_KEY, value);
  $('ai-trigger-uuid').value = value;
  link.startAiWorkflow(value);
  aiTriggerModal.close();
}
$('ai-trigger-cancel').addEventListener('click', () => aiTriggerModal.close());
$('ai-trigger-confirm').addEventListener('click', confirmAiTrigger);
$('ai-trigger-modal-input').addEventListener('keydown', event => {
  if (event.key === 'Enter') { event.preventDefault(); confirmAiTrigger(); }
});
$('ai-trigger-modal-input').addEventListener('input', () => { $('ai-trigger-modal-error').hidden = true; });

for (const mode of ['remote', 'ai', 'tools']) $(`mode-${mode}`).addEventListener('click', () => {
  if (mode === 'ai' && link.state?.aiConfig?.settings?.provider === 'local' && !link.state.aiConfig.settings.workflow_id) {
    selectPage('voice');
    return;
  }
  if (mode === 'ai' && link.state?.aiConfig?.settings?.provider === 'verasist') {
    openAiTriggerModal();
    return;
  }
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
  $('tool-route-label').hidden = name !== 'follow_route';
  $('tool-angle').parentElement.hidden = name === 'follow_route';
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
  let toolArguments = name === 'goto' ? {distance_m: Number($('tool-distance').value), angle_deg: angle}
    : name === 'look_at' ? {angle_deg: angle}
      : {angle_deg: angle, sweep_deg: Number($('tool-sweep').value)};
  if (name === 'follow_route') {
    try {
      const map = link.state?.distanceMap;
      const waypoints = JSON.parse($('tool-route').value);
      if (!map?.map_id || !Array.isArray(waypoints) || !waypoints.length) throw new Error();
      toolArguments = {map_id: map.map_id, map_revision: map.revision, waypoints};
    } catch {
      $('tool-result').textContent = 'Güncel harita ve geçerli bir waypoint JSON listesi gerekli.';
      return;
    }
  }
  const fields = name === 'follow_route' ? [] : ['tool-angle', ...(name === 'goto' ? ['tool-distance'] :
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
$('prepare-mapping').addEventListener('click', () => {
  if (!canControl()) return;
  reset();
  link.stop();
  link.send({type: 'prepareMapping'});
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

$('menu-open').addEventListener('click', openDrawer);
$('menu-close').addEventListener('click', closeDrawer);
$('drawer-backdrop').addEventListener('click', closeDrawer);
$('page-menu').addEventListener('click', openDrawer);
$('page-back').addEventListener('click', () => selectPage('control'));
for (const button of document.querySelectorAll('[data-page]')) button.addEventListener('click', () => selectPage(button.dataset.page));
$('reconnect').addEventListener('click', () => { selectPage('control'); link.connect(); });
$('ai-trigger-uuid').addEventListener('input', () => renderAiWorkflow(link.state));
$('start-ai-workflow').addEventListener('click', () => {
  const value = $('ai-trigger-uuid').value.trim();
  localStorage.setItem(AI_TRIGGER_UUID_KEY, value);
  link.startAiWorkflow(value);
  selectPage('control');
});
$('calibrate-compass').addEventListener('click', () => {
  link.calibrateCompass();
  selectPage('calibration');
});
$('endpoint').textContent = location.host;
$('menu-address').textContent = location.origin;
$('distance-map').addEventListener('click', () => {
  $('map-dialog').showModal(); drawDistanceMap($('distance-map-full'), link.state?.distanceMap, link.state?.navigation?.route_plan);
});
$('map-close').addEventListener('click', () => $('map-dialog').close());
$('drive-speed').addEventListener('input', event => {
  driveSpeed = Number(event.target.value) / 100;
  $('drive-speed-value').textContent = `${Math.round(driveSpeed * 100)}%`;
  keyboard();
  render();
});

function keyboard() {
  const held = (...codes) => codes.some(code => keys.has(code));
  if (drive.enabled && drive.pointer === null) {
    let x = Number(held('KeyD')) - Number(held('KeyA'));
    let y = Number(held('KeyS')) - Number(held('KeyW'));
    if (x) y = 0; // Four-way drive: turning takes priority.
    drive.position(x, y);
    link.input('drive', x * driveSpeed, y * driveSpeed);
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
selectPage(activePage, true);
window.addEventListener('popstate', () => selectPage(location.hash.slice(1) || 'control', true));
window.addEventListener('hashchange', () => selectPage(location.hash.slice(1) || 'control', true));


// Reuse this controller's ownership; the embedded editor has no second socket.
const mimicModal = $('mimics-modal');
const mimicFrame = $('mimics-frame');
function openMimics() {
  link.stop(); closeDrawer(); mimicModal.showModal();
  mimicFrame.src = '/mimics?embedded=1';
}
const closeMimics = () => {
  if (link.state?.owner) link.send({type:'stopMimic'});
  mimicModal.close(); mimicFrame.src = 'about:blank'; selectPage('control');
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
