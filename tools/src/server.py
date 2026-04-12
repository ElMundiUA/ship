"""
MCP server with search and fetch tools.
Optional auth, HTTPS support.
"""
import json
import os
from pathlib import Path

import chromadb
from chromadb.config import Settings
from dotenv import load_dotenv

from src.embeddings import get_embedding_function
from mcp.server.fastmcp import FastMCP
from starlette.middleware import Middleware
from starlette.requests import Request
from starlette.responses import JSONResponse

load_dotenv()

# Config from env
CHROMA_PATH = Path(os.environ.get("CHROMA_PATH", "/app/chroma_db"))
ENABLE_AUTH = os.environ.get("ENABLE_AUTH", "false").lower() in ("true", "1", "yes")
API_KEY = os.environ.get("API_KEY", "")
SSL_KEYFILE = os.environ.get("SSL_KEYFILE", "")
SSL_CERTFILE = os.environ.get("SSL_CERTFILE", "")
HOST = os.environ.get("HOST", "0.0.0.0")
PORT = int(os.environ.get("PORT", "8000"))

# Lazy-loaded
_collection = None
_docs_index: dict[str, str] | None = None


def _get_collection():
    global _collection
    if _collection is None:
        ef = get_embedding_function()
        client = chromadb.PersistentClient(
            path=str(CHROMA_PATH),
            settings=Settings(anonymized_telemetry=False),
        )
        _collection = client.get_collection(name="docs", embedding_function=ef)
    return _collection


def _get_docs_index() -> dict[str, str]:
    global _docs_index
    if _docs_index is None:
        idx_path = CHROMA_PATH / "docs_index.json"
        if not idx_path.exists():
            _docs_index = {}
        else:
            _docs_index = json.loads(idx_path.read_text(encoding="utf-8"))
    return _docs_index


def _check_auth(request: Request) -> bool:
    if not ENABLE_AUTH or not API_KEY:
        return True
    auth = request.headers.get("Authorization")
    if auth and auth.startswith("Bearer "):
        token = auth[7:]
        return token == API_KEY
    api_key = request.headers.get("X-API-Key")
    return api_key == API_KEY if api_key else False


# Create FastMCP app
mcp = FastMCP(
    name="docs-mcp-server",
    host=HOST,
    port=PORT,
    streamable_http_path="/mcp",
    json_response=True,
)


@mcp.tool()
def search(query: str, top_k: int = 5) -> str:
    """Semantic search over documents. Returns top 5 most relevant chunks by default.
    Use this to find information across the document collection."""
    coll = _get_collection()
    results = coll.query(query_texts=[query], n_results=min(top_k, 20))
    if not results or not results["documents"] or not results["documents"][0]:
        return "No results found."

    docs = results["documents"][0]
    metadatas = results["metadatas"][0] if results.get("metadatas") else [{}] * len(docs)
    distances = results["distances"][0] if results.get("distances") else [0] * len(docs)

    lines = []
    for i, (doc, meta, dist) in enumerate(zip(docs, metadatas, distances), 1):
        doc_id = meta.get("doc_id", "unknown")
        lines.append(f"--- Result {i} (doc: {doc_id}, score: {1 - dist / 2:.2f}) ---\n{doc}")

    return "\n\n".join(lines)


@mcp.tool()
def fetch(doc_id: str) -> str:
    """Fetch full document by ID. Use doc_id from search results to read the complete document."""
    index = _get_docs_index()
    if doc_id not in index:
        available = ", ".join(sorted(index.keys())[:20])
        if len(index) > 20:
            available += f" ... and {len(index) - 20} more"
        return f"Document '{doc_id}' not found. Available: {available}"
    return index[doc_id]


# Add health route
@mcp.custom_route("/health", methods=["GET"])
async def health(request: Request):
    return JSONResponse({"status": "ok", "auth_enabled": ENABLE_AUTH})

@mcp.custom_route("/", methods=["GET"])
async def root(request: Request):
    return JSONResponse(
        {
            "service": "docs-mcp-server",
            "status": "ok",
            "message": "Hello from Azure Web App",
        }
    )


def run_server():
    """Run with optional SSL and auth."""
    import uvicorn
    from starlette.applications import Starlette
    from starlette.middleware import Middleware
    from starlette.middleware.base import BaseHTTPMiddleware

    app = mcp.streamable_http_app()

    if ENABLE_AUTH and API_KEY:

        class AuthMiddleware(BaseHTTPMiddleware):
            async def dispatch(self, request: Request, call_next):
                if not _check_auth(request):
                    return JSONResponse({"error": "Unauthorized"}, status_code=401)
                return await call_next(request)

        app = Starlette(
            debug=False,
            routes=app.routes,
            middleware=[Middleware(AuthMiddleware)],
            lifespan=app.router.lifespan_context if hasattr(app.router, "lifespan_context") else None,
        )

    ssl_keyfile = SSL_KEYFILE if SSL_KEYFILE and os.path.isfile(SSL_KEYFILE) else None
    ssl_certfile = SSL_CERTFILE if SSL_CERTFILE and os.path.isfile(SSL_CERTFILE) else None

    uvicorn.run(
        app,
        host=HOST,
        port=PORT,
        log_level="info",
        ssl_keyfile=ssl_keyfile,
        ssl_certfile=ssl_certfile,
    )


if __name__ == "__main__":
    run_server()
