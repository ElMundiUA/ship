// Tiny Express API. Intentionally "janky but works":
//  - binds 127.0.0.1 by default (HOST env overrides) -> on a container this is
//    unreachable from outside unless something injects HOST=0.0.0.0.
//  - health route is /healthz (not /health), so the planner has to actually
//    look, not guess.
const express = require("express");
const cors = require("cors");

const app = express();
app.use(cors());

const PORT = process.env.PORT || 5000;
const HOST = process.env.HOST || "127.0.0.1"; // localhost-only unless told otherwise

app.get("/healthz", (req, res) => {
  res.json({ ok: true, service: "api", ts: Date.now() });
});

app.get("/api/hello", (req, res) => {
  res.json({ msg: "hello from the janky api" });
});

app.listen(PORT, HOST, () => {
  console.log(`api listening on http://${HOST}:${PORT}`);
});
