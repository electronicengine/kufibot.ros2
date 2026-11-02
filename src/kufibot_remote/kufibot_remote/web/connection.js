// Shared wire protocol with the Expo controller; no ROS or UDP browser plugin.
const zero = () => ({ drive_x: 0, drive_y: 0, head_x: 0, head_y: 0 });

export class RobotConnection extends EventTarget {
  constructor() {
    super();
    this.state = null;
    this.axes = zero();
    this.control = null;
    this.peer = null;
    this.videoRetry = null;
    this.videoBase = null;
    this.retry = null;
    this.pulse = null;
    this.pendingInput = false;
    this.pendingMode = null;
    this.claimPending = false;
    this.lastClaimAt = 0;
    this.frameVisible = false;
    this.active = !document.hidden;
    this.disposed = false;
    this.visibility = () => {
      this.active = !document.hidden;
      if (this.active) this.connect();
      else this.suspend();
    };
    this.pagehide = () => { this.active = false; this.suspend(); };
    this.pageshow = () => {
      if (!this.active && !document.hidden) { this.active = true; this.connect(); }
    };
    document.addEventListener('visibilitychange', this.visibility);
    window.addEventListener('pagehide', this.pagehide);
    window.addEventListener('pageshow', this.pageshow);
  }

  emit(name, detail) { this.dispatchEvent(new CustomEvent(name, { detail })); }
  get ready() {
    return !!this.state?.owner && this.state.mode === 'remote' &&
      this.state.appliedMode === 'remote' && !this.pendingMode && this.active;
  }

  send(data) {
    const ws = this.control;
    if (!ws || ws.readyState !== WebSocket.OPEN || ws.bufferedAmount >= 8192) return false;
    if (data.type === 'input' && this.pendingInput) return false;
    ws.send(JSON.stringify(data));
    if (data.type === 'input') this.pendingInput = true;
    return true;
  }

  input(part, x, y) {
    if (!this.ready || (part === 'drive' && !this.state.driveAvailable)) return;
    this.axes[`${part}_x`] = x;
    this.axes[`${part}_y`] = y;
    this.send({ type: 'input', ...this.axes });
  }

  stop() {
    this.axes = zero();
    // Stopping a local workflow returns the robot to remote mode, even if
    // its preceding mode acknowledgement has not reached the browser yet.
    this.pendingMode = null;
    if (this.state?.owner) this.send({ type: 'stop' });
    this.emit('reset');
  }

  stopManualInput() {
    // Focus/layout changes end manual gestures. Explicit STOP and connection
    // loss still revoke autonomous navigation through stop().
    this.axes = zero();
    if (this.ready) this.send({type: 'input', ...this.axes});
    this.emit('reset');
  }

  setMode(mode) {
    if (!this.state?.owner) return;
    this.stop();
    this.pendingMode = mode;
    if (!this.send({ type: 'mode', mode })) this.pendingMode = null;
    this.emit('state', this.state);
  }

  claim() { this.send({ type: 'claim' }); }

  joint(name, value) {
    if (this.ready) this.send({ type: 'joint', name, value });
  }

  calibrateCompass() {
    if (this.ready) this.send({ type: 'calibrateCompass' });
  }

  startAiWorkflow(triggerUuid) {
    if (this.state?.owner) this.send({ type: 'startAiWorkflow', triggerUuid });
  }

  clearFrame() {
    const visible = this.frameVisible;
    this.frameVisible = false;
    if (visible) this.emit('frame', null);
  }


  frameReceived() {
    this.lastFrame = performance.now();
    this.frameVisible = true;
    if (this.videoError) {
      this.videoError = false;
      this.emit('error', '');
    }
  }

  closeVideo() {
    clearTimeout(this.videoRetry);
    this.videoRetry = null;
    this.videoAbort?.abort();
    const oldPeer = this.peer;
    this.peer = null;
    if (oldPeer) {
      oldPeer.ontrack = oldPeer.onconnectionstatechange = null;
      oldPeer.close();
    }
    this.clearFrame();
    this.emit('stream', null);
  }

