import React, { useEffect, useRef, useState } from "react";
import { createRoot } from "react-dom/client";
import {
  ReactFlow,
  ReactFlowProvider,
  Background,
  Controls,
  Handle,
  Position,
  addEdge,
  applyNodeChanges,
  applyEdgeChanges,
  useReactFlow,
  type Node,
  type Edge,
  type Connection,
} from "@xyflow/react";
import dagre from "@dagrejs/dagre";
import "@xyflow/react/dist/style.css";
import "./style.css";
import { RecordingsTable, RecordingResult } from "./recordings";
import { state, seenRuntimeEvents, send, command, get } from "./transport";
import { VoiceSettings } from "./voice-settings";

type Workflow = {
  id: string;
  schema_version: number;
  name: string;
  revision: number;
  nodes: Node[];
  edges: Edge[];
  settings: Record<string, any>;
  viewport: { x: number; y: number; zoom: number };
};
const labels: Record<string, string> = {
  start: "Başlangıç",
  agent: "Ajan",
  condition: "Koşul",
  tool: "Araç adımı",
  end: "Bitiş",
  toolResource: "Araç kaynağı",
  knowledge: "Bilgi koleksiyonu",
};
const iconPaths: Record<string, string> = {
  start: "m9 5 10 7-10 7Z", agent: "M5 8h14v12H5z M12 4v4 M8 12h1 M15 12h1 M8 16h8",
  condition: "m12 3 9 9-9 9-9-9Z", tool: "m14 5 5 5 M4 20l9-9 M14 5l3-2 4 4-2 3-5 1-3-3Z",
  end: "M6 6h12v12H6z", toolResource: "M9 7H5v12h12v-4 M12 3h9v9 M21 3l-9 9",
  knowledge: "M4 5h6l2 2 2-2h6v14h-6l-2 2-2-2H4z M12 7v14",
};
function Icon({kind}: {kind: string}) {
  return <svg className="flow-icon" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="1.6" strokeLinecap="round" strokeLinejoin="round" aria-hidden="true"><path d={iconPaths[kind] || iconPaths.agent}/></svg>;
}
const descriptions: Record<string, string> = {
  start: "Akışın giriş noktası", agent: "Konuşmayı ve davranışı yönetin", condition: "Bir koşula göre yolu ayırın",
  tool: "Robotun bir işlemi yürütmesini sağlayın", end: "Akışı tamamlayın", toolResource: "Ajana kullanabileceği bir araç verin", knowledge: "Dosyalarınızdan bilgi sağlayın",
};
function Card({ id, data, type, selected }: any) {
  const resource = ["toolResource", "knowledge"].includes(type);
  const outputs =
    type === "condition"
      ? ["true", "false"]
      : type === "tool"
        ? ["success", "error"]
        : [""];
  return (
    <div
      className={`card ${type} ${selected ? "selected" : ""} ${data.active ? "active" : ""}`}
    >
      {!resource && type !== "start" && (
        <Handle type="target" position={Position.Left} />
      )}
      <div className="card-title">
        <span className="node-icon"><Icon kind={type}/></span>
        {String(data.name || labels[type])}
      </div>
      <small>{labels[type]}</small>
      <p>
        {String(
          data.tool ||
            data.prompt ||
            data.message ||
            data.collection_id ||
            "Ayarları açmak için seçin",
        ).slice(0, 90)}
      </p>
      {type !== "end" &&
        outputs.map((out, i) => (
          <Handle
            key={out}
            id={out || undefined}
            type="source"
            position={Position.Right}
            style={{ top: `${40 + i * 30}%` }}
            title={out || "Bağla"}
          />
        ))}
      {outputs.length > 1 && <small>{outputs.join(" / ")}</small>}
    </div>
  );
}
const nodeTypes = Object.fromEntries(Object.keys(labels).map((k) => [k, Card]));
function blank(settings: Record<string, any>): Workflow {
  return {
    id: crypto.randomUUID(),
    name: "Yeni workflow",
    schema_version: 1,
    revision: 0,
    settings: {
      ...settings,
      provider: "local",
      workflow_id: "",
      system_prompt:
        "Sen Kufibot, Türkçe konuşan yardımcı bir robotsun. Yalnız bağlı araçları kullan. Yanıtlarını kısa tut.",
    },
    nodes: [
      {
        id: "start",
        type: "start",
        position: { x: 80, y: 160 },
        data: { name: "Başlangıç" },
      },
      {
        id: "agent",
        type: "agent",
        position: { x: 400, y: 160 },
        data: { name: "Türkçe Ajan", prompt: "" },
      },
    ],
    edges: [{ id: "start-agent", source: "start", target: "agent" }],
    viewport: { x: 0, y: 0, zoom: 1 },
  };
}

