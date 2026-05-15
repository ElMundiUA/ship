import express from "express";
import { FeedbackStore } from "./db/feedback_store.js";
import { feedbackRouter } from "./api/feedback.js";

export function buildApp(): express.Express {
  const app = express();
  app.use(express.json());
  const store = new FeedbackStore();
  app.use("/feedback", feedbackRouter(store));
  app.get("/healthz", (_req, res) => res.json({ ok: true }));
  return app;
}

const port = Number(process.env.PORT ?? 3000);
if (import.meta.url === `file://${process.argv[1]}`) {
  buildApp().listen(port, () => {
    console.log(`feedback API listening on :${port}`);
  });
}
