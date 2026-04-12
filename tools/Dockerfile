# docs-mcp-server: single-container MCP server with vector search
FROM python:3.12-slim

WORKDIR /app

# Install build deps for sentence-transformers (optional, for some systems)
RUN apt-get update && apt-get install -y --no-install-recommends \
    build-essential \
    && rm -rf /var/lib/apt/lists/*

# Copy and install Python deps
COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

# Copy data (MD files) and source
COPY data/ /app/data/
COPY src/ /app/src/
COPY chroma_db/ /app/chroma_db/

# Build index at image build time: parse, chunk, vectorize, store in ChromaDB
# Allow skipping during build (e.g., when embeddings require runtime secrets).
ARG BUILD_INDEX=true
ENV DATA_DIR=/app/data
ENV CHROMA_PATH=/app/chroma_db
RUN if [ -f "/app/chroma_db/docs_index.json" ]; then \
      echo "Chroma index found, skipping build"; \
    elif [ "$BUILD_INDEX" = "true" ]; then \
      python -m src.builder; \
    else \
      echo "Skipping index build"; \
    fi

# Default: HTTP (TLS terminated by platform). Set SSL_KEYFILE/SSL_CERTFILE to enable HTTPS explicitly.
ENV HOST=0.0.0.0
ENV PORT=8080
ENV ENABLE_AUTH=false
ENV SSL_KEYFILE=
ENV SSL_CERTFILE=

EXPOSE 8080

CMD ["python", "-m", "src.server"]
