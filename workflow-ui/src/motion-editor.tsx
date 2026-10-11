import React, {useEffect, useRef, useState} from "react";

export function MotionEditor({initial, state, apply, close}: {
  initial: any; state: any; apply: (value: any, pose: boolean) => void; close: () => void;
}) {
  const frame = useRef<HTMLIFrameElement>(null);
  const [pose, setPose] = useState(!initial || initial.keyframes?.length === 1);
  const poseRef = useRef(pose); poseRef.current = pose;
  const stateRef = useRef(state); stateRef.current = state;
  useEffect(() => {
    const receive = (event: MessageEvent) => {
      if (event.origin !== location.origin || event.source !== frame.current?.contentWindow) return;
      if (event.data?.type === "mimicReady") frame.current?.contentWindow?.postMessage({
        type: "mimicWorkflowInit", motion: initial, state: stateRef.current,
      }, location.origin);
      if (event.data?.type === "mimicApply" && stateRef.current?.owner) apply(poseRef.current ? {...event.data.motion, keyframes:[{time_ms:0,joints:event.data.pose}]} : event.data.motion, poseRef.current);
      if (event.data?.type === "mimicClose") close();
    };
    const escape = (event: KeyboardEvent) => {
      if (event.key === "Escape") {
        event.preventDefault(); event.stopImmediatePropagation();
        frame.current?.contentWindow?.postMessage({type:"mimicRequestClose"}, location.origin);
      }
    };
    window.addEventListener("message", receive);
    window.addEventListener("keydown", escape, true);
    return () => {window.removeEventListener("message", receive); window.removeEventListener("keydown", escape, true);};
  }, []);
  useEffect(() => {
    frame.current?.contentWindow?.postMessage({type:"mimicState", state}, location.origin);
  }, [state]);
  return <div className="motion-editor-overlay" role="dialog" aria-modal="true" aria-label="Workflow hareket editörü">
    <label>Hareket biçimi <select aria-label="Hareket biçimi" value={pose ? "pose" : "sequence"} onChange={e => setPose(e.target.value === "pose")}>
      <option value="pose">Tek poz · seçili adımın açıları</option><option value="sequence">Hareket dizisi · tüm zaman adımları</option>
    </select></label>
    <iframe ref={frame} src="/mimics?workflow=1" title="Workflow 3D hareket editörü"/>
  </div>;
}

export function CameraResult({result}: {result: any}) {
  const image = result?.image;
  return image?.mime_type === "image/jpeg" && typeof image.base64 === "string"
    ? <img className="workflow-camera-result" src={`data:image/jpeg;base64,${image.base64}`} alt="Robotun kamera görüntüsü"/> : null;
}