function Editor() {
  const [caps, setCaps] = useState<any>(null),
    [robot, setRobot] = useState<any>(state),
    [flow, setFlow] = useState<Workflow>(blank({}));
  const [list, setList] = useState<Workflow[]>([]),
    [collections, setCollections] = useState<any[]>([]),
    [docs, setDocs] = useState<any[]>([]);
  const [recordingId, setRecordingId] = useState(() => new URLSearchParams(location.search).get("recording") || "");
  function openRecording(id: string) {
    const url = new URL(location.href);
    url.searchParams.set("recording", id);
    window.history.pushState({}, "", url);
    setRecordingId(id);
  }
  function backToRecordings() {
    const url = new URL(location.href);
    url.searchParams.delete("recording");
    url.searchParams.set("tab", "recordings");
    window.history.pushState({}, "", url);
    setRecordingId(""); setPanel("test"); setTestTab("recordings");
  }
  useEffect(() => {
    const navigate = () => {
      setRecordingId(new URLSearchParams(location.search).get("recording") || "");
      setPanel("test"); setTestTab("recordings");
    };
    window.addEventListener("popstate", navigate);
    if (new URLSearchParams(location.search).get("tab") === "recordings") navigate();
    return () => window.removeEventListener("popstate", navigate);
  }, []);
  const [selected, setSelected] = useState(""),
    [panel, setPanel] = useState(""),
    [message, setMessage] = useState(""),
    [dirty, setDirty] = useState(false);
  const [libraryOpen, setLibraryOpen] = useState(false);
  const [nodeSearch, setNodeSearch] = useState("");
  const [busy, setBusy] = useState(false);
  const busyRef = useRef(false);
  const mutationVersion = useRef(0);
  const [saving, setSaving] = useState(false);
  function showPanel(value: string) { setPanel(value); setLibraryOpen(false); }
  const [collection, setCollection] = useState(""),
    [collectionName, setCollectionName] = useState(""),
    [delimiter, setDelimiter] = useState("");
  const [testText, setTestText] = useState(""),
    [real, setReal] = useState(false),
    [events, setEvents] = useState<any[]>([]);
  const [activeNodes, setActiveNodes] = useState<Record<string, string>>({live:"", text:""});
  const [argsText, setArgsText] = useState("{}");
  const [testMode, setTestMode] = useState("live");
  const [testTab, setTestTab] = useState("live");
  const [testExpanded, setTestExpanded] = useState(false);
  const [livePhase, setLivePhase] = useState("idle");
  const [textPhase, setTextPhase] = useState("idle");
  const [testErrors, setTestErrors] = useState<Record<string, string>>({live:"", text:""});
  const [liveDetail, setLiveDetail] = useState("");
  const textSnapshot = useRef("");
  const liveSince = useRef(0);
  const active = activeNodes[testMode];
  const visibleEvents = events.filter(e => (e.test_mode || "live") === testMode);
  function selectTestTab(value: string) { setTestTab(value); if (["live", "text"].includes(value)) setTestMode(value); }

  const chatLog = useRef<HTMLDivElement>(null);
  const followChat = useRef(true);
  const [preview, setPreview] = useState<any>(null);
  const history = useRef<Workflow[]>([]),
    future = useRef<Workflow[]>([]),
    input = useRef<HTMLInputElement>(null);
  const rf = useReactFlow();
  const canvasRef = useRef<HTMLDivElement>(null);
  useEffect(() => {
    if (!canvasRef.current) return;
    let timer: ReturnType<typeof setTimeout>;
    const observer = new ResizeObserver(() => {
      clearTimeout(timer);
      timer = setTimeout(() => rf.fitView({padding:0.25, maxZoom:1}), 100);
    });
    observer.observe(canvasRef.current);
    return () => { observer.disconnect(); clearTimeout(timer); };
  }, [rf]);
  const flowId = useRef(flow.id);
  flowId.current = flow.id;
  const owner = !!robot?.owner;
  const localVoice = robot?.aiConfig?.settings?.provider === "local";
  const voiceActive = !!robot?.voiceStatus?.active;
  const liveEngaged = ["starting", "running", "stopping"].includes(livePhase) || (localVoice && (voiceActive || robot?.appliedMode === "ai"));
  const textEngaged = ["waiting", "ready"].includes(textPhase);
  const testWorkflow = {...flow, nodes:flow.nodes.map(({selected, dragging, measured, ...node}) => node),
    edges:flow.edges.map(({selected, ...edge}) => edge)};
  const textChanged = !!textSnapshot.current && textSnapshot.current !== JSON.stringify(testWorkflow);
  const liveMessages = (robot?.voiceTranscripts || []).filter((item: any) =>
    (!item.workflow_id || item.workflow_id === flow.id) && (!liveSince.current || item.timestamp_ms >= liveSince.current));
  useEffect(() => {
    if (voiceActive && localVoice && livePhase !== "stopping") setLivePhase("running");
    else if (["running", "stopping"].includes(livePhase) && !voiceActive && robot?.appliedMode === "remote") setLivePhase("idle");
    if (robot?.voiceStatus?.state === "error" && livePhase === "starting") {
      setLivePhase("error"); setTestErrors(old => ({...old, live:robot.voiceStatus.detail || "Ses oturumu başlatılamadı"}));
    }
  }, [voiceActive, localVoice, robot?.appliedMode, robot?.voiceStatus?.state, livePhase]);
  const chatMessages = testMode === "live" && liveMessages.length ? liveMessages.map((item: any) => ({
    ...item, sources:[...visibleEvents].reverse().find(e => e.type === "answer" && e.text === item.text)?.sources,
  })) :
    visibleEvents.filter(e => ["answer", "transcript"].includes(e.type)).map((e, i) => ({
      id: `event-${i}`, role: e.type === "answer" ? "assistant" : e.role || "user", text:e.text, final:true,
      timestamp_ms: e.timestamp_ms, sources:e.sources, sequence:visibleEvents.indexOf(e),
    }));
  const nodeName = (id: string) => String(flow.nodes.find(n => n.id === id)?.data.name || id || "—");
  const activity = visibleEvents.flatMap((event, i) => {
    const trace = event.event;
    if (!trace || !["node", "transition", "tool_start", "tool_result", "semantic_match"].includes(trace.type)) return [];
    if (trace.name === "transition_node") return [];
    const transition = trace.type === "transition";
    return [{id:`activity-${i}`, role:"activity", timestamp_ms:event.timestamp_ms, sequence:i,
      title: trace.type === "semantic_match" ? `Anlamsal eşleşme: ${Number(trace.score).toFixed(2)} / ${Number(trace.threshold).toFixed(2)} · ${trace.matched ? "Seçildi" : trace.ambiguous ? "Eşit puan, işlem yapılmadı" : "Eşik altında"}` : transition ? `Düğüm geçişi: ${nodeName(trace.from_node)} → ${nodeName(trace.node_id)}` :
        trace.type === "node" ? `Etkin düğüm: ${nodeName(trace.node_id)}` :
        `${trace.type === "tool_start" ? "Araç çağrısı" : trace.result?.status === "error" ? "Araç hatası" : "Araç sonucu"}: ${trace.name}`,
      trace}];
  });
  const timeline: any[] = [...chatMessages.filter((item: any) => item.text), ...activity]
    .sort((a: any, b: any) => ((a.timestamp_ms || Number(a.id)/1e6 || 0) - (b.timestamp_ms || Number(b.id)/1e6 || 0))
      || (a.sequence ?? Number.MAX_SAFE_INTEGER) - (b.sequence ?? Number.MAX_SAFE_INTEGER));
  useEffect(() => {
    if (chatLog.current && followChat.current) chatLog.current.scrollTop = chatLog.current.scrollHeight;
  }, [robot?.voiceTranscripts, events, panel, testMode]);
  function restore(saved: Workflow) {
    seenRuntimeEvents.clear();
    setEvents([]); setActiveNodes({live:"",text:""});
    const ids = new Set(saved.nodes.map(n => n.id));
    const edges = saved.edges.filter(e => ids.has(e.source) && ids.has(e.target));
    setFlow({...saved, edges});
    setDirty(edges.length !== saved.edges.length);
    if (edges.length !== saved.edges.length)
      setMessage("Silinmiş düğümlere ait kopuk bağlantılar taslaktan kaldırıldı. Başlangıcı bir ajana bağlayın ve kaydedin.");
    history.current = [];
    future.current = [];
    rf.setViewport(saved.viewport);
  }
  async function refresh() {
    setList(await get("/api/workflows"));
    setCollections(await get("/api/knowledge/collections"));
    if (collection)
      setDocs(await get(`/api/knowledge/${collection}/documents`));
  }
  useEffect(() => {
    get("/api/workflow-capabilities")
      .then(async (value) => {
        setCaps(value);
        const defaults = { ...value.defaults };
        for (const [kind, wanted] of Object.entries({
          stt: "trRecognizeModel",
          llm: "ufakzeka-1-q8_0",
          tts: "fettah",
          embedding: "embedding:mxbaiV1",
        })) {
          defaults[kind] =
            value.models.find((m: any) => m.id === wanted && m.available)?.id ||
            value.models.find((m: any) => m.kind === kind && m.available)?.id ||
            "";
        }
        const requested = new URLSearchParams(location.search).get("workflow");
        if (requested) {
          const workflows: Workflow[] = await get("/api/workflows");
          const saved = workflows.find((w) => w.id === requested);
          if (!saved) throw new Error("Workflow bulunamadı");
          restore(saved);
        } else {
          setFlow(blank(defaults));
        }
      })
      .catch((e) => setMessage(e.message));
    refresh().catch((e) => setMessage(e.message));
    const onState = (e: any) => setRobot(e.detail);
    const onEvent = (e: any) => {
      if (e.detail.workflow_id && e.detail.workflow_id !== flowId.current) return;
      const value = e.detail;
      const mode = value.test_mode || "live";
      setEvents((old) => [...old.slice(-299), {...value, test_mode:mode, timestamp_ms:value.timestamp_ms || value.event?.timestamp_ms || Date.now()}]);
      if (value.type === "trace" && value.event.type === "node")
        setActiveNodes(old => ({...old, [mode]:value.event.node_id}));
      if (value.type === "answer" && mode === "text") setTextPhase(value.ended ? "ended" : "ready");
      if (value.type === "error") {
        setTestErrors(old => ({...old, [mode]:value.message || "Test başarısız"}));
        if (mode === "text") setTextPhase("error"); else setLivePhase("error");
      }
      if (value.type === "test_session") {
        if (mode === "live") {
          setLivePhase(value.status === "stopped" ? "stopping" : value.status);
          setLiveDetail(value.message || "");
          if (value.status === "error") setTestErrors(old => ({...old, live:value.message}));
        } else if (value.status === "stopped") {
          setTextPhase(old => ["ended", "error"].includes(old) ? old : "idle");
          textSnapshot.current = "";
        }
      }
    };
    const onUpload = (e: any) => {
      setMessage(e.detail.error || "Dosya yüklendi; indeksleme sıraya alındı");
      refresh().catch(() => {});
    };
    window.addEventListener("robot-state", onState);
    window.addEventListener("workflow-event", onEvent);
    window.addEventListener("upload-event", onUpload);
    return () => {
      window.removeEventListener("robot-state", onState);
      window.removeEventListener("workflow-event", onEvent);
      window.removeEventListener("upload-event", onUpload);
    };
  }, []);
  useEffect(() => {
    const timer = setInterval(() => refresh().catch(() => {}), 3000);
    return () => clearInterval(timer);
  }, [collection]);
  useEffect(() => {
    const f = (e: BeforeUnloadEvent) => {
      if (dirty) {
        e.preventDefault();
        e.returnValue = "";
      }
    };
    window.addEventListener("beforeunload", f);
    return () => window.removeEventListener("beforeunload", f);
  }, [dirty]);
  const node = flow.nodes.find((n) => n.id === selected);
  useEffect(
    () => setArgsText(JSON.stringify(node?.data.arguments || {}, null, 2)),
    [selected],
  );
  function change(next: Workflow | ((previous: Workflow) => Workflow), record = true) {
    if (!owner) return;
    mutationVersion.current++;
    if (record) {
      history.current = [...history.current.slice(-49), structuredClone(flow)];
      future.current = [];
    }
    setFlow(previous => typeof next === "function" ? next(previous) : next);
    setDirty(true);
  }
  function update(data: any) {
    if (node)
      change({
        ...flow,
        nodes: flow.nodes.map((n) =>
          n.id === node.id ? { ...n, data: { ...n.data, ...data } } : n,
        ),
      });
  }
  async function run(action: () => Promise<any>) {
    if (busyRef.current) return;
    busyRef.current = true; setBusy(true);
    try {
      setMessage("İşleniyor…");
      const result = await action();
      setMessage(result?.errors?.length ? result.errors.join("\n") : "Tamam");
      await refresh();
      return result;
    } catch (e: any) {
      setMessage(e.message);
    } finally { busyRef.current = false; setBusy(false); }
  }
  async function startLiveTest() {
    setLivePhase("starting"); setLiveDetail("Ses oturumu hazırlanıyor…");
    setTestErrors(old => ({...old, live:""}));
    selectTestTab("live"); followChat.current = true;
    liveSince.current = Date.now();
    setEvents(old => old.filter(e => e.test_mode === "text"));
    setActiveNodes(old => ({...old, live:""}));
    const version = mutationVersion.current;
    try {
      const result = await command("liveStart", {workflow:flow, real});
      setFlow(previous => version === mutationVersion.current ? result.workflow : {...previous, revision:result.workflow.revision});
      setDirty(version !== mutationVersion.current);
      textSnapshot.current = ""; setTextPhase("idle");
      return result;
    } catch (error: any) {
      setLivePhase("error"); setTestErrors(old => ({...old, live:error.message})); throw error;
    }
  }
  async function stopLiveTest() {
    setLivePhase("stopping"); setLiveDetail("Ses oturumu sonlandırılıyor…");
    try { return await command("liveStop"); }
    catch (error: any) { setLivePhase("error"); setTestErrors(old => ({...old, live:error.message})); throw error; }
  }
  async function resetTextTest() {
    const result = await command("testStop");
    setTextPhase("idle"); textSnapshot.current = "";
    setTestErrors(old => ({...old, text:""}));
    setEvents(old => old.filter(e => (e.test_mode || "live") !== "text"));
    setActiveNodes(old => ({...old, text:""}));
    return result;
  }
  async function sendTextTest() {
    const text = testText.trim();
    if (!owner || !text || textPhase === "waiting" || liveEngaged || voiceActive) return;
    setTestErrors(old => ({...old, text:""}));
    try {
      if (textChanged || ["ended", "error"].includes(textPhase)) await resetTextTest();
      setTextPhase("waiting"); followChat.current = true;
      textSnapshot.current = JSON.stringify(testWorkflow);
      setEvents(old => [...old, {type:"transcript",role:"user",text,test_mode:"text",timestamp_ms:Date.now()}]);
      const result = await command("test", {workflow:testWorkflow, text, real});
      setTestText("");
      return result;
    } catch (error: any) {
      setTextPhase("error"); setTestErrors(old => ({...old, text:error.message})); throw error;
    }
  }
  async function save() {
    const version = mutationVersion.current;
    setSaving(true);
    try {
      const result = await command("save", { workflow: { ...flow, viewport: rf.getViewport() } });
      setFlow(previous => version === mutationVersion.current ? result : {...previous, revision: result.revision});
      setDirty(version !== mutationVersion.current);
      return result;
    } finally { setSaving(false); }
  }
  function add(kind: string) {
    const id = crypto.randomUUID();
    const bounds = canvasRef.current!.getBoundingClientRect();
    const position = rf.screenToFlowPosition({x:bounds.left + bounds.width / 2 - 115, y:bounds.top + bounds.height / 2 - 65});
    const occupied = rf.getNodes();
    while (occupied.some(n => Math.abs(n.position.x - position.x) < 270 && position.y < n.position.y + (n.measured?.height || 180) + 30 && position.y + 210 > n.position.y)) position.y += 220;
    change({
      ...flow,
      nodes: [
        ...flow.nodes,
        {
          id,
          type: kind,
          position,
          data: {
            name: labels[kind],
            ...(kind === "condition"
              ? { operator: "contains", field: "user", value: "" }
              : {}),
            ...(["tool", "toolResource"].includes(kind)
              ? { tool: "get_sensor_data", arguments: { sensor: "all" } }
              : {}),
            ...(kind === "knowledge" ? { collection_id: collection } : {}),
          },
        },
      ],
    });
    setSelected(id);
    showPanel("edit");
    setTimeout(() => rf.fitView({padding:0.25, maxZoom:1}), 100);
  }
  async function upload(files: FileList | null) {
    if (!files || !collection) return;
    for (const file of Array.from(files)) {
      if (file.size > 20 * 1024 * 1024)
        throw new Error("Dosya 20 MiB sınırını aşıyor");
      const form = new FormData();
      form.append("file", file);
      const r = await fetch(
        `/api/knowledge/${collection}/upload${delimiter ? "?delimiter=" + encodeURIComponent(delimiter) : ""}`,
        {
          method: "POST",
          headers: { Authorization: `Bearer ${robot?.workflowToken}` },
          body: form,
        },
      );
      if (!r.ok) throw new Error(await r.text());
    }
    await refresh();
  }
  function arrange() {
    const graph = new dagre.graphlib.Graph();
    graph.setGraph({ rankdir: "LR", ranksep: 100, nodesep: 60 });
    graph.setDefaultEdgeLabel(() => ({}));
    flow.nodes.forEach((n) => graph.setNode(n.id, { width: 230, height: 130 }));
    flow.edges.forEach((e) => graph.setEdge(e.source, e.target));
    dagre.layout(graph);
    change({
      ...flow,
      nodes: flow.nodes.map((n) => ({
        ...n,
        position: { x: graph.node(n.id).x, y: graph.node(n.id).y },
      })),
    });
    setTimeout(() => rf.fitView({padding:0.25,maxZoom:1}), 50);
  }
  const close = () => {
    if (busyRef.current) return;
    if (dirty && !confirm("Kaydedilmeyen değişiklikler var. Kapatılsın mı?"))
      return;
    const value = { type: "workflowClose" };
    if (window.ReactNativeWebView)
      window.ReactNativeWebView.postMessage(JSON.stringify(value));
    else if (window.parent !== window)
      window.parent.postMessage(value, location.origin);
    else location.href = "/";
  };
  useEffect(() => {
    const requestClose = (event: MessageEvent) => {
      if (event.origin === location.origin && event.source === window.parent && event.data?.type === "workflowRequestClose") close();
    };
    const escape = (event: KeyboardEvent) => {
      if (event.key !== "Escape") return;
      event.preventDefault();
      if (panel || libraryOpen) { setPanel(""); setLibraryOpen(false); }
      else close();
    };
    window.addEventListener("message", requestClose);
    window.addEventListener("keydown", escape);
    return () => { window.removeEventListener("message", requestClose); window.removeEventListener("keydown", escape); };
  }, [dirty, panel, libraryOpen]);
  const newWorkflow = () => {
    if (!dirty || confirm("Taslak değişiklikleri bırakılsın mı?")) {
      restore(blank(flow.settings)); setDirty(true); mutationVersion.current++;
    }
  };
  if (recordingId) return <RecordingResult key={recordingId} id={recordingId} token={robot?.workflowToken} get={get} back={backToRecordings}/>;
  return (
    <main lang="tr">
      <header className="editor-header">
        <button className="back-button" aria-label="Workflow listesine dön" disabled={busy} onClick={close}>←</button>
        <div className="editor-identity"><span className="editor-eyebrow">WORKFLOW STUDIO</span>
          <input aria-label="Workflow adı" disabled={!owner} value={flow.name} onChange={e => change({...flow, name:e.target.value})}/>
        </div>
        <span className={`save-state ${dirty ? "is-dirty" : ""}`} role="status">{saving ? "Kaydediliyor…" : dirty ? "Kaydedilmemiş değişiklikler" : "Kaydedildi"}<small>Revizyon {flow.revision}</small></span>
        <div className="header-actions">
          <button disabled={!owner || busy} onClick={() => run(() => command("validate", {workflow:flow}))}>Doğrula</button>
          <button className="primary" disabled={!owner || busy} onClick={() => run(save)}>{saving ? "Kaydediliyor…" : "Kaydet"}</button>
          <button disabled={!owner || busy} onClick={() => run(async () => { const saved = await save(); return command("activate", {id:saved.id}); })}>Etkinleştir</button>
        </div>
      </header>
      <div className="editor-subbar">
        <div className="workflow-switcher"><select aria-label="Workflow değiştir" disabled={busy} value={flow.id} onChange={e => {
          if (dirty && !confirm("Taslak değişiklikleri bırakılsın mı?")) return;
          const next = list.find(w => w.id === e.target.value); if (next) { restore(next); mutationVersion.current++; }
        }}><option value={flow.id}>{flow.name}</option>{list.filter(w => w.id !== flow.id).map(w => <option key={w.id} value={w.id}>{w.name}</option>)}</select>
        <button aria-label="Yeni workflow" disabled={!owner || busy} onClick={newWorkflow}>＋ Yeni</button></div>
        <nav aria-label="Editör panelleri">
          <button className={`library-toggle ${libraryOpen ? "chosen" : ""}`} aria-expanded={libraryOpen} onClick={() => {setLibraryOpen(!libraryOpen); setPanel("");}}>＋ Düğümler</button>
          {[["edit", "Özellikler"], ["models", "Modeller"], ["settings", "Ayarlar"], ["knowledge", "Dosyalar"], ["test", "Test"]].map(([key,label]) => <button key={key} aria-pressed={panel === key} className={panel === key ? "chosen" : ""} onClick={() => showPanel(panel === key ? "" : key)}>{label}</button>)}
        </nav>
      </div>
      <div className="workspace">
        <aside className={`node-library ${libraryOpen ? "mobile-open" : ""}`} aria-label="Düğüm kütüphanesi">
          <div className="panel-heading"><div><small>OLUŞTUR</small><h3>Düğüm kütüphanesi</h3></div><button className="mobile-close" aria-label="Düğüm kütüphanesini kapat" onClick={() => setLibraryOpen(false)}>×</button></div>
          <input type="search" aria-label="Düğüm ara" placeholder="Düğüm ara…" value={nodeSearch} onChange={e => setNodeSearch(e.target.value)}/>
          <div className="palette-list">{Object.entries(labels).filter(([kind,label]) => `${label} ${descriptions[kind]}`.toLocaleLowerCase("tr").includes(nodeSearch.toLocaleLowerCase("tr"))).map(([kind,label]) => <button key={kind} className={`palette ${kind}`} aria-label={label} disabled={!owner} onClick={() => add(kind)}><Icon kind={kind}/><span>{label}<small>{descriptions[kind]}</small></span><span className="palette-plus">＋</span></button>)}</div>
          {Object.keys(labels).every(kind => !`${labels[kind]} ${descriptions[kind]}`.toLocaleLowerCase("tr").includes(nodeSearch.toLocaleLowerCase("tr"))) && <p>Eşleşen düğüm bulunamadı.</p>}
          <div className="library-tip"><strong>Bir bağlantıyla başlayın</strong><p>Düğüm ekleyin, ardından çıkış noktasını diğer düğümün girişine sürükleyin.</p></div>
        </aside>
        {(panel || libraryOpen) && <button className="panel-backdrop" aria-label="Paneli kapat" onClick={() => {setPanel("");setLibraryOpen(false);}}/>}
        <aside className={`inspector ${panel === "test" ? "test-inspector" : ""} ${testExpanded && panel === "test" ? "test-expanded" : ""}`} hidden={!panel} aria-label="Workflow özellikleri">
          <div className="panel-heading"><div><small>ÇALIŞMA ALANI</small><h3>{{edit:"Özellikler",models:"Model ayarları",settings:"Görüşme ayarları",knowledge:"Dosyalar",test:"Test alanı"}[panel]}</h3></div><button aria-label="Özellik panelini kapat" onClick={() => setPanel("")}>×</button></div>
          {panel === "edit" &&
            (node ? (
              <>
                <h3>{labels[node.type!]}</h3>
                <label>
                  Ad
                  <input
                    value={String(node.data.name || "")}
                    onChange={(e) => update({ name: e.target.value })}
                  />
                </label>
                {["agent", "start", "end"].includes(node.type!) && (
                  <label>
                    {flow.settings.llm_enabled === false ? "Düğüm metni · doğrudan seslendirilir" : "Düğüm komutu · sistem mesajı olarak gönderilir"}
                    <textarea
                      rows={7}
                      value={String(node.data.prompt ?? node.data.message ?? "")}
                      onChange={(e) => update({ prompt: e.target.value })}
                    />
                  </label>
                )}
                {node.type === "agent" && (
                  <label>
                    Belge yanıtı
                    <select
                      value={String(node.data.answer_mode || "auto")}
                      onChange={(e) => update({ answer_mode: e.target.value })}
                    >
                      <option value="auto">Otomatik (modelle yanıtla)</option>
                      <option value="quote">Kaynağı doğrudan alıntıla</option>
                      <option value="generate">
                        Model kaynaklardan yanıt üretsin
                      </option>
                    </select>
                  </label>
                )}
                {["tool", "toolResource"].includes(node.type!) && (
                  <label>
                    Araç
                    <select
                      value={String(node.data.tool || "")}
                      onChange={(e) => {
                        update({
                          tool: e.target.value,
                          arguments: caps.tools[e.target.value],
                        });
                        setArgsText(
                          JSON.stringify(caps.tools[e.target.value], null, 2),
                        );
                      }}
                    >
                      {Object.keys(caps?.tools || {}).map((t) => (
                        <option key={t}>{t}</option>
                      ))}
                    </select>
                  </label>
                )}
                {["tool", "toolResource"].includes(node.type!) && (
                  <>
                    <label>
                      Parametreler (JSON)
                      <textarea
                        rows={8}
                        value={argsText}
                        onChange={(e) => setArgsText(e.target.value)}
                        onBlur={() => {
                          try {
                            update({ arguments: JSON.parse(argsText) });
                          } catch {
                            setMessage("Geçersiz JSON parametreleri");
                          }
                        }}
                      />
                    </label>
                    <p>
                      Önceki sonuç: {'{"$ref":"last_result.battery"}'} ·
                      Kullanıcı metni: {'{"$ref":"user"}'}
                    </p>
                    {node.data.tool === "search_documents" && (
                      <label>
                        İzinli koleksiyonlar
                        <select
                          multiple
                          value={(node.data.collection_ids as string[]) || []}
                          onChange={(e) =>
                            update({
                              collection_ids: Array.from(
                                e.target.selectedOptions,
                              ).map((o) => o.value),
                            })
                          }
                        >
                          {collections.map((c) => (
                            <option key={c.id} value={c.id}>
                              {c.name}
                            </option>
                          ))}
                        </select>
                      </label>
                    )}
                  </>
                )}
                {["toolResource", "knowledge"].includes(node.type!) && (
                  <label>
                    Tetikleme ifadeleri
                    <textarea rows={4} placeholder="Her satıra örnek bir kullanıcı ifadesi yazın"
                      value={Array.isArray(node.data.trigger_phrases) ? node.data.trigger_phrases.join("\n") : String(node.data.trigger_phrases || "")}
                      onChange={e => update({trigger_phrases:e.target.value})}/>
                    <small>Kullanıcı bu ifadelere anlamsal olarak yakın konuştuğunda çalışır. Boşsa otomatik seçilmez.</small>
                  </label>
                )}
                {node.type === "knowledge" && (
                  <label>
                    Koleksiyon
                    <select
                      value={String(node.data.collection_id || "")}
                      onChange={(e) =>
                        update({ collection_id: e.target.value })
                      }
                    >
                      <option value="">Seçin</option>
                      {collections.map((c) => (
                        <option key={c.id} value={c.id}>
                          {c.name} · {c.state}
                        </option>
                      ))}
                    </select>
                  </label>
                )}
                {node.type === "condition" && (
                  <>
                    <label>
                      Alan
                      <input
                        value={String(node.data.field || "user")}
                        onChange={(e) => update({ field: e.target.value })}
                      />
                    </label>
                    <label>
                      İşlem
                      <select
                        value={String(node.data.operator || "contains")}
                        onChange={(e) => update({ operator: e.target.value })}
                      >
                        {["eq", "contains", "gt", "lt", "exists"].map((op) => (
                          <option key={op}>{op}</option>
                        ))}
                      </select>
                    </label>
                    <label>
                      Değer
                      <input
                        value={String(node.data.value ?? "")}
                        onChange={(e) =>
                          update({
                            value: ["gt", "lt"].includes(
                              String(node.data.operator),
                            )
                              ? Number(e.target.value)
                              : e.target.value,
                          })
                        }
                      />
                    </label>
                  </>
                )}
                <h4>Çıkışlar</h4>
                {["start", "agent"].includes(node.type!) && <p>{flow.settings.llm_enabled === false ? "LLM kapalıyken node metni doğrudan seslendirilir. Başlangıç çıkışı boşsa ilk metinden sonra otomatik ilerler." : <>Geçişler, bu düğümün ilk LLM yanıtından sonraki kullanıcı mesajlarında değerlendirilir.{node.type === "start" && " Başlangıç çıkışı boşsa ilk yanıttan sonra otomatik ilerler; koşul yazılırsa eşleşme bekler."}</>}</p>}
                {flow.edges
                  .filter((e) => e.source === node.id)
                  .map((edge) => (
                    <label key={edge.id}>
                      {edge.sourceHandle || "Çıkış"} →{" "}
                      {
                        flow.nodes.find((n) => n.id === edge.target)?.data
                          .name as string
                      }
                      <textarea rows={3}
                        aria-label="Geçiş tetikleme ifadeleri"
                        placeholder="Örnek kullanıcı ifadesi (her satıra bir örnek)"
                        value={String(edge.label || "")}
                        onChange={(e) =>
                          change({
                            ...flow,
                            edges: flow.edges.map((x) =>
                              x.id === edge.id
                                ? { ...x, label: e.target.value }
                                : x,
                            ),
                          })
                        }
                      />
                      <button
                        onClick={() =>
                          change({
                            ...flow,
                            edges: flow.edges.filter((x) => x.id !== edge.id),
                          })
                        }
                      >
                        Bağlantıyı sil
                      </button>
                    </label>
                  ))}
                <button
                  disabled={!owner}
                  onClick={() => {
                    const copy = {
                      ...structuredClone(node),
                      id: crypto.randomUUID(),
                      position: {
                        x: node.position.x + 40,
                        y: node.position.y + 80,
                      },
                    };
                    change({ ...flow, nodes: [...flow.nodes, copy] });
                  }}
                >
                  Çoğalt
                </button>
                <button
                  className="danger"
                  disabled={!owner}
                  onClick={() => {
                    change({
                      ...flow,
                      nodes: flow.nodes.filter((n) => n.id !== node.id),
                      edges: flow.edges.filter(
                        (e) => e.source !== node.id && e.target !== node.id,
                      ),
                    });
                    setSelected("");
                  }}
                >
                  Düğümü sil
                </button>
              </>
            ) : (
              <p>Canvas üzerinde bir düğüm seçin.</p>
            ))}
          {panel === "settings" && <VoiceSettings value={flow.settings.voice || {}} fields={caps?.voice_fields} disabled={!owner || busy}
            change={voice => change({...flow, settings:{...flow.settings, voice}})}/>}
          {panel === "models" && (
            <>
              <h3>Yerel modeller</h3>
              <label>
                Dil
                <select
                  value={flow.settings.language || "tr"}
                  onChange={(e) =>
                    change({
                      ...flow,
                      settings: { ...flow.settings, language: e.target.value },
                    })
                  }
                >
                  <option value="tr">Türkçe</option>
                  <option value="en">English</option>
                </select>
              </label>
              <label>
                <input type="checkbox" aria-label="LLM kullan" checked={flow.settings.llm_enabled !== false}
                  onChange={e => change({...flow, settings:{...flow.settings, llm_enabled:e.target.checked}})}/>
                LLM kullan
                <small>Kapalıyken başlangıç, ajan ve bitiş node’larındaki metin doğrudan TTS’ye gönderilir; LLM ve embedding modeli yüklenmez.</small>
              </label>
              {["stt", ...(flow.settings.llm_enabled === false ? ["tts"] : ["llm", "tts", "embedding"])].map((kind) => (
                <label key={kind}>
                  {kind.toUpperCase()}
                  <select
                    value={flow.settings[kind] || ""}
                    onChange={(e) =>
                      change({
                        ...flow,
                        settings: { ...flow.settings, [kind]: e.target.value },
                      })
                    }
                  >
                    <option value="">Model seçin</option>
                    {caps?.models
                      .filter((m: any) => m.kind === kind && m.available)
                      .map((m: any) => (
                        <option key={m.id} value={m.id}>
                          {m.label || m.id}
                        </option>
                      ))}
                  </select>
                </label>
              ))}
              {flow.settings.llm_enabled !== false && <label>
                Anlamsal eşleşme eşiği
                <input type="number" min="0.01" max="1" step="0.01" aria-label="Anlamsal eşleşme eşiği"
                  value={flow.settings.semantic_threshold ?? 0.70}
                  onChange={e => change({...flow, settings:{...flow.settings, semantic_threshold:Number(e.target.value)}})}/>
                <small>Varsayılan 0,70. Geçişler ve bağlı araçlar aynı eşiği kullanır. Bu değer bir olasılık yüzdesi değildir.</small>
              </label>}
              <p>{flow.settings.llm_enabled === false ? "Bu özel akışta node metni değiştirilmeden konuşulur. Araç ve koşul node’ları sabit bağlantılarıyla çalışmaya devam eder." : "Düğüm komutu, etkin olduğu her turda sistem mesajı olarak gönderilir. Kullanıcının mesajı ayrı bir kullanıcı mesajı olarak eklenir. Geçiş için çıkışlara, araç seçimi için kaynak düğümlerine tetikleme ifadeleri yazın."}</p>
            </>
          )}
          {panel === "knowledge" && (
            <>
              <h3>Bilgi koleksiyonları</h3>
              <input
                placeholder="Yeni koleksiyon adı"
                value={collectionName}
                onChange={(e) => setCollectionName(e.target.value)}
              />
              <button
                disabled={!owner || !collectionName}
                onClick={() =>
                  run(async () => {
                    const result = await command("collectionCreate", {
                      name: collectionName,
                      model: flow.settings.embedding,
                    });
                    setCollection(result.id);
                    return result;
                  })
                }
              >
                Koleksiyon oluştur
              </button>
              <select
                value={collection}
                onChange={(e) => {
                  setCollection(e.target.value);
                  setDocs([]);
                  setPreview(null);
                }}
              >
                <option value="">Koleksiyon seçin</option>
                {collections.map((c) => (
                  <option key={c.id} value={c.id}>
                    {c.name} · {c.state}
                  </option>
                ))}
              </select>
              {!!collection && (
                <>
                  <label>
                    Koleksiyon embedding modeli
                    <select
                      disabled={!owner}
                      value={
                        collections.find((c) => c.id === collection)?.model ||
                        ""
                      }
                      onChange={(e) =>
                        run(() =>
                          command("collectionModel", {
                            id: collection,
                            model: e.target.value,
                          }),
                        )
                      }
                    >
                      {caps?.models
                        .filter(
                          (m: any) => m.kind === "embedding" && m.available,
                        )
                        .map((m: any) => (
                          <option key={m.id} value={m.id}>
                            {m.label || m.id}
                          </option>
                        ))}
                    </select>
                  </label>
                  <label>
                    Dosya önizlemesi
                    <select
                      value=""
                      onChange={(e) =>
                        run(async () =>
                          setPreview(
                            await get(
                              `/api/knowledge/${collection}/documents/${e.target.value}/preview`,
                            ),
                          ),
                        )
                      }
                    >
                      <option value="">Dosya seçin</option>
                      {docs.map((d) => (
                        <option key={d.id} value={d.id}>
                          {d.name}
                        </option>
                      ))}
                    </select>
                  </label>
                  {preview && (
                    <section>
                      <b>{preview.name}</b>
                      {preview.records.map((r: any, i: number) => (
                        <details key={i}>
                          <summary>{r.location}</summary>
                          <p>{r.text}</p>
                        </details>
                      ))}
                      {preview.truncated && <p>İlk 20 kayıt gösteriliyor.</p>}
                    </section>
                  )}
                </>
              )}
              {!!collection && (
                <>
                  <p>{collections.find((c) => c.id === collection)?.error}</p>
                  <label>
                    CSV ayırıcı
                    <select
                      value={delimiter}
                      onChange={(e) => setDelimiter(e.target.value)}
                    >
                      <option value="">Otomatik</option>
                      <option value=",">Virgül</option>
                      <option value=";">Noktalı virgül</option>
                      <option value={"\t"}>Tab</option>
                      <option value="|">Dikey çizgi</option>
                    </select>
                  </label>
                  <div
                    className="drop"
                    onDragOver={(e) => e.preventDefault()}
                    onDrop={(e) => {
                      e.preventDefault();
                      if (owner) run(() => upload(e.dataTransfer.files));
                    }}
                  >
                    PDF, CSV, JSON · en fazla 20 MiB
                    <br />
                    <button
                      disabled={!owner}
                      onClick={() => {
                        if (window.ReactNativeWebView)
                          window.ReactNativeWebView.postMessage(
                            JSON.stringify({
                              type: "workflowPickFile",
                              collection,
                              delimiter,
                              token: robot?.workflowToken,
                            }),
                          );
                        else input.current?.click();
                      }}
                    >
                      Dosya yükle
                    </button>
                    <input
                      ref={input}
                      hidden
                      type="file"
                      multiple
                      accept=".pdf,.csv,.json"
                      onChange={(e) => run(() => upload(e.target.files))}
                    />
                  </div>
                  <button
                    disabled={!owner}
                    onClick={() =>
                      run(() => command("reindex", { id: collection }))
                    }
                  >
                    Yeniden indeksle
                  </button>
                  <button
                    disabled={!owner}
                    onClick={() => {
                      if (confirm("Koleksiyon kaldırılsın mı?"))
                        run(() =>
                          command("collectionDelete", { id: collection }),
                        );
                    }}
                  >
                    Koleksiyonu kaldır
                  </button>
                  {docs.map((d) => (
                    <section key={d.id}>
                      <b>{d.name}</b>
                      <p>{JSON.parse(d.warnings || "[]").join("\n")}</p>
                      <button
                        disabled={!owner}
                        onClick={() => {
                          if (
                            confirm("Dosya arama sonuçlarından kaldırılsın mı?")
                          )
                            run(() =>
                              command("documentDelete", {
                                id: collection,
                                document_id: d.id,
                              }),
                            );
                        }}
                      >
                        Dosyayı kaldır
                      </button>
                    </section>
                  ))}
                </>
              )}
            </>
          )}
          {panel === "test" && (
            <div className="test-workspace">
              <div className="test-tabs" role="tablist" aria-label="Test bölümleri">
                {[["live", "Canlı"], ["text", "Metin"], ["recordings", "Kayıtlar"], ["events", "Olaylar"]].map(([key,label]) =>
                  <button key={key} id={`test-tab-${key}`} role="tab" aria-selected={testTab === key} aria-controls={`test-pane-${key}`} tabIndex={testTab === key ? 0 : -1}
                    onClick={() => selectTestTab(key)} onKeyDown={e => {
                      const tabs = ["live", "text", "recordings", "events"];
                      if (["ArrowLeft", "ArrowRight", "Home", "End"].includes(e.key)) {
                        e.preventDefault(); const index = tabs.indexOf(key);
                        const next = e.key === "Home" ? tabs[0] : e.key === "End" ? tabs[3] : tabs[(index + (e.key === "ArrowRight" ? 1 : 3)) % 4];
                        selectTestTab(next); document.getElementById(`test-tab-${next}`)?.focus();
                      }
                    }}>{label}</button>)}
              </div>
              <div className="test-view-tools"><span>{testMode === "live" ? "Ses oturumu" : "Metin oturumu"} · {nodeName(active)}</span><button className="test-expand" aria-label={testExpanded ? "Test panelini daralt" : "Test panelini genişlet"} onClick={() => setTestExpanded(!testExpanded)}>{testExpanded ? "Daralt" : "Genişlet"}</button></div>
              {["live", "text"].includes(testTab) && <div className="test-conversation" role="tabpanel" id={`test-pane-${testTab}`} aria-labelledby={`test-tab-${testTab}`}>
                <div className="test-session-controls">
                  {testTab === "live" ? <>
                    <div className="test-session-actions">
                      <button className="primary" disabled={!owner || busy || liveEngaged || voiceActive || textEngaged} onClick={() => run(startLiveTest)}>{livePhase === "starting" ? "Başlatılıyor…" : "Sesli görüşmeyi başlat"}</button>
                      <button className="danger" disabled={!owner || busy || !liveEngaged || livePhase === "stopping"} onClick={() => run(stopLiveTest)}>{livePhase === "stopping" ? "Sonlandırılıyor…" : "Sesli görüşmeyi sonlandır"}</button>
                    </div>
                    <p className="test-status" role="status">{livePhase === "starting" || livePhase === "stopping" ? liveDetail : livePhase === "running" ? robot?.voiceStatus?.detail || "Ses oturumu açık · Robotun mikrofonuna konuşun" : "Hazır · Başlatıldığında taslak kaydedilir ve etkinleştirilir"}</p>
                    {textEngaged && <p className="test-hint">Sesli görüşmeden önce Metin sekmesindeki testi durdurun.</p>}
                  </> : <>
                    <div className="test-session-actions"><button disabled={!owner || busy || liveEngaged || voiceActive} onClick={() => run(resetTextTest)}>Yeni metin oturumu</button><button className="danger" disabled={!owner || busy || !textEngaged} onClick={() => run(async () => {const result = await command("testStop");setTextPhase("idle");textSnapshot.current="";return result;})}>Metin testini durdur</button></div>
                    <p className="test-status" role="status">{textPhase === "waiting" ? "Yanıt hazırlanıyor…" : textPhase === "ended" ? "Akış tamamlandı · Sonraki mesaj yeni oturum açar" : textPhase === "ready" ? "Sonraki mesajı yazın · Etkin düğüm korunur" : "Taslağı metinle deneyin · Mikrofon ve hoparlör kullanılmaz"}</p>
                    {(liveEngaged || voiceActive) && <p className="test-hint">Metin testi için önce Canlı sekmesinden sesli görüşmeyi sonlandırın.</p>}
                    {textChanged && <p className="test-hint">Akış değişti; sonraki mesaj yeni bir metin oturumu açar.</p>}
                  </>}
                  <label className="test-motion"><input type="checkbox" checked={real} disabled={liveEngaged || textEngaged || busy} onChange={e => setReal(e.target.checked)}/>Gerçek robot hareketleri</label>
                  {testErrors[testTab] && <p className="test-error" role="alert">{testErrors[testTab]}</p>}
                </div>
                <div className="conversation-heading"><h3>{testTab === "live" ? "Canlı konuşma" : "Metin konuşması"}</h3><small>Etkin düğüm: {nodeName(active)}</small></div>
                <div ref={chatLog} className="live-chat" role="log" aria-label={testTab === "live" ? "Canlı konuşmalar" : "Metin test konuşmaları"} aria-live="polite"
                  onScroll={() => {const el=chatLog.current; if(el) followChat.current=el.scrollHeight-el.scrollTop-el.clientHeight<40;}}>
                  {!timeline.length && <div className="conversation-empty"><strong>{testTab === "live" ? "Konuşmayı buradan takip edin" : "İlk test mesajınızı gönderin"}</strong><p>Yanıtlar, düğüm geçişleri, eşleşme puanları ve belge kaynakları burada görünecek.</p></div>}
                  {timeline.map((item: any) => item.role === "activity" ? <div key={item.id} className="chat-activity">
                    <strong>{item.title}</strong><small>{new Date(item.timestamp_ms).toLocaleTimeString()}</small>
                    {["tool_start", "tool_result"].includes(item.trace.type) && <details><summary>Parametreler ve sonuç</summary><pre>{JSON.stringify(item.trace.arguments ?? item.trace.result, null, 2)}</pre></details>}
                    {item.trace.type === "semantic_match" && <small>{item.trace.trigger}</small>}
                  </div> : <div key={item.id} className={`chat-message ${item.role}`}>
                    <strong>{item.role === "user" ? "Sen" : "Kufi"}</strong><p>{item.text}</p>
                    {item.final === false && <small>{item.role === "user" ? "Dinleniyor…" : "Yanıt hazırlanıyor…"}</small>}
                    {!!item.sources?.length && <div className="chat-sources"><small>Belge kaynakları</small>{item.sources.map((source: any, i: number) => <details key={source.chunk_id || i}><summary>{source.filename || "Belge"} · {source.location} {Number.isFinite(source.score) ? `· ${source.score.toFixed(3)}` : ""}</summary><p>{source.text}</p></details>)}</div>}
                  </div>)}
                </div>
                {testTab === "text" && <form className="test-composer" onSubmit={e => {e.preventDefault();run(sendTextTest);}}>
                  <textarea aria-label="Metin test mesajı" rows={2} maxLength={1500} value={testText} onChange={e => setTestText(e.target.value)} placeholder="Geçişi veya belge aramasını deneyecek bir mesaj yazın…" onKeyDown={e => {if (e.key === "Enter" && (e.ctrlKey || e.metaKey)) {e.preventDefault();run(sendTextTest);}}}/>
                  <div><small>Ctrl / ⌘ + Enter · {testText.length}/1500</small><button className="primary" disabled={!owner || busy || textPhase === "waiting" || liveEngaged || voiceActive || !testText.trim()} type="submit">{textPhase === "waiting" ? "Yanıt bekleniyor…" : "Metin testi gönder"}</button></div>
                </form>}
              </div>}
              {testTab === "recordings" && <div className="test-scroll" role="tabpanel" id="test-pane-recordings" aria-labelledby="test-tab-recordings"><RecordingsTable token={robot?.workflowToken} get={get} open={openRecording}/></div>}
              {testTab === "events" && <div className="test-scroll" role="tabpanel" id="test-pane-events" aria-labelledby="test-tab-events"><h3>Olaylar ve kaynaklar</h3><button onClick={() => setEvents(old => old.filter(e => (e.test_mode || "live") !== testMode))}>Günlüğü temizle</button><div role="log" aria-label="Araçlar ve düğüm geçişleri">{visibleEvents.map((event,i) => <section key={i}><b>{event.type}</b>{event.message && <p>{event.message}</p>}{event.event && <pre>{JSON.stringify(event.event,null,2)}</pre>}{event.sources?.map((source:any,j:number) => <details key={source.chunk_id || j}><summary>{source.filename} · {source.location}</summary><p>{source.text}</p></details>)}</section>)}</div></div>}
            </div>
          )}
        </aside>
        <div className="canvas" ref={canvasRef}>
          <div className="canvas-toolbar">
            <button
              aria-label="Geri al" title="Geri al"
              disabled={!owner || busy || !history.current.length}
              onClick={() => {
                const previous = history.current.pop();
                if (previous) {
                  future.current.push(flow);
                  mutationVersion.current++;
                  setFlow({...previous, revision:flow.revision});
                  setDirty(true);
                }
              }}
            >
              ↶
            </button>
            <button
              aria-label="Yinele" title="Yinele"
              disabled={!owner || busy || !future.current.length}
              onClick={() => {
                const next = future.current.pop();
                if (next) {
                  history.current.push(flow);
                  mutationVersion.current++;
                  setFlow({...next, revision:flow.revision});
                  setDirty(true);
                }
              }}
            >
              ↷
            </button>
            <button onClick={() => rf.fitView({padding:0.25,maxZoom:1})}>Sığdır</button>
            <button onClick={arrange} disabled={!owner}>
              Otomatik yerleşim
            </button>
          </div>
          <ReactFlow
            nodes={flow.nodes.map((n) => ({
              ...n,
              data: { ...n.data, active: n.id === active },
            }))}
            edges={flow.edges}
            nodeTypes={nodeTypes}
            colorMode="dark"
            defaultViewport={flow.viewport}
            nodesDraggable={owner}
            nodesConnectable={owner}
            onNodeClick={(_, n) => {
              setSelected(n.id);
              showPanel("edit");
            }}
            onNodesChange={(changes) => {
              if (changes.every(c => c.type === "select" || c.type === "dimensions")) {
                setFlow(previous => ({...previous, nodes:applyNodeChanges(changes, previous.nodes)}));
                return;
              }
              if (owner)
                change(
                  previous => {
                    const nodes = applyNodeChanges(changes, previous.nodes);
                    const ids = new Set(nodes.map(n => n.id));
                    return { ...previous, nodes, edges: previous.edges.filter(e => ids.has(e.source) && ids.has(e.target)) };
                  },
                  changes.some((c) => c.type === "remove"),
                );
            }}
            onNodeDragStart={() => {
              history.current.push(structuredClone(flow));
              future.current = [];
            }}
            onEdgesChange={(changes) => {
              if (changes.every(c => c.type === "select")) {
                setFlow(previous => ({...previous, edges:applyEdgeChanges(changes, previous.edges)}));
                return;
              }
              if (owner)
                change(previous => ({
                  ...previous,
                  edges: applyEdgeChanges(changes, previous.edges).filter(e => previous.nodes.some(n => n.id === e.source) && previous.nodes.some(n => n.id === e.target)),
                }));
            }}
            onConnect={(c: Connection) =>
              change(previous => ({
                ...previous,
                edges: addEdge(
                  {
                    ...c,
                    type: "smoothstep",
                    animated: ["toolResource", "knowledge"].includes(
                      previous.nodes.find((n) => n.id === c.source)?.type || "",
                    ),
                  },
                  previous.edges,
                ),
              }))
            }
            fitView
            fitViewOptions={{padding:0.25,maxZoom:1}}
            minZoom={0.15}
            maxZoom={2}
            deleteKeyCode={owner ? ["Backspace", "Delete"] : null}
          >
            <Background color="#334155" gap={24} />
            <Controls fitViewOptions={{padding:0.25,maxZoom:1}} />
          </ReactFlow>
        </div>
      </div>
      <footer role="status">
        {!owner ? "İzleyici · düzenlemek için kumandayı devralın. " : ""}
        {message ||
          "Düğümleri ekleyin, çıkış noktalarından sürükleyerek bağlayın."}
      </footer>
    </main>
  );
}
// Also handle old open tabs and cached HTML that still load the editor entry.
function openArchiveRoute() {
  if (location.pathname.replace(/\/$/, "") !== "/recordings" && location.hash !== "#recordings") return false;
  const url = new URL(location.href);
  url.pathname = "/recordings";
  url.hash = "";
  url.searchParams.delete("workflow");
  url.searchParams.delete("tab");
  location.replace(url.href);
  return true;
}
window.addEventListener("hashchange", openArchiveRoute);
if (!openArchiveRoute()) {
  createRoot(document.getElementById("root")!).render(
    <ReactFlowProvider>
      <Editor />
    </ReactFlowProvider>,
  );
}