  checkVideo(now) {
    const peer = this.peer;
    if (!peer || this.videoRetry !== null) return;
    if (!this.videoNegotiating && now - Math.max(this.lastFrame, this.videoStartedAt) > 5000) {
      this.retryVideo(peer, this.videoBase);
      return;
    }
    if (this.videoStatsPending || now - this.videoStatsAt < 1000) return;
    this.videoStatsAt = now;
    this.videoStatsPending = true;
    // Media currentTime/timeupdate can advance while the last image is frozen.
    // Count decoded RTP frames even when the video element is hidden.
    peer.getStats().then(stats => {
      if (peer !== this.peer || this.videoRetry !== null) return;
      let frames = 0;
      for (const report of stats.values()) {
        if (report.type === 'inbound-rtp' && (report.kind || report.mediaType) === 'video') {
          frames += report.framesDecoded ?? report.framesReceived ?? 0;
        }
      }
      if (frames > this.videoFrames) this.frameReceived();
      this.videoFrames = frames;
    }).catch(() => {
      // The deadline above also covers a failed stats request.
    }).finally(() => {
      if (peer === this.peer) this.videoStatsPending = false;
    });
  }

  close() {
    clearTimeout(this.retry);
    clearInterval(this.pulse);
    for (const ws of [this.control]) {
      if (!ws) continue;
      ws.onopen = ws.onmessage = ws.onclose = ws.onerror = null;
      ws.close();
    }
    this.control = null;
    this.closeVideo();
    this.state = null;
    this.pendingInput = false;
    this.pendingMode = null;
    this.axes = zero();
    this.clearFrame();
    this.emit('reset');
    this.emit('state', null);
  }

  suspend() {
    this.stop();
    this.close();
    this.emit('connection', { text: 'Sekme duraklatıldı', connected: false });
  }

  reconnect() {
    this.stop();
    this.close();
    if (this.disposed || !this.active) return;
    this.emit('connection', { text: 'Bağlantı kesildi · yeniden deneniyor', connected: false });
    this.retry = setTimeout(() => this.connect(), 2000);
  }

