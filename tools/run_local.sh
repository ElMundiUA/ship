#!/bin/bash
# Local run (without Docker). Build index first, then start server.
# Uses HTTP by default (no SSL). For HTTPS use Docker.
set -e
cd "$(dirname "$0")"

export DATA_DIR="${DATA_DIR:-./data}"
export CHROMA_PATH="${CHROMA_PATH:-./chroma_db}"
export HOST="${HOST:-127.0.0.1}"
export PORT="${PORT:-8000}"
export SSL_KEYFILE=""  # No SSL for local
export SSL_CERTFILE=""

# Build index if not exists
if [ ! -d "$CHROMA_PATH" ] || [ ! -f "$CHROMA_PATH/docs_index.json" ]; then
  echo "Building index..."
  python -m src.builder
fi

echo "Starting server at http://$HOST:$PORT/mcp"
python -m src.server
