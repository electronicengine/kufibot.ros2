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
const pending = new Map<
  string,
  { resolve: (x: any) => void; reject: (e: Error) => void }
>();
let socket: WebSocket | undefined;
export let state: any = null;
export const seenRuntimeEvents = new Set<string>();
export function send(data: any) {
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
export function command(action: string, values: any = {}): Promise<any> {
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
  if (!data || typeof data !== "object") return;
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
          detail: { ...(["answer", "transcript"].includes(event.type) ? event : { type: "trace", event, workflow_id: event.workflow_id }), test_mode: "live" },
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
export async function get(path: string) {
  const response = await fetch(path, {
    headers: state?.workflowToken
      ? { Authorization: `Bearer ${state.workflowToken}` }
      : {},
  });
  if (!response.ok) throw new Error(await response.text());
  return response.json();
}