  connect() {
    if (this.disposed || !this.active) return;
    this.stop();
    this.close();
    this.emit('error', '');
    this.emit('connection', { text: 'Bağlanıyor…', connected: false });
    const base = `${location.protocol === 'https:' ? 'wss:' : 'ws:'}//${location.host}`;
    this.lastState = performance.now();
    this.lastFrame = 0;
    this.lastHeartbeat = 0;
    const ws = this.control = new WebSocket(`${base}/control`);
    ws.onopen = () => {
      this.claim();
    };
    ws.onmessage = event => {
      try {
        const data = JSON.parse(event.data);
        if (data.type === 'state' && data.version === 1 && data.sensors && data.joints &&
            ['remote', 'ai', 'tools'].includes(data.mode) && typeof data.owner === 'boolean') {
          this.lastState = performance.now();
          this.state = data;
          if (data.videoPaused) {
            if (this.peer || this.videoRetry !== null) this.closeVideo();
            if (this.videoError) { this.videoError = false; this.emit('error', ''); }
          } else if (!this.peer && this.videoRetry === null) {
            this.startWebRtc(base);
          }
          // A browser can receive a state packet before its initial claim is
          // processed, or immediately after a short Wi-Fi/WebRTC reconnect.
          // Reclaim an idle controller once so the UI cannot remain disabled.
          if (!data.owner && !this.claimPending && performance.now() - this.lastClaimAt > 1000) {
            this.claimPending = true;
            this.lastClaimAt = performance.now();
            this.claim();
          }
          if (data.owner) this.claimPending = false;
          if (this.pendingMode === data.mode && data.appliedMode === data.mode) this.pendingMode = null;
          if (!this.ready) { this.axes = zero(); this.emit('reset'); }
          if (!data.driveAvailable) { this.axes.drive_x = 0; this.axes.drive_y = 0; }
          if (!data.camera) this.clearFrame();
          this.emit('connection', { text: data.owner ? 'Bağlı · kontrol sende' : 'Bağlı · izleyici', connected: true });
          this.emit('state', data);
        } else if (data.type === 'workflowResult' || data.type === 'workflowEvent') {
          this.emit('workflow', data);
        } else if (data.type === 'toolResult') {
          this.emit('toolResult', data);
        } else if (data.type === 'ack' || data.type === 'error') {
          if (data.command === 'input') this.pendingInput = false;
          if (data.type === 'error') {
            if (data.command === 'mode') this.pendingMode = null;
            if (data.command === 'claim') this.claimPending = false;
            // A final joystick packet can arrive after the AI mode change.
            // The server still rejects it; it is not an actionable UI error.
            if (data.command === 'input' && this.state?.mode !== 'remote') return;
            this.emit('error', String(data.message));
          } else if (['claim', 'mode', 'joint'].includes(data.command)) {
            if (data.command === 'claim') this.claimPending = false;
            this.emit('error', '');
          }
        }
      } catch { this.emit('error', 'Geçersiz robot yanıtı'); }
    };
    ws.onerror = ws.onclose = () => this.reconnect();
    this.pulse = setInterval(() => {
      const now = performance.now();
      if (now - this.lastState > 2500) { this.reconnect(); return; }
      if (now - this.lastFrame > 2000) this.clearFrame();
      this.checkVideo(now);
      if (!this.state?.owner) return;
      if (now - this.lastHeartbeat >= 500) {
        this.send({ type: 'heartbeat' });
        this.lastHeartbeat = now;
      }
      if (this.ready) this.send({ type: 'input', ...this.axes });
    }, 100);
  }

  async startWebRtc(base) {
    if (this.disposed || !this.active || !this.state || this.state.videoPaused ||
        this.control?.readyState !== WebSocket.OPEN) return;
    this.closeVideo();
    this.videoBase = base;
    this.videoNegotiating = true;
    this.videoStartedAt = performance.now();
    this.lastFrame = 0;
    this.videoFrames = 0;
    this.videoStatsAt = 0;
    this.videoStatsPending = false;
    const peer = this.peer = new RTCPeerConnection({ iceServers: [] });
    const abort = this.videoAbort = new AbortController();
    const timeout = setTimeout(() => abort.abort(), 15000);
    try {
      peer.addTransceiver('video', { direction: 'recvonly' });
      peer.ontrack = event => {
        if (peer !== this.peer || abort.signal.aborted) return;
        event.track.onended = () => this.retryVideo(peer, base);
        this.emit('stream', event.streams[0] || new MediaStream([event.track]));
      };
      peer.onconnectionstatechange = () => {
        if (peer !== this.peer) return;
        if (['failed', 'disconnected'].includes(peer.connectionState)) this.retryVideo(peer, base);
      };
      const offer = await peer.createOffer();
      if (peer !== this.peer || abort.signal.aborted) return;
      await peer.setLocalDescription(offer);
      while (peer.iceGatheringState !== 'complete') {
        if (abort.signal.aborted || peer !== this.peer) throw new Error('Video bağlantısı iptal edildi');
        await new Promise(resolve => setTimeout(resolve, 25));
      }
      const response = await fetch(`${base.replace(/^ws/, 'http')}/offer`, {
        method: 'POST', headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify(peer.localDescription), signal: abort.signal,
      });
      if (!response.ok) throw new Error(`WebRTC signaling failed (${response.status})`);
      const answer = await response.json();
      if (peer === this.peer) await peer.setRemoteDescription(answer);
    } catch (error) {
      if (peer === this.peer) {
        this.videoError = true;
        this.emit('error', `Kamera bağlantısı kurulamadı: ${error.message}`);
        this.retryVideo(peer, base);
      }
    } finally {
      clearTimeout(timeout);
      if (peer === this.peer) {
        this.videoNegotiating = false;
        this.videoStartedAt = performance.now();
      }
    }
  }

