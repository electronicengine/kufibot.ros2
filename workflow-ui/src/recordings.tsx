import React, { useEffect, useMemo, useRef, useState } from "react";

type Activity = {
  type: string; offset_ms: number; sequence: number; role?: string; text?: string;
  name?: string; node_id?: string; from_node?: string; arguments?: unknown;
  result?: any; score?: number; matched?: boolean; completion?: Activity;
};
type Recording = {
  id: string; created_at: number; duration_sec: number; workflow_name?: string;
  workflow?: { id?: string; name?: string; nodes?: Record<string, string> }; events?: Activity[];
};
type Peaks = { duration_sec: number; channels: Record<"user" | "assistant", number[][]> };
type Get = (path: string) => Promise<any>;
export function duration(seconds: number) {
  const value = Math.max(0, Math.floor(seconds || 0));
  return `${Math.floor(value / 60).toString().padStart(2, "0")}:${(value % 60).toString().padStart(2, "0")}`;
}
const date = (value: number) => new Date(value * 1000).toLocaleString("tr-TR", { dateStyle: "medium", timeStyle: "short" });
const endpoint = (id: string) => `/api/recordings/${encodeURIComponent(id)}`;
const resultLink = (id: string) => {
  const query = new URLSearchParams(location.search);
  query.set("recording", id);
  return `?${query}`;
};

export function RecordingsTable({ token, get, open, title = "Çalıştırma kayıtları" }: { token?: string; get: Get; open: (id: string) => void; title?: string }) {
  const [items, setItems] = useState<Recording[]>([]);
  const [loading, setLoading] = useState(true), [error, setError] = useState("");
  const [retry, setRetry] = useState(0);
  useEffect(() => {
    if (!token) { setLoading(false); return; }
    let cancelled = false;
    async function refresh(initial = false) {
      if (initial) setLoading(true);
      try {
        const data = await get("/api/recordings");
        if (!cancelled) { setItems(data); setError(""); }
      } catch { if (!cancelled) setError("Kayıtlar yüklenemedi. Bağlantıyı kontrol edip yeniden deneyin."); }
      finally { if (!cancelled) setLoading(false); }
    }
    refresh(true);
    const timer = setInterval(() => refresh(), 5000);
    return () => { cancelled = true; clearInterval(timer); };
  }, [!!token, get, retry]);
  return <div className="recordings-list">
    <div className="recordings-heading"><div><span className="recording-eyebrow">GÖRÜŞME ARŞİVİ</span><h3>{title}</h3><p>Her görüşmenin sesi ve adım adım geçmişi, bir arada.</p></div>
      <button disabled={loading || !token} onClick={() => setRetry(x => x + 1)}>Kayıtları yenile</button></div>
    {!token ? <p role="status">Kayıtları görüntülemek için robot bağlantısı ve kumanda sahipliği gerekli.</p> : <>
      {error && <p className="recording-error" role="alert">{error}</p>}
      {loading && <p role="status">Kayıtlar yükleniyor…</p>}
      {!loading && !error && !items.length && <div className="recording-empty"><span aria-hidden="true">◉</span><h4>Henüz tamamlanmış ses kaydı yok.</h4><p>Yerel görüşme tamamlandığında kaydı burada görünecek.</p></div>}
      {!!items.length && <div className="recordings-table-scroll"><table className="recordings-table"><thead><tr><th>Tarih / saat</th><th>Workflow</th><th>Süre</th><th><span className="recording-sr-only">Sonuç</span></th></tr></thead>
        <tbody>{items.map(item => <tr key={item.id} onClick={() => open(item.id)}><td><strong>{date(item.created_at)}</strong><small>Yerel sesli görüşme</small></td><td>{item.workflow_name || "Yerel görüşme"}</td><td className="recording-time">{duration(item.duration_sec)}</td><td><a href={resultLink(item.id)} onClick={event => { event.stopPropagation(); if (!event.ctrlKey && !event.metaKey && !event.shiftKey && !event.altKey) { event.preventDefault(); open(item.id); } }}>Sonuçları görüntüle <span aria-hidden="true">↗</span></a></td></tr>)}</tbody></table></div>}
    </>}
  </div>;
}

