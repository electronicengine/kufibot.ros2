import { initNetworkHandoff } from './network-handoff.js';
const $ = id => document.getElementById(id);
const prefix = '/api/device-settings/';

export function initDeviceSettings(link) {
  const handoff = initNetworkHandoff();
  let active = false, timer, audioLoaded = false, audioBusy = false, audioDirty = false;
  let networkBusy = false, scanning = false, loadingNetwork = false, selected = null;
  let saved = {}, networks = [], networkJob = 'idle', accepted = false;
  let hotspotSecured = true;
  const owner = () => Boolean(link.active && link.state?.owner && link.state?.workflowToken);
  const message = (id, text, error = false) => {
    $(id).textContent = text;
    $(id).classList.toggle('device-error', error);
  };
  async function request(path, data) {
    const controller = new AbortController();
    const timeout = setTimeout(() => controller.abort(), data ? 45000 : 15000);
    try {
      const response = await fetch(prefix + path, {
        method: data ? 'POST' : 'GET', cache: 'no-store', signal: controller.signal,
        headers: {...(data ? {'Content-Type': 'application/json'} : {}),
          ...(link.state?.workflowToken ? {Authorization:`Bearer ${link.state.workflowToken}`} : {})},
        ...(data ? {body: JSON.stringify(data)} : {}),
      });
      const result = await response.json().catch(() => ({}));
      if (!response.ok) throw new Error(result.error || 'İşlem tamamlanamadı. Bağlantıyı kontrol edin.');
      return result;
    } finally { clearTimeout(timeout); }
  }
  function renderControls() {
    const editable = owner();
    $('device-owner-notice').hidden = editable;
    $('device-claim').disabled = link.control?.readyState !== WebSocket.OPEN;
    for (const id of ['device-microphone', 'device-speaker']) $(id).disabled = !editable || audioBusy || !audioLoaded;
    $('device-audio-save').disabled = !editable || audioBusy || !audioLoaded || !audioDirty;
    $('device-audio-refresh').disabled = audioBusy;
    $('device-network-scan').disabled = !editable || scanning || networkBusy;
    $('device-network-connect').disabled = !editable || networkBusy || scanning || !selected?.supported || selected?.connected;
    $('device-network-password').disabled = !editable || networkBusy;
    for (const id of ['device-network-ssid', 'device-network-security', 'device-network-manual-select']) $(id).disabled = !editable || networkBusy;
    $('device-hotspot-password-show').disabled = !editable || !hotspotSecured;
    $('device-hotspot-password-show').hidden = !hotspotSecured;
    if (!editable || !hotspotSecured) { $('device-hotspot-password').textContent = ''; $('device-hotspot-password').hidden = true; }
    for (const row of $('device-network-list').querySelectorAll('button')) row.disabled = networkBusy;
  }
  function audioSelect(id, entries, current) {
    const select = $(id);
    select.replaceChildren(new Option('Mevcut robot yapılandırması', ''));
    for (const item of entries) select.add(new Option(item.label + (item.connected ? '' : ' · Bağlı değil'), item.id));
    if (current && !entries.some(item => item.id === current)) select.add(new Option('Seçilen aygıt · Bağlı değil', current));
    select.value = current || '';
  }
  async function loadAudio() {
    if (audioBusy) return;
    audioBusy = true; renderControls();
    try {
      const data = await request('audio');
      // Refresh the options without discarding an unsaved selection.
      const draft = audioDirty ? {microphone: $('device-microphone').value, speaker: $('device-speaker').value} : data.settings;
      saved = data.settings;
      audioSelect('device-microphone', data.microphones, draft.microphone);
      audioSelect('device-speaker', data.speakers, draft.speaker);
      audioLoaded = true;
      message('device-audio-status', 'Seçimler yeni görüşmelerde kullanılır.');
    } catch (error) { message('device-audio-status', error.message, true); }
    finally { audioBusy = false; updateDirty(); }
  }
  function updateDirty() {
    audioDirty = $('device-microphone').value !== saved.microphone || $('device-speaker').value !== saved.speaker;
    renderControls();
  }
  async function saveAudio() {
    if (!owner() || audioBusy) return;
    audioBusy = true; renderControls();
    message('device-audio-status', 'Aygıt bağlantısı kontrol ediliyor…');
    try {
      const data = await request('audio', {microphone: $('device-microphone').value, speaker: $('device-speaker').value});
      saved = data.settings;
      message('device-audio-status', data.message);
    } catch (error) { message('device-audio-status', error.message, true); }
    finally { audioBusy = false; updateDirty(); }
  }
  function selectNetwork(item) {
    if (networkBusy) return;
    if (selected?.ssid !== item.ssid) $('device-network-password').value = '';
    selected = item;
    $('device-network-form').hidden = false;
    $('device-network-name').textContent = item.ssid;
    $('device-network-password-field').hidden = !item.password_allowed || item.connected || !item.supported;
    $('device-network-password').required = item.password_allowed && !item.saved && item.supported && !item.connected;
    $('device-network-password').placeholder = item.saved ? 'Kayıtlı parolayı kullan' : 'Ağ parolası';
    $('device-network-password-help').textContent = item.saved ? 'Boş bırakırsanız robot kayıtlı bağlantıyı kullanır.' : 'Ağın Wi-Fi parolasını girin.';
    if (!item.supported) message('device-network-status', 'Bu ağ için robotta önceden yapılandırılmış bir bağlantı gerekli.', true);
    else if (item.connected) message('device-network-status', 'Robot zaten bu ağa bağlı.');
    else if (!accepted) message('device-network-status', '');
    renderNetworks(); renderControls();
  }
  function renderNetworks() {
    const list = $('device-network-list');
    const focused = list.contains(document.activeElement) ? document.activeElement.dataset.network : '';
    list.replaceChildren();
    for (const item of networks) {
      const row = document.createElement('button');
      row.type = 'button'; row.className = 'device-network-row'; row.dataset.network = item.id;
      row.setAttribute('aria-pressed', String(selected?.id === item.id));
      const signal = document.createElement('span'); signal.className = 'device-signal';
      signal.setAttribute('aria-label', `Sinyal yüzde ${item.signal}`);
      for (let i = 0; i < 4; i++) {
        const bar = document.createElement('span');
        bar.classList.toggle('lit', item.signal >= i * 25); signal.append(bar);
      }
      const title = document.createElement('span'); title.className = 'device-network-label';
      const name = document.createElement('strong'); name.textContent = item.ssid;
      const meta = document.createElement('small'); meta.textContent = item.security + (item.saved ? ' · Kayıtlı' : '');
      title.append(name, meta);
      const badge = document.createElement('span'); badge.className = 'device-network-badge';
      badge.textContent = item.connected ? 'Bağlı' : selected?.id === item.id ? 'Seçildi' : '›';
      row.classList.toggle('connected', item.connected); row.append(signal, title, badge);
      row.addEventListener('click', () => selectNetwork(item)); list.append(row);
    }
    if (!networks.length) {
      const empty = document.createElement('p'); empty.className = 'device-help';
      empty.textContent = 'Henüz bir ağ görünmüyor. Yakındaki ağları tarayın.'; list.append(empty);
    }
    if (focused) [...list.querySelectorAll('button')].find(row => row.dataset.network === focused)?.focus();
  }
  function applyNetwork(data) {
    networks = data.networks || [];
    const current = networks.find(item => item.connected);
    const setup = data.hotspot || {};
    hotspotSecured = setup.secured !== false;
    $('device-hotspot-active').hidden = !setup.active;
    $('device-hotspot-info').hidden = !setup.installed;
    if (setup.active && !selected) $('device-network-manual').open = true;
    $('device-network-current').textContent = setup.active ? `${setup.ssid} · Kurulum ağı açık` : current
      ? `${current.ssid} · ${(data.addresses || []).join(' · ')}` : 'Robot bir Wi-Fi ağına bağlı değil.';
    $('device-hotspot-ssid').textContent = setup.ssid || '';
    $('device-hotspot-security').textContent = hotspotSecured ? 'Parolalı ağ' : 'Parolasız ağ';
    for (const [id, address] of [['device-hotspot-url', setup.setup_url], ['device-hotspot-local', setup.local_url]]) {
      if (address && /^http:\/\/(?:[a-z0-9.-]+)(?::[0-9]+)?(?:\/|$)/i.test(address)) {
        $(id).href = address; $(id).textContent = address;
      }
    }
    if (data.hostname && /^[a-z0-9.-]+$/i.test(data.hostname)) {
      const url = new URL(setup.local_url || location.href); url.hostname = data.hostname; url.hash = 'connection';
      $('device-network-local').href = url.href;
      $('device-network-local').textContent = `${url.host} ↗`;
    }
    const status = data.status || {};
    networkJob = status.state || 'idle'; networkBusy = networkJob === 'connecting';
    if (status.message) message('device-network-status', status.message, networkJob === 'error');
    if (networkJob === 'connected' || networkJob === 'error') accepted = false;
    if (networkJob === 'error') handoff.stop();
    // AP paths can change after a scan; keep the user's SSID selection.
    if (selected && !selected.manual) {
      const match = networks.find(item => item.id === selected.id) || networks.find(item => item.ssid === selected.ssid && item.security === selected.security);
      if (match) selected = match;
      else if (!networkBusy) { selected = null; $('device-network-form').hidden = true; }
    }
    renderNetworks(); renderControls();
  }
  async function loadNetwork(scan = false) {
    if (loadingNetwork) return;
    loadingNetwork = true; scanning = scan; renderControls();
    if (scan) message('device-network-status', 'Yakındaki ağlar taranıyor…');
    try {
      applyNetwork(await request(scan ? 'network/scan' : 'network', scan ? {} : undefined));
      if (scan && networkJob === 'idle') message('device-network-status', `${networks.length} ağ bulundu.`);
    } catch (error) {
      if (accepted) message('device-network-status', 'Ağ değişirken bağlantı kesildi. Cihazınızı seçtiğiniz ağa bağlayın; robot sabit yerel adresinden aranıyor.');
      else message('device-network-status', error.message, true);
    } finally { loadingNetwork = false; scanning = false; renderControls(); }
  }
  async function connectNetwork(event) {
    event.preventDefault();
    if (!owner() || networkBusy || !selected?.supported || selected.connected) return;
    networkBusy = true; renderControls();
    message('device-network-status', 'Bağlantı isteği gönderiliyor…');
    const password = $('device-network-password').value;
    $('device-network-password').value = ''; $('device-network-password').type = 'password';
    $('device-network-reveal').textContent = 'Göster'; $('device-network-reveal').setAttribute('aria-pressed', 'false');
    try {
      const value = selected.manual ? {ssid:selected.ssid, security:selected.security, password} : {network:selected.id, password};
      const status = await request('network/connect', value);
      accepted = true; networkJob = status.state;
      handoff.start(status, selected.ssid);
      message('device-network-status', status.message + ' Cihazınızı da seçilen ağa bağlayın.');
    } catch (error) { networkBusy = false; message('device-network-status', error.message, true); }
    renderControls();
  }
  $('device-audio-refresh').addEventListener('click', loadAudio);
  $('device-audio-save').addEventListener('click', saveAudio);
  for (const id of ['device-microphone', 'device-speaker']) $(id).addEventListener('change', updateDirty);
  $('device-network-scan').addEventListener('click', () => loadNetwork(true));
  $('device-network-form').addEventListener('submit', connectNetwork);
  $('device-network-manual-form').addEventListener('submit', event => {
    event.preventDefault();
    if (!owner() || networkBusy) return;
    const ssid = $('device-network-ssid').value;
    if (!ssid || new TextEncoder().encode(ssid).length > 32) { message('device-network-status', 'Ağ adı 1–32 bayt olmalı.', true); return; }
    const security = $('device-network-security').value;
    selectNetwork({id:`manual:${ssid}`, manual:true, ssid, security, supported:true,
      connected:false, saved:false, secured:security !== 'open', password_allowed:security !== 'open'});
    $('device-network-password').focus();
  });
  $('device-hotspot-password-show').addEventListener('click', async () => {
    if (!owner()) return;
    if (!$('device-hotspot-password').hidden) { $('device-hotspot-password').textContent = ''; $('device-hotspot-password').hidden = true; return; }
    try {
      const value = await request('hotspot-credentials');
      if (!owner()) return;
      $('device-hotspot-password').textContent = value.password;
      $('device-hotspot-password').hidden = false;
    } catch (error) { message('device-network-status', error.message, true); }
  });
  $('device-claim').addEventListener('click', () => link.claim());
  $('device-network-reveal').addEventListener('click', () => {
    const reveal = $('device-network-password').type === 'password';
    $('device-network-password').type = reveal ? 'text' : 'password';
    $('device-network-reveal').textContent = reveal ? 'Gizle' : 'Göster';
    $('device-network-reveal').setAttribute('aria-pressed', String(reveal));
  });
  link.addEventListener('state', () => { if (active) renderControls(); });
  link.addEventListener('reset', renderControls);
  return {setActive(value) {
    active = value; clearInterval(timer);
    if (active) {
      loadAudio(); loadNetwork();
      timer = setInterval(() => loadNetwork(), 5000);
      renderControls();
    } else {
      $('device-network-password').value = ''; $('device-network-password').type = 'password';
      $('device-network-reveal').textContent = 'Göster'; $('device-network-reveal').setAttribute('aria-pressed', 'false');
    }
  }};
}
