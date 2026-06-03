// JANK: the env name has a typo ("RUL" instead of "URL"). The code reads the
// same typo'd name, so it WORKS — it's just badly named. This is the "human
// durost" case: the planner can't rely on the name being sensible, it has to
// understand by meaning that this is the backend base URL. Defaults to
// localhost, so a naive deploy points at nothing.
//
// This is a standalone frontend: there's no backend in the same app, so the
// planner should treat this as a required env to be pointed at the separately
// deployed backend (NOT a same-app $APP_URL self-reference).
const API = import.meta.env.VITE_API_RUL || "http://localhost:8000";

document.getElementById("base").textContent = API;

document.getElementById("btn").addEventListener("click", async () => {
  const statusEl = document.getElementById("status");
  const detailEl = document.getElementById("detail");
  statusEl.textContent = "checking";
  detailEl.textContent = "";
  try {
    const r = await fetch(`${API}/health`);
    const j = await r.json();
    statusEl.textContent = r.ok ? "ok" : "fail";
    statusEl.style.color = r.ok ? "green" : "crimson";
    detailEl.textContent = JSON.stringify(j, null, 2);
  } catch (e) {
    statusEl.textContent = "fail";
    statusEl.style.color = "crimson";
    detailEl.textContent = String(e);
  }
});
