"""Standalone janky backend (FastAPI).

Single-service repo — no frontend in here. Used to test that the planner
produces ONE service component (not an invented frontend), picks the right
health path, and that HOST=0.0.0.0 gets injected (the Dockerfile defaults
uvicorn to 127.0.0.1).

CORS is wide open so the separately-deployed frontend can call it.
"""

from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware

app = FastAPI(title="janky-backend")

app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_methods=["*"],
    allow_headers=["*"],
)


@app.get("/health")
def health():
    return {"ok": True, "service": "janky-backend"}


@app.get("/api/ping")
def ping():
    return {"pong": True}
