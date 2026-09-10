import { useCallback, useEffect, useRef, useState } from 'react';
import { AppState } from 'react-native';
import { MediaStream, RTCPeerConnection, RTCSessionDescription } from 'react-native-webrtc';
import { Robot } from './discovery';

export type AiSettings = {provider: 'verasist' | 'local'; language: string; stt: string; llm: string; tts: string; system_prompt: string};
export type AiConfig = {settings: AiSettings; models: {id: string; label?: string; kind: 'stt' | 'llm' | 'tts'; languages: string[]; available: boolean}[]; error: string};
export type State = {
  navigation?: {enabled: boolean; state: string; reason: string; calibrated: boolean; task_id: string} | null;
  mimic?: {state: string; id: string | null; elapsed_ms: number; duration_ms?: number; revision?: number; error?: string | null};
  navigationRequested?: boolean;
  aiConfig?: AiConfig;
  voiceStatus?: {state: string; detail: string; active: boolean};
  type: 'state'; version: 1; owner: boolean; mode: 'ai' | 'remote';
  appliedMode: string | null; camera: boolean; driveAvailable: boolean;
  aiTriggerUuid?: string;
  distanceMap?: {frame: string; units: string; origin: number[]; robot_pose: number[];
    robot_heading_deg: number; obstacle_points: number[][]; boundary_paths?: number[][][]} | null;
  calibration?: {active: boolean; samples: number; target: number; message: string;
    raw?: {x: number; y: number}; minimum?: {x: number; y: number}; maximum?: {x: number; y: number}};
  sensors: Record<string, number | null>; joints: Record<string, number>;
};
type Axes = {drive_x: number; drive_y: number; head_x: number; head_y: number};
const zero = (): Axes => ({drive_x: 0, drive_y: 0, head_x: 0, head_y: 0});

// 124.0.7 inherits these runtime methods from event-target-shim, whose
// "/index" declaration is not resolved by Expo's bundler module resolution.
type VideoPeer = RTCPeerConnection & {
  addEventListener(type: 'track', listener: (event: {streams: MediaStream[]}) => void): void;
  addEventListener(type: 'connectionstatechange', listener: () => void): void;
};

