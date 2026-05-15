import { Router } from "express";
import type { FeedbackStore } from "../db/feedback_store.js";

export function feedbackRouter(store: FeedbackStore): Router {
  const router = Router();

  router.get("/", (_req, res) => {
    res.json({ items: store.list() });
  });

  router.post("/", (req, res) => {
    const { author, message } = req.body ?? {};
    if (typeof author !== "string" || typeof message !== "string") {
      res.status(400).json({ error: "author and message required" });
      return;
    }
    const row = store.add({ author, message });
    res.status(201).json(row);
  });

  router.get("/:id", (req, res) => {
    const row = store.get(req.params.id);
    if (!row) {
      res.status(404).json({ error: "not found" });
      return;
    }
    res.json(row);
  });

  router.delete("/:id", (req, res) => {
    const ok = store.remove(req.params.id);
    res.status(ok ? 204 : 404).end();
  });

  return router;
}