  retryVideo(peer, base) {
    if (!peer || peer !== this.peer || this.videoRetry !== null) return;
    if (this.state?.videoPaused) { this.closeVideo(); return; }
    // Camera availability must not revoke workflow ownership or cancel uploads.
    this.clearFrame();
    this.videoAbort?.abort();
    peer.ontrack = peer.onconnectionstatechange = null;
    peer.close();
    this.emit('stream', null);
    this.videoRetry = setTimeout(() => {
      this.videoRetry = null;
      if (this.peer === peer && this.active && !this.disposed &&
          this.control?.readyState === WebSocket.OPEN) this.startWebRtc(base);
    }, 1000);
  }

  dispose() {
    this.disposed = true;
    this.suspend();
    document.removeEventListener('visibilitychange', this.visibility);
    window.removeEventListener('pagehide', this.pagehide);
    window.removeEventListener('pageshow', this.pageshow);
  }
}

export const isNativeApp = () => window.parent === window &&
  typeof window.ReactNativeWebView?.postMessage === 'function';

export function postNative(action, values = {}) {
  if (!isNativeApp()) return false;
  window.ReactNativeWebView.postMessage(JSON.stringify({type:'kufibotNative', version:1, action, ...values}));
  return true;
}

export function initNativeApp(link) {
  if (!isNativeApp()) return;
  const section = document.createElement('section');
  section.id = 'native-app-connection'; section.className = 'device-section';
  const heading = document.createElement('div'); heading.className = 'device-heading';
  const title = document.createElement('h3'); title.textContent = 'Uygulama bağlantısı';
  heading.append(title); section.append(heading);
  const address = document.createElement('p'); address.className = 'device-help';
  address.id = 'native-app-address'; address.textContent = location.origin + '/'; section.append(address);
  const actions = document.createElement('div'); actions.className = 'device-actions';
  for (const [action, text] of [['discovery','Robotları bul'], ['wifi','Telefonun Wi-Fi ayarlarını aç'],
    ['share','Web adresini paylaş'], ['browser','Tarayıcıda aç']]) {
    const button = document.createElement('button');
    button.type = 'button'; button.className = 'device-quiet'; button.id = `native-app-${action}`;
    button.textContent = text; button.addEventListener('click', () => postNative(action)); actions.append(button);
  }
  section.append(actions);
  document.querySelector('[data-page-content="connection"]').prepend(section);
  document.getElementById('device-network-connect-help').textContent =
    'Bağlandıktan sonra telefonunuzu da seçtiğiniz Wi-Fi ağına geçirip uygulamaya dönün. Uygulama aynı robotu yeni IP adresinde otomatik bulur. Bağlantı kurulamazsa robot önceki ağa veya kurulum ağına döner.';
  const localAddress = document.getElementById('device-hotspot-local').parentElement;
  localAddress.hidden = true; localAddress.previousElementSibling.hidden = true;
  let lastConnected;
  const publishConnection = connected => {
    if (lastConnected === connected) return;
    lastConnected = connected; postNative('connection', {connected});
  };
  link.addEventListener('state', () => publishConnection(Boolean(link.state)));
  link.addEventListener('reset', () => publishConnection(Boolean(link.state)));
  window.addEventListener('pagehide', () => publishConnection(false));
  window.addEventListener('kufibot-native-back', () => {
    const dialog = [...document.querySelectorAll('dialog[open]')].at(-1);
    if (dialog) {
      if (dialog.dispatchEvent(new Event('cancel', {cancelable:true}))) dialog.close();
      return;
    }
    if (document.getElementById('menu').classList.contains('open')) {
      document.getElementById('menu-close').click(); return;
    }
    if (location.hash) {
      if (history.length > 1) history.back(); else location.hash = '';
      return;
    }
    postNative('discovery');
  });
  postNative('ready');
}