export function useRobot(robot: Robot | null) {
  const [state, setState] = useState<State | null>(null);
  const [frame, setFrame] = useState<string | null>(null);
  const [connection, setConnection] = useState('Robot aranıyor');
  const [error, setError] = useState('');
  const socket = useRef<WebSocket | null>(null);
  const axes = useRef(zero());
  const inputPending = useRef(false);
  const live = useRef<State | null>(null);
  const send = useCallback((data: {type: string; [key: string]: unknown}) => {
    const ws = socket.current;
    if (data.type === 'input' && inputPending.current) return;
    // React Native does not initialize bufferedAmount; ACKs bound input traffic.
    if (ws?.readyState === WebSocket.OPEN && (ws.bufferedAmount ?? 0) < 8192) {
      ws.send(JSON.stringify(data));
      if (data.type === 'input') inputPending.current = true;
    }
  }, []);
  const stop = useCallback(() => {
    axes.current = zero();
    if (live.current?.owner) send({type: 'stop'});
  }, [send]);
  const input = useCallback((part: 'drive' | 'head', x: number, y: number) => {
    axes.current[`${part}_x`] = x;
    axes.current[`${part}_y`] = y;
    const s = live.current;
    if (s?.owner && s.mode === 'remote' && s.appliedMode === 'remote') {
      send({type: 'input', ...axes.current});
    }
  }, [send]);

  useEffect(() => {
    if (!robot) return;
    let disposed = false;
    let active = AppState.currentState === 'active';
    let retry: ReturnType<typeof setTimeout> | undefined;
    let pulse: ReturnType<typeof setInterval> | undefined;
    let ws: WebSocket | null = null;
    let video: RTCPeerConnection | null = null;
    let videoAbort: AbortController | null = null;
    let lastState = 0;
    let lastHeartbeat = 0;
    const reset = () => {
      axes.current = zero(); live.current = null; inputPending.current = false;
      setState(null); setFrame(null);
    };
    const close = () => {
      clearTimeout(retry); clearInterval(pulse);
      socket.current = null;
      const old = ws; ws = null;
      if (old) { old.onclose = null; old.onmessage = null; old.onopen = null; old.onerror = null; old.close(); }
      const oldVideo = video; video = null;
      videoAbort?.abort(); videoAbort = null;
      oldVideo?.close();
      reset();
    };
    const reconnect = () => {
      close();
      if (!disposed && active) {
        setConnection('Bağlantı kesildi · yeniden deneniyor');
        retry = setTimeout(connect, 2000);
      }
    };
    const startVideo = async () => {
      const peer = new RTCPeerConnection({iceServers: []}) as VideoPeer;
      video = peer;
      const abort = new AbortController();
      videoAbort = abort;
      const timeout = setTimeout(() => abort.abort(), 15000);
      peer.addEventListener('track', event => {
        if (video === peer && event.streams[0]) setFrame(event.streams[0].toURL());
      });
      peer.addEventListener('connectionstatechange', () => {
        if (video === peer && ['failed', 'disconnected'].includes(peer.connectionState)) reconnect();
      });
      try {
        peer.addTransceiver('video', {direction: 'recvonly'});
        await peer.setLocalDescription(await peer.createOffer());
        // SDP carries all candidates: this server does not use trickle ICE.
        while (peer.iceGatheringState !== 'complete') {
          if (abort.signal.aborted || video !== peer) throw new Error('Video bağlantısı iptal edildi');
          await new Promise(resolve => setTimeout(resolve, 25));
        }
        const response = await fetch(`http://${robot.host}:${robot.port}/offer`, {
          method: 'POST', headers: {'Content-Type': 'application/json'},
          body: JSON.stringify(peer.localDescription), signal: abort.signal,
        });
        if (!response.ok) throw new Error(`WebRTC: ${response.status}`);
        const answer = await response.json();
        if (video === peer) await peer.setRemoteDescription(new RTCSessionDescription(answer));
      } catch (error) {
        if (video === peer) {
          setError(String(error));
          reconnect();
        }
      } finally {
        clearTimeout(timeout);
      }
    };
    const connect = () => {
      if (disposed || !active) return;
      close(); setError(''); setConnection('Bağlanıyor…');
      lastState = Date.now(); lastHeartbeat = 0;
      ws = new WebSocket(`ws://${robot.host}:${robot.port}/control`);
      socket.current = ws;
      ws.onopen = () => {
        send({type: 'claim'});
        void startVideo();
      };
      ws.onmessage = event => {
        try {
          const data = JSON.parse(event.data);
          if (data.type === 'state' && data.version === 1 && data.sensors &&
              data.joints && ['remote', 'ai'].includes(data.mode)) {
            lastState = Date.now(); live.current = data; setState(data);
            setConnection(data.owner ? 'Bağlı' : 'İzleyici');
            if (!data.owner || data.mode !== 'remote' || data.appliedMode !== 'remote') axes.current = zero();
          } else if (data.type === 'ack') {
            if (data.command === 'input') inputPending.current = false;
          } else if (data.type === 'error') {
            if (data.command === 'input') inputPending.current = false;
            setError(String(data.message));
          }
        } catch { setError('Geçersiz robot yanıtı'); }
      };
      ws.onclose = reconnect;
      ws.onerror = reconnect;
      pulse = setInterval(() => {
        const now = Date.now();
        if (now - lastState > 2500) { reconnect(); return; }
        const s = live.current;
        if (!s?.owner) return;
        if (now - lastHeartbeat > 500) { send({type: 'heartbeat'}); lastHeartbeat = now; }
        if (s.mode === 'remote' && s.appliedMode === 'remote') send({type: 'input', ...axes.current});
      }, 100);
    };
    const listener = AppState.addEventListener('change', next => {
      active = next === 'active';
      stop();
      if (active) connect();
      else { close(); setConnection('Uygulama duraklatıldı'); }
    });
    connect();
    return () => { disposed = true; stop(); listener.remove(); close(); };
  }, [robot?.host, robot?.port, send, stop]);
  return {state, frame, connection, error, send, stop, input};
}
