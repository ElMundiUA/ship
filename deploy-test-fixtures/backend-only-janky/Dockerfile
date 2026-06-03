FROM python:3.12-slim
WORKDIR /app
COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt
COPY . .
EXPOSE 8000
# JANK: host defaults to 127.0.0.1 unless HOST is provided. On a container that
# is unreachable from outside — the planner should inject HOST=0.0.0.0.
CMD ["sh", "-c", "uvicorn src.main:app --host ${HOST:-127.0.0.1} --port ${PORT:-8000}"]
