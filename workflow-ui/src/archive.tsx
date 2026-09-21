import React, { useEffect, useState } from "react";
import { createRoot } from "react-dom/client";
import { state, get } from "./transport";
import { RecordingsTable, RecordingResult } from "./recordings";
import "./style.css";

function RecordingArchive() {
  const [robot, setRobot] = useState<any>(state);
  const [id, setId] = useState(() => new URLSearchParams(location.search).get("recording") || "");
  useEffect(() => {
    const onState = (event: any) => setRobot(event.detail);
    const onPop = () => setId(new URLSearchParams(location.search).get("recording") || "");
    window.addEventListener("robot-state", onState);
    window.addEventListener("popstate", onPop);
    return () => { window.removeEventListener("robot-state", onState); window.removeEventListener("popstate", onPop); };
  }, []);
  function navigate(recording: string) {
    const url = new URL(location.href);
    if (recording) url.searchParams.set("recording", recording);
    else url.searchParams.delete("recording");
    window.history.pushState({}, "", url);
    setId(recording);
  }
  if (id) return <RecordingResult key={id} id={id} token={robot?.workflowToken} get={get} back={() => navigate("")}/>;
  return <main className="recording-result recording-archive" lang="tr">
    {window.parent === window && <header className="recording-result-header"><a className="recording-back" href="/#recordings">← Kontrol merkezine dön</a><span className="recording-eyebrow">KUFIBOT / KAYITLAR</span></header>}
    <div className="recording-result-body"><section className="recording-history">
      <RecordingsTable token={robot?.workflowToken} get={get} open={navigate} title="Tüm yerel görüşmeler"/>
      <p className="recording-archive-note">Test oturumları ve ses ajanıyla yapılan diğer tüm tamamlanmış yerel görüşmeler burada listelenir.</p>
    </section></div>
  </main>;
}
createRoot(document.getElementById("root")!).render(<RecordingArchive/>);
