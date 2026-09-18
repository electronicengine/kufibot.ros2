// Shared wire protocol with the Expo controller; no ROS or UDP browser plugin.
const zero = () => ({ drive_x: 0, drive_y: 0, head_x: 0, head_y: 0 });

export class RobotConnection extends EventTarget {
  constructor() {
    super();
    this.state = null;
    this.axes = zero();
    this.control = null;
    this.peer = null;
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
    if (this.state?.mode !== 'ai' || this.pendingMode) this.stop();
    else this.emit('reset');
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
    if (this.frameVisible) this.emit('frame', null);
    this.frameVisible = false;
  }


  frameReceived() {
    this.lastFrame = performance.now();
    this.frameVisible = true;
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
    this.videoAbort?.abort();
    const oldPeer = this.peer; this.peer = null;
    if (oldPeer) oldPeer.close();
    this.emit('stream', null);
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
      this.startWebRtc(base);
    };
    ws.onmessage = event => {
      try {
        const data = JSON.parse(event.data);
        if (data.type === 'state' && data.version === 1 && data.sensors && data.joints &&
            ['remote', 'ai', 'tools'].includes(data.mode) && typeof data.owner === 'boolean') {
          this.lastState = performance.now();
          this.state = data;
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
      if (!this.state?.owner) return;
      if (now - this.lastHeartbeat >= 500) {
        this.send({ type: 'heartbeat' });
        this.lastHeartbeat = now;
      }
      if (this.ready) this.send({ type: 'input', ...this.axes });
    }, 100);
  }

  async startWebRtc(base) {
    const peer = this.peer = new RTCPeerConnection({ iceServers: [] });
    const abort = this.videoAbort = new AbortController();
    const timeout = setTimeout(() => abort.abort(), 15000);
    try {
      peer.addTransceiver('video', { direction: 'recvonly' });
      peer.ontrack = event => {
        this.frameReceived();
        this.emit('stream', event.streams[0]);
      };
      peer.onconnectionstatechange = () => {
        if (peer !== this.peer) return;
        if (['failed', 'disconnected'].includes(peer.connectionState)) this.retryVideo(peer, base);
      };
      const offer = await peer.createOffer();
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
        this.emit('error', `Kamera bağlantısı kurulamadı: ${error.message}`);
        this.retryVideo(peer, base);
      }
    } finally {
      clearTimeout(timeout);
    }
  }

  retryVideo(peer, base) {
    // Camera availability must not revoke workflow ownership or cancel uploads.
    this.clearFrame();
    peer.close();
    setTimeout(() => {
      if (this.peer === peer && this.active && !this.disposed &&
          this.control?.readyState === WebSocket.OPEN) this.startWebRtc(base);
    }, 3000);
  }

  dispose() {
    this.disposed = true;
    this.suspend();
    document.removeEventListener('visibilitychange', this.visibility);
    window.removeEventListener('pagehide', this.pagehide);
    window.removeEventListener('pageshow', this.pageshow);
  }
}
