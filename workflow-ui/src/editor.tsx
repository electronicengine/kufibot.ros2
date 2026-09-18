import React, { useEffect, useRef, useState } from "react";
import { createRoot } from "react-dom/client";
import {
  ReactFlow,
  ReactFlowProvider,
  Background,
  Controls,
  MiniMap,
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

// LAN HTTP is not a secure context: randomUUID is absent, getRandomValues is available.
if (!crypto.randomUUID)
  crypto.randomUUID = () => {
    const bytes = crypto.getRandomValues(new Uint8Array(16));
    bytes[6] = (bytes[6] & 15) | 64;
    bytes[8] = (bytes[8] & 63) | 128;
    const hex = Array.from(bytes, (b) => b.toString(16).padStart(2, "0")).join(
      "",
    );
    return `${hex.slice(0, 8)}-${hex.slice(8, 12)}-${hex.slice(12, 16)}-${hex.slice(16, 20)}-${hex.slice(20)}`;
  };

declare global {
  interface Window {
    ReactNativeWebView?: { postMessage(s: string): void };
    kufibotWorkflowReceive?: (data: any) => void;
  }
}
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
const icons: Record<string, string> = {
  start: "▶",
  agent: "◈",
  condition: "◇",
  tool: "⚙",
  end: "■",
  toolResource: "⚒",
  knowledge: "▤",
};
const pending = new Map<
  string,
  { resolve: (x: any) => void; reject: (e: Error) => void }
>();
let socket: WebSocket | undefined;
let state: any = null;
const seenRuntimeEvents = new Set<string>();
function send(data: any) {
  if (window.ReactNativeWebView)
    window.ReactNativeWebView.postMessage(
      JSON.stringify({ type: "workflowCommand", command: data }),
    );
  else if (window.parent !== window)
    window.parent.postMessage(
      { type: "workflowCommand", command: data },
      location.origin,
    );
  else if (socket?.readyState === WebSocket.OPEN)
    socket.send(JSON.stringify(data));
  else throw new Error("Robot bağlantısı yok");
}
function command(action: string, values: any = {}): Promise<any> {
  const request_id = crypto.randomUUID();
  return new Promise((resolve, reject) => {
    const timeout = setTimeout(() => {
      pending.delete(request_id);
      reject(new Error("İşlem zaman aşımına uğradı"));
    }, 15000);
    pending.set(request_id, {
      resolve: (value) => {
        clearTimeout(timeout);
        resolve(value);
      },
      reject: (error) => {
        clearTimeout(timeout);
        reject(error);
      },
    });
    try {
      send({ type: "workflow", action, request_id, ...values });
    } catch (e) {
      clearTimeout(timeout);
      pending.delete(request_id);
      reject(e);
    }
  });
}
function receive(data: any) {
  if (data.type === "state") {
    state = data;
    window.dispatchEvent(new CustomEvent("robot-state", { detail: data }));
    const runtimeEvents = data.aiConfig?.workflow_events || [data.aiConfig?.workflow_event].filter(Boolean);
    for (const event of runtimeEvents) {
      const key = `${event.session_id}:${event.at}`;
      if (seenRuntimeEvents.has(key)) continue;
      seenRuntimeEvents.add(key);
      if (seenRuntimeEvents.size > 256) seenRuntimeEvents.delete(seenRuntimeEvents.values().next().value!);
      window.dispatchEvent(
        new CustomEvent("workflow-event", {
          detail: ["answer", "transcript"].includes(event.type) ? event : { type: "trace", event, workflow_id: event.workflow_id },
        }),
      );
    }
  }
  if (data.type === "workflowResult") {
    const p = pending.get(data.request_id);
    pending.delete(data.request_id);
    data.error ? p?.reject(new Error(data.error)) : p?.resolve(data.result);
  }
  if (data.type === "workflowEvent")
    window.dispatchEvent(
      new CustomEvent("workflow-event", { detail: data.event }),
    );
  if (data.type === "workflowUpload")
    window.dispatchEvent(new CustomEvent("upload-event", { detail: data }));
}
window.kufibotWorkflowReceive = receive;
window.addEventListener("message", (event) => {
  if (event.origin === location.origin && event.source === window.parent)
    receive(event.data);
});
if (!window.ReactNativeWebView && window.parent === window) {
  socket = new WebSocket(
    `${location.protocol === "https:" ? "wss" : "ws"}://${location.host}/control`,
  );
  socket.onopen = () => send({ type: "claim" });
  socket.onmessage = (e) => receive(JSON.parse(e.data));
  socket.onclose = () => receive({ type: "state", owner: false });
  setInterval(() => {
    if (state?.owner && socket?.readyState === WebSocket.OPEN)
      send({ type: "heartbeat" });
  }, 500);
}
async function get(path: string) {
  const response = await fetch(path, {
    headers: state?.workflowToken
      ? { Authorization: `Bearer ${state.workflowToken}` }
      : {},
  });
  if (!response.ok) throw new Error(await response.text());
  return response.json();
}
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
        <span>{icons[type]}</span>
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
    [docs, setDocs] = useState<any[]>([]),
    [recordings, setRecordings] = useState<any[]>([]);
  const [selected, setSelected] = useState(""),
    [panel, setPanel] = useState("nodes"),
    [message, setMessage] = useState(""),
    [dirty, setDirty] = useState(false);
  const [collection, setCollection] = useState(""),
    [collectionName, setCollectionName] = useState(""),
    [delimiter, setDelimiter] = useState("");
  const [testText, setTestText] = useState(""),
    [real, setReal] = useState(false),
    [events, setEvents] = useState<any[]>([]),
    [active, setActive] = useState("");
  const [argsText, setArgsText] = useState("{}");
  const [testMode, setTestMode] = useState("live");
  const chatLog = useRef<HTMLDivElement>(null);
  const followChat = useRef(true);
  const [preview, setPreview] = useState<any>(null);
  const history = useRef<Workflow[]>([]),
    future = useRef<Workflow[]>([]),
    input = useRef<HTMLInputElement>(null);
  const rf = useReactFlow();
  const flowId = useRef(flow.id);
  flowId.current = flow.id;
  const owner = !!robot?.owner;
  const liveMessages = (robot?.voiceTranscripts || []).filter((item: any) =>
    !item.workflow_id || item.workflow_id === flow.id);
  const chatMessages = testMode === "live" && liveMessages.length ? liveMessages :
    events.filter(e => ["answer", "transcript"].includes(e.type)).map((e, i) => ({
      id: `event-${i}`, role: e.type === "answer" ? "assistant" : e.role || "user", text:e.text, final:true,
      timestamp_ms: e.timestamp_ms,
    }));
  const nodeName = (id: string) => String(flow.nodes.find(n => n.id === id)?.data.name || id || "—");
  const activity = events.flatMap((event, i) => {
    const trace = event.event;
    if (!trace || !["node", "transition", "tool_start", "tool_result"].includes(trace.type)) return [];
    if (trace.name === "transition_node") return [];
    const transition = trace.type === "transition";
    return [{id:`activity-${i}`, role:"activity", timestamp_ms:event.timestamp_ms,
      title: transition ? `Node geçişi: ${nodeName(trace.from_node)} → ${nodeName(trace.node_id)}` :
        trace.type === "node" ? `Etkin node: ${nodeName(trace.node_id)}` :
        `${trace.type === "tool_start" ? "Araç çağrısı" : trace.result?.status === "error" ? "Araç hatası" : "Araç sonucu"}: ${trace.name}`,
      trace}];
  });
  const timeline: any[] = [...chatMessages.filter((item: any) => item.text), ...activity]
    .sort((a: any, b: any) => (a.timestamp_ms || Number(a.id)/1e6 || 0) - (b.timestamp_ms || Number(b.id)/1e6 || 0));
  useEffect(() => {
    if (chatLog.current && followChat.current) chatLog.current.scrollTop = chatLog.current.scrollHeight;
  }, [robot?.voiceTranscripts, events, panel, testMode]);
  function restore(saved: Workflow) {
    seenRuntimeEvents.clear();
    setEvents([]); setActive("");
    const ids = new Set(saved.nodes.map(n => n.id));
    const edges = saved.edges.filter(e => ids.has(e.source) && ids.has(e.target));
    setFlow({...saved, edges});
    setDirty(edges.length !== saved.edges.length);
    if (edges.length !== saved.edges.length)
      setMessage("Silinmiş node’lara ait kopuk bağlantılar taslaktan kaldırıldı. Başlangıcı bir ajana bağlayın ve kaydedin.");
    history.current = [];
    future.current = [];
    rf.setViewport(saved.viewport);
  }
  async function refresh() {
    setList(await get("/api/workflows"));
    setCollections(await get("/api/knowledge/collections"));
    if (state?.workflowToken)
      setRecordings(await get("/api/recordings"));
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
      setEvents((old) => [...old.slice(-99), {...e.detail, timestamp_ms:e.detail.timestamp_ms || e.detail.event?.timestamp_ms || Date.now()}]);
      if (e.detail.type === "trace" && e.detail.event.type === "node")
        setActive(e.detail.event.node_id);
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
    try {
      setMessage("İşleniyor…");
      const result = await action();
      setMessage(result?.errors?.length ? result.errors.join("\n") : "Tamam");
      await refresh();
      return result;
    } catch (e: any) {
      setMessage(e.message);
    }
  }
  async function save() {
    const result = await command("save", {
      workflow: { ...flow, viewport: rf.getViewport() },
    });
    setFlow(result);
    setDirty(false);
    return result;
  }
  function add(kind: string) {
    const id = crypto.randomUUID();
    change({
      ...flow,
      nodes: [
        ...flow.nodes,
        {
          id,
          type: kind,
          position: rf.screenToFlowPosition({
            x: window.innerWidth * 0.5,
            y: window.innerHeight * 0.5,
          }),
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
    setPanel("edit");
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
    setTimeout(() => rf.fitView(), 50);
  }
  const close = () => {
    if (dirty && !confirm("Kaydedilmeyen değişiklikler var. Kapatılsın mı?"))
      return;
    const value = { type: "workflowClose" };
    if (window.ReactNativeWebView)
      window.ReactNativeWebView.postMessage(JSON.stringify(value));
    else if (window.parent !== window)
      window.parent.postMessage(value, location.origin);
    else location.href = "/";
  };
  return (
    <main>
      <header>
        <button onClick={close}>←</button>
        <strong>◈ Local Agent</strong>
        <input
          aria-label="Workflow adı"
          value={flow.name}
          onChange={(e) => change({ ...flow, name: e.target.value })}
        />
        <span className="badge">
          {dirty ? "Taslak •" : "Kaydedildi"} · r{flow.revision}
        </span>
        <button disabled={!owner} onClick={() => run(save)}>
          Kaydet
        </button>
        <button
          disabled={!owner}
          onClick={() => run(() => command("validate", { workflow: flow }))}
        >
          Doğrula
        </button>
        <button
          className="primary"
          disabled={!owner}
          onClick={() =>
            run(async () => {
              const saved = await save();
              return command("activate", { id: saved.id });
            })
          }
        >
          Etkinleştir
        </button>
      </header>
      <nav>
        {[
          ["nodes", "＋ Node"],
          ["edit", "Node ayarları"],
          ["models", "Modeller"],
          ["knowledge", "▤ Dosyalar"],
          ["test", "▷ Test"],
        ].map(([key, label]) => (
          <button
            key={key}
            className={panel === key ? "chosen" : ""}
            onClick={() => setPanel(panel === key ? "" : key)}
          >
            {label}
          </button>
        ))}
      </nav>
      <div className="workspace">
        <aside hidden={!panel}>
          {panel === "nodes" && (
            <>
              <h3>Workflow’lar</h3>
              <select
                value={flow.id}
                onChange={(e) => {
                  if (dirty && !confirm("Taslak değişiklikleri bırakılsın mı?"))
                    return;
                  const next = list.find((w) => w.id === e.target.value);
                  if (next) {
                    restore(next);
                  }
                }}
              >
                <option value={flow.id}>{flow.name}</option>
                {list
                  .filter((w) => w.id !== flow.id)
                  .map((w) => (
                    <option key={w.id} value={w.id}>
                      {w.name}
                    </option>
                  ))}
              </select>
              <button
                disabled={!owner}
                onClick={() => {
                  if (
                    !dirty ||
                    confirm("Taslak değişiklikleri bırakılsın mı?")
                  ) {
                    setFlow(blank(flow.settings));
                    setDirty(true);
                  }
                }}
              >
                Yeni workflow
              </button>
              <h3>Node ekle</h3>
              {Object.entries(labels).map(([kind, label]) => (
                <button
                  key={kind}
                  className={`palette ${kind}`}
                  disabled={!owner}
                  onClick={() => add(kind)}
                >
                  {icons[kind]} {label}
                </button>
              ))}
              <p>
                Kaynak node’unun çıkışını ajanın girişine bağlayın. Akış
                çizgileri yürütme sırasını belirler.
              </p>
            </>
          )}
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
                    Sistem mesajı · davranış talimatı (sesli okunmaz)
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
                      <option value="auto">Otomatik (ufakzeka: alıntı)</option>
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
                {node.type === "tool" && (
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
                {flow.edges
                  .filter((e) => e.source === node.id)
                  .map((edge) => (
                    <label key={edge.id}>
                      {edge.sourceHandle || "Çıkış"} →{" "}
                      {
                        flow.nodes.find((n) => n.id === edge.target)?.data
                          .name as string
                      }
                      <input
                        placeholder="Geçiş açıklaması"
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
                  Node’u sil
                </button>
              </>
            ) : (
              <p>Canvas üzerinde bir node seçin.</p>
            ))}
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
              {["stt", "llm", "tts", "embedding"].map((kind) => (
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
              <label>
                Ortak talimat
                <textarea
                  rows={6}
                  value={flow.settings.system_prompt || ""}
                  onChange={(e) =>
                    change({
                      ...flow,
                      settings: {
                        ...flow.settings,
                        system_prompt: e.target.value,
                      },
                    })
                  }
                />
              </label>
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
            <>
              <h3>Workflow testi</h3>
              <p>Sesli test, bu taslağı kaydedip etkinleştirir ve robotu YZ moduna geçirir. Robotun mikrofonuna konuşun.</p>
              <button disabled={!owner} onClick={() => run(async () => {
                setTestMode("live");
                setEvents([]); setActive("");
                const result = await command("liveStart", {workflow: flow, real});
                setFlow(result.workflow); setDirty(false);
                return result;
              })}>Test et · Sesli görüşmeyi başlat</button>
              <p role="status">{robot?.voiceStatus?.detail || robot?.voiceStatus?.state || "Ses ajanı bekleniyor"}</p>
              <p>Etkin node: <strong>{flow.nodes.find(n => n.id === active)?.data.name as string || active || "—"}</strong></p>
              <h3>Canlı konuşma</h3>
              <div ref={chatLog} className="live-chat" role="log" aria-label="Canlı konuşmalar" aria-live="polite"
                onScroll={() => {const el=chatLog.current; if(el) followChat.current=el.scrollHeight-el.scrollTop-el.clientHeight<40;}}>
                {!timeline.length && <p>Konuşmalar, araç çağrıları ve node geçişleri burada görünecek.</p>}
                {timeline.map((item: any) => item.role === "activity" ? <div key={item.id} className="chat-activity">
                  <strong>{item.title}</strong>
                  <small>{new Date(item.timestamp_ms).toLocaleTimeString()}</small>
                  {["tool_start", "tool_result"].includes(item.trace.type) && <details><summary>Parametreler ve sonuç</summary>
                    <pre>{JSON.stringify(item.trace.arguments ?? item.trace.result, null, 2)}</pre></details>}
                </div> : <div key={item.id} className={`chat-message ${item.role}`}>
                  <strong>{item.role === "user" ? "Sen" : "Kufi"}</strong>
                  <p>{item.text}</p>
                  {item.final === false && <small>{item.role === "user" ? "Dinleniyor…" : "Yanıt hazırlanıyor…"}</small>}
                </div>)}
              </div>
              <h3>Çalıştırma kayıtları</h3>
              <p>Tek WAV dosyasının sol kanalı kullanıcı mikrofonu, sağ kanalı Kufibot’un TTS sesidir.</p>
              <button onClick={() => refresh().catch((e) => setMessage(e.message))}>Kayıtları yenile</button>
              {!recordings.length && <p>Henüz tamamlanmış ses kaydı yok.</p>}
              {recordings.map((recording) => <section key={recording.id}>
                <b>{new Date(recording.created_at * 1000).toLocaleString()}</b>
                <small> · {Math.ceil(recording.duration_sec)} sn · sol: kullanıcı / sağ: Kufi</small>
                <audio controls preload="none" src={`/api/recordings/${encodeURIComponent(recording.id)}?token=${encodeURIComponent(robot?.workflowToken || "")}`} />
              </section>)}
              <p>{caps?.model_notes?.[flow.settings.llm]}</p>
              <textarea
                rows={4}
                value={testText}
                onChange={(e) => setTestText(e.target.value)}
                placeholder="Türkçe bir soru yazın…"
              />
              <label>
                <input
                  type="checkbox"
                  checked={real}
                  onChange={(e) => setReal(e.target.checked)}
                />{" "}
                Gerçek robot hareketleri
              </label>
              <button
                disabled={!owner || !testText}
                onClick={() =>
                  run(async () => {
                    setTestMode("text");
                    setEvents(old => [...old, {type:"transcript",role:"user",text:testText,timestamp_ms:Date.now()}]);
                    return command("test", { workflow: flow, text: testText, real });
                  })
                }
              >
                Metin testi gönder
              </button>
              <button
                disabled={!owner}
                onClick={() => run(() => command("testStop"))}
              >
                Testi durdur
              </button>
              <button onClick={() => setEvents([])}>Günlüğü temizle</button>
              <details><summary>Teknik olay günlüğü ve kaynaklar</summary>
              <div role="log" aria-label="Araçlar ve node geçişleri">
              {events.map((event, i) => (
                <section key={i}>
                  <b>{event.type === "transcript" ? "Sen" : event.type === "answer" ? "Kufi" :
                    event.event?.type === "node" ? `Etkin node: ${flow.nodes.find(n => n.id === event.event.node_id)?.data.name || event.event.node_id}` :
                    event.event?.name === "transition_node" ? `Node geçişi: ${event.event.node_id} → ${event.event.arguments?.target || event.event.result?.node_id}` : event.type}</b>
                  {event.text && !["answer", "transcript"].includes(event.type) && <p>{event.text}</p>}
                  {event.message && <p>{event.message}</p>}
                  {event.sources?.map((s: any) => (
                    <details key={s.chunk_id}>
                      <summary>
                        {s.filename} · {s.location} · {s.score.toFixed(3)}
                      </summary>
                      <p>{s.text}</p>
                    </details>
                  ))}
                  {event.event && (
                    <pre>{JSON.stringify(event.event, null, 2)}</pre>
                  )}
                </section>
              ))}
              </div>
              </details>
            </>
          )}
        </aside>
        <div className="canvas">
          <div className="canvas-toolbar">
            <button
              disabled={!owner || !history.current.length}
              onClick={() => {
                const previous = history.current.pop();
                if (previous) {
                  future.current.push(flow);
                  setFlow(previous);
                  setDirty(true);
                }
              }}
            >
              ↶
            </button>
            <button
              disabled={!owner || !future.current.length}
              onClick={() => {
                const next = future.current.pop();
                if (next) {
                  history.current.push(flow);
                  setFlow(next);
                  setDirty(true);
                }
              }}
            >
              ↷
            </button>
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
            defaultViewport={flow.viewport}
            nodesDraggable={owner}
            nodesConnectable={owner}
            onNodeClick={(_, n) => {
              setSelected(n.id);
              setPanel("edit");
            }}
            onNodesChange={(changes) => {
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
            minZoom={0.15}
            maxZoom={2}
            deleteKeyCode={owner ? ["Backspace", "Delete"] : null}
          >
            <Background color="#334155" gap={24} />
            <Controls />
            <MiniMap pannable zoomable />
          </ReactFlow>
        </div>
      </div>
      <footer role="status">
        {!owner ? "İzleyici · düzenlemek için kumandayı devralın. " : ""}
        {message ||
          "Node’ları ekleyin, çıkış noktalarından sürükleyerek bağlayın."}
      </footer>
    </main>
  );
}
createRoot(document.getElementById("root")!).render(
  <ReactFlowProvider>
    <Editor />
  </ReactFlowProvider>,
);
