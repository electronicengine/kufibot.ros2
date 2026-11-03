import {isNativeApp, postNative} from './connection.js';

const key = 'kufibot.networkHandoff';
const $ = id => document.getElementById(id);

export function initNetworkHandoff({fetcher = fetch, navigate = url => location.assign(url)} = {}) {
  let pending = null, timer = null, checking = false;
  function localURL(value) {
    const hostname = value.hostname;
    if (!/^[a-z0-9](?:[a-z0-9-]{0,61}[a-z0-9])?\.local$/i.test(hostname || '')) return null;
    const url = value.local_url ? new URL(value.local_url) : new URL(location.href);
    url.protocol = 'http:'; url.hostname = hostname; url.pathname = '/'; url.hash = 'connection'; url.search = '';
    if (value.local_url) url.port = new URL(value.local_url).port;
    return url;
  }
  function stop(message = '') {
    if (pending && message) postNative('networkFailed', {message});
    clearTimeout(timer); timer = null; pending = null;
    try { sessionStorage.removeItem(key); } catch {}
    $('device-network-handoff').hidden = true;
  }
  async function probe() {
    if (isNativeApp()) return;
    if (!pending || checking) return;
    if (Date.now() - pending.started > 15 * 60 * 1000) { stop(); return; }
    const url = localURL(pending);
    // HTTPS pages cannot fetch an HTTP portal. Top-level navigation stays usable.
    if (!url || location.protocol === 'https:') return;
    checking = true;
    const controller = new AbortController();
    const timeout = setTimeout(() => controller.abort(), 2500);
    try {
      const identity = new URL('/api/network-identity', url);
      identity.searchParams.set('handoff', pending.handoff);
      const response = await fetcher(identity, {mode:'cors', credentials:'omit', cache:'no-store', signal:controller.signal});
      if (response.ok) {
        const value = await response.json();
        if (pending && value.connected && value.robot_id === pending.robot_id && value.hostname === pending.hostname) {
          const destination = url.href;
          stop(); navigate(destination); return;
        }
      }
    } catch {} finally { clearTimeout(timeout); checking = false; }
    if (pending) timer = setTimeout(probe, 3000);
  }
  function start(value, ssid) {
    if (isNativeApp()) {
      clearTimeout(timer);
      pending = {...value, ssid, started:value.started || Date.now()};
      $('device-network-handoff').hidden = false;
      $('device-handoff-message').textContent = `Telefonunuzu “${ssid}” ağına bağlayıp uygulamaya dönün. Robot yeni IP adresinde otomatik aranacak.`;
      const open = $('device-handoff-open');
      open.href = '#connection'; open.textContent = 'Telefonun Wi-Fi ayarlarını aç';
      open.onclick = event => { event.preventDefault(); postNative('wifi'); };
      postNative('networkHandoff', {robot_id:value.robot_id, handoff:value.handoff, ssid, started:pending.started, target:value.target});
      try { sessionStorage.setItem(key, JSON.stringify(pending)); } catch {}
      return;
    }
    const url = localURL(value);
    if (!url) return;
    clearTimeout(timer);
    pending = {...value, ssid, started:value.started || Date.now()};
    $('device-network-handoff').hidden = false;
    $('device-handoff-message').textContent = `Cihazınızı “${ssid}” ağına bağlayın. Robot ${url.host} adresinden yeniden aranıyor.`;
    $('device-handoff-open').href = url.href;
    $('device-handoff-open').textContent = `Robotu aç · ${url.host}`;
    if (location.protocol === 'https:') $('device-handoff-message').textContent = `Cihazınızı “${ssid}” ağına bağladıktan sonra Robotu aç bağlantısına dokunun.`;
    try { sessionStorage.setItem(key, JSON.stringify(pending)); } catch {}
    if (pending.robot_id && pending.handoff) timer = setTimeout(probe, 1500);
  }
  try {
    const saved = JSON.parse(sessionStorage.getItem(key) || 'null');
    if (saved && Date.now() - saved.started < 15 * 60 * 1000) start(saved, saved.ssid);
  } catch {}
  window.addEventListener('online', () => { clearTimeout(timer); probe(); });
  document.addEventListener('visibilitychange', () => { if (!document.hidden) { clearTimeout(timer); probe(); } });
  return {start, stop};
}
