import React, { useState } from "react";

// JANK: non-standard env name + a hardcoded localhost fallback. A human wired
// the dashboard to the api by typing the dev URL and never came back to it.
// The deploy planner is supposed to (a) realise this is the backend base even
// though it isn't called VITE_API_URL, and (b) point it at the deployed api
// (via $APP_URL) instead of localhost.
const API_BASE = import.meta.env.VITE_BACKEND_BASE || "http://localhost:5000";

export default function App() {
  const [status, setStatus] = useState("idle");
  const [detail, setDetail] = useState("");

  async function checkBackend() {
    setStatus("checking");
    setDetail("");
    try {
      const r = await fetch(`${API_BASE}/healthz`);
      const j = await r.json();
      setStatus(r.ok ? "ok" : "fail");
      setDetail(JSON.stringify(j, null, 2));
    } catch (e) {
      setStatus("fail");
      setDetail(String(e));
    }
  }

  return (
    <div style={{ fontFamily: "system-ui, sans-serif", padding: 40, maxWidth: 640 }}>
      <h1>Janky Dashboard</h1>
      <p>
        Talking to API at: <code>{API_BASE}</code>
      </p>
      <button onClick={checkBackend} style={{ padding: "8px 16px", fontSize: 16 }}>
        Check backend health
      </button>
      <p>
        Backend status:{" "}
        <b style={{ color: status === "ok" ? "green" : status === "fail" ? "crimson" : "#888" }}>
          {status}
        </b>
      </p>
      {detail && <pre>{detail}</pre>}
    </div>
  );
}