function timeline(events: Activity[]) {
  const result: Activity[] = [], pending: Activity[] = [];
  for (const event of [...events].sort((a, b) => a.offset_ms - b.offset_ms || a.sequence - b.sequence)) {
    if (event.type === "tool_result") {
      const index = pending.findIndex(item => item.name === event.name && item.node_id === event.node_id);
      if (index >= 0) { pending.splice(index, 1)[0].completion = event; continue; }
    }
    const item = { ...event };
    result.push(item);
    if (event.type === "tool_start") pending.push(item);
  }
  return result;
}

export function RecordingResult({ id, token, get, back }: { id: string; token?: string; get: Get; back: () => void }) {
  const [recording, setRecording] = useState<Recording | null>(null), [peaks, setPeaks] = useState<Peaks | null>(null);
  const [error, setError] = useState(""), [waveError, setWaveError] = useState("");
  const [retry, setRetry] = useState(0), [position, setPosition] = useState(0), [playing, setPlaying] = useState(false);
  const [audioError, setAudioError] = useState(""), [sourceToken, setSourceToken] = useState(token || "");
  const audio = useRef<HTMLAudioElement>(null), resume = useRef({ time: 0, playing: false });
  useEffect(() => { if (!sourceToken && token) setSourceToken(token); }, [token, sourceToken]);
  useEffect(() => {
    if (!token) return;
    let cancelled = false;
    setRecording(null); setPeaks(null); setError(""); setWaveError("");
    get(`${endpoint(id)}/details`).then(value => { if (!cancelled) setRecording(value); })
      .catch(() => { if (!cancelled) setError("Kayıt bulunamadı veya yüklenemedi. Bağlantıyı kontrol edip yeniden deneyin."); });
    get(`${endpoint(id)}/waveform`).then(value => { if (!cancelled) setPeaks(value); })
      .catch(() => { if (!cancelled) setWaveError("Ses dalgası yüklenemedi. Ses oynatıcısını kullanmaya devam edebilirsiniz."); });
    return () => { cancelled = true; };
  }, [id, !!token, get, retry]);
  const total = recording?.duration_sec || 0;
  function seek(time: number) {
    const value = Math.max(0, Math.min(total, time));
    if (audio.current) {
      resume.current.time = value;
      if (audio.current.readyState >= 1) audio.current.currentTime = value;
      else audio.current.load();
      setPosition(value);
    }
  }
  async function play() {
    const player = audio.current;
    if (!player) return;
    if (!player.paused) { player.pause(); return; }
    try { await player.play(); setAudioError(""); }
    catch { setAudioError("Ses oynatılamadı. Tekrar oynatmayı deneyin."); }
  }
  const items = useMemo(() => timeline(recording?.events || []), [recording?.events]);
  const node = (value?: string) => recording?.workflow?.nodes?.[value || ""] || value || "—";
  const messages = items.filter(item => item.type === "transcript").length;
  const tools = items.filter(item => item.type === "tool_start").length;
  return <main className="recording-result" lang="tr">
    <header className="recording-result-header"><button className="recording-back" onClick={back}>← Kayıtlara dön</button><span className="recording-eyebrow">WORKFLOW STUDIO / GÖRÜŞME SONUCU</span></header>
    <div className="recording-result-body">
      {!token ? <div className="recording-empty" role="status">Kayıt için robot bağlantısı ve kumanda sahipliği bekleniyor.</div> : error ? <div className="recording-empty" role="alert"><p>{error}</p><button onClick={() => setRetry(x => x + 1)}>Yeniden dene</button></div> : !recording ? <p role="status">Görüşme yükleniyor…</p> : <>
        <div className="recording-title"><div><span className="recording-eyebrow">YEREL SESLİ GÖRÜŞME</span><h1>{recording.workflow?.name || "Yerel görüşme"}</h1><p>{date(recording.created_at)} <span aria-hidden="true">·</span> Görüşme kaydı</p></div><span className="recording-status">● Tamamlandı</span></div>
        <div className="recording-stats"><div><small>Görüşme süresi</small><strong>{duration(total)}</strong></div><div><small>Konuşma mesajı</small><strong>{recording.events ? messages : "—"}</strong></div><div><small>Araç çağrısı</small><strong>{recording.events ? tools : "—"}</strong></div></div>
        <section className="recording-player" aria-label="Görüşme ses kaydı"><div className="recording-section-heading"><div><h2>Görüşmenin sesi</h2><p>Dalganın istediğiniz noktasına tıklayın veya sürükleyin.</p></div><span className="recording-chip">STEREO · 2 KANAL</span></div>
          <audio ref={audio} preload="metadata" src={sourceToken ? `${endpoint(id)}?token=${encodeURIComponent(sourceToken)}` : undefined}
            onTimeUpdate={event => { setPosition(event.currentTarget.currentTime); resume.current.time = event.currentTarget.currentTime; }}
            onPlay={() => { setPlaying(true); resume.current.playing = true; }} onPause={() => { setPlaying(false); resume.current.playing = false; }} onEnded={() => { setPlaying(false); resume.current.playing = false; }}
            onLoadedMetadata={event => { event.currentTarget.currentTime = Math.min(resume.current.time, total); if (resume.current.playing) event.currentTarget.play().catch(() => setAudioError("Oynatmayı yeniden başlatın.")); }}
            onError={() => { if (token && sourceToken !== token) { resume.current = { time: position, playing }; setSourceToken(token); } else setAudioError("Ses yüklenemedi. Bağlantıyı kontrol edip yeniden deneyin."); }}/>
          {peaks ? <div className="recording-waves">{(["user", "assistant"] as const).map(role => <Wave key={role} role={role} values={peaks.channels[role]} duration={total} position={position} seek={seek}/>)}
            <div className="recording-wave-ticks"><span>00:00</span><span>{duration(total / 2)}</span><span>{duration(total)}</span></div></div> : <p role="status">{waveError || "Ses dalgaları hazırlanıyor…"}</p>}
          <div className="recording-player-controls"><button className="recording-play" onClick={play} aria-label={playing ? "Sesi duraklat" : "Sesi oynat"}>{playing ? "Ⅱ" : "▶"}</button><span className="recording-time">{duration(position)} <span>/ {duration(total)}</span></span>
            <input className="recording-seek" type="range" aria-label="Ses konumu" min="0" max={total} step="0.1" value={position} onChange={event => seek(Number(event.target.value))}/>
            <label className="recording-speed">Hız <select aria-label="Oynatma hızı" defaultValue="1" onChange={event => { if (audio.current) audio.current.playbackRate = Number(event.target.value); }}>{[0.75, 1, 1.25, 1.5, 2].map(rate => <option key={rate} value={rate}>{rate}×</option>)}</select></label></div>
          {audioError && <div className="recording-error" role="alert">{audioError} <button onClick={() => { setAudioError(""); if (token !== sourceToken) setSourceToken(token || ""); else audio.current?.load(); }}>Sesi yeniden yükle</button></div>}
          {waveError && <button onClick={() => setRetry(x => x + 1)}>Dalgayı yeniden yükle</button>}
        </section>
        <section className="recording-history"><div className="recording-section-heading"><div><h2>Görüşme geçmişi</h2><p>Konuşmalar, düğüm geçişleri ve araç çağrıları.</p></div><span className="recording-chip">ZAMAN ÇİZELGESİ</span></div>
          {!items.length ? <div className="recording-empty">{recording.events ? "Bu görüşmede konuşma veya olay kaydedilmedi." : "Bu kayıtta geçmiş bilgisi bulunmuyor"}</div> : <ol className="recording-timeline">{items.map(item => {
            const tool = item.type.startsWith("tool_");
            const result = item.completion?.result ?? item.result;
            const failed = result?.status === "error" || result?.success === false || !!result?.error;
            const title = item.type === "transcript" ? item.role === "user" ? "Siz" : "Kufi" : item.type === "transition" ? `${node(item.from_node)} → ${node(item.node_id)}` : item.type === "node" ? `Etkin düğüm: ${node(item.node_id)}` : tool ? `Araç çağrısı: ${item.name}` : "Anlamsal eşleşme";
            return <li key={item.sequence} className={`recording-event ${item.type === "transcript" ? item.role : "activity"}`}>
              <button className="recording-event-time" aria-label={`${duration(item.offset_ms / 1000)} zamanına git`} onClick={() => seek(item.offset_ms / 1000)}>{duration(item.offset_ms / 1000)}</button>
              <div className="recording-event-card"><div className="recording-event-title"><strong>{title}</strong>{tool && <span className={`recording-chip ${failed ? "failed" : ""}`}>{failed ? "Hata" : result !== undefined ? "Sonuç alındı" : "Sonuç kaydedilmedi"}</span>}</div>
                {item.type === "transcript" ? <p>{item.text}</p> : tool ? <details><summary>Çağrı detayları</summary>{item.arguments !== undefined && <><h4>Argümanlar</h4><pre>{JSON.stringify(item.arguments, null, 2)}</pre></>}{result !== undefined && <><h4>Sonuç</h4><pre>{JSON.stringify(result, null, 2)}</pre></>}{item.completion && <button className="recording-event-time" onClick={() => seek(item.completion!.offset_ms / 1000)}>Sonuç zamanı · {duration(item.completion.offset_ms / 1000)}</button>}</details> : item.type === "semantic_match" ? <p>{item.matched ? "Eşleşme seçildi" : "Eşleşme seçilmedi"} · Skor {item.score?.toFixed(2)}</p> : null}
              </div></li>;
          })}</ol>}
        </section>
      </>}
    </div>
  </main>;
}

function Wave({ role, values, duration: total, position, seek }: { role: "user" | "assistant"; values: number[][]; duration: number; position: number; seek: (time: number) => void }) {
  const label = role === "user" ? "Siz · Mikrofon" : "Kufi · Asistan";
  const percent = total ? position / total * 1000 : 0;
  const path = useMemo(() => values.map(([min, max], index) => `M${(index + .5) / values.length * 1000},${40 - max * 36}V${40 - min * 36}`).join(" "), [values]);
  function pointer(event: React.PointerEvent<HTMLDivElement>) {
    const bounds = event.currentTarget.getBoundingClientRect();
    seek((event.clientX - bounds.left) / bounds.width * total);
  }
  return <div className={`recording-wave-row ${role}`}><span className="recording-wave-label"><i/>{label}</span>
    <div className="recording-wave" role="slider" tabIndex={0} aria-label={`${label} ses dalgası`} aria-valuemin={0} aria-valuemax={total} aria-valuenow={Math.min(position, total)} aria-valuetext={`${duration(position)} / ${duration(total)}`}
      onPointerDown={event => { event.currentTarget.focus(); event.currentTarget.setPointerCapture(event.pointerId); pointer(event); }} onPointerMove={event => { if (event.currentTarget.hasPointerCapture(event.pointerId)) pointer(event); }}
      onPointerUp={event => { if (event.currentTarget.hasPointerCapture(event.pointerId)) { pointer(event); event.currentTarget.releasePointerCapture(event.pointerId); } }}
      onKeyDown={event => { if (["ArrowLeft", "ArrowRight", "ArrowUp", "ArrowDown", "Home", "End"].includes(event.key)) { event.preventDefault(); seek(event.key === "Home" ? 0 : event.key === "End" ? total : position + (["ArrowLeft", "ArrowDown"].includes(event.key) ? -5 : 5)); } }}>
      <svg viewBox="0 0 1000 80" preserveAspectRatio="none" aria-hidden="true"><defs><clipPath id={`played-${role}`}><rect width={percent} height="80"/></clipPath></defs><path className="recording-wave-baseline" d="M0 40H1000"/><path className="recording-wave-unplayed" d={path} strokeWidth={Math.max(.6, 650 / values.length)}/><path className="recording-wave-played" clipPath={`url(#played-${role})`} d={path} strokeWidth={Math.max(.6, 650 / values.length)}/><path className="recording-wave-cursor" d={`M${percent} 0V80`}/></svg>
    </div>
  </div>;
}
