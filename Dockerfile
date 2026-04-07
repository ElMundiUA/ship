# syntax=docker/dockerfile:1
# Static docs: MkDocs build → nginx (port 8080, /health for probes)

FROM python:3.13-alpine AS builder
WORKDIR /src

RUN apk add --no-cache git

COPY requirements-docs.txt mkdocs.yml ./
COPY docs ./docs
COPY hooks ./hooks

RUN pip install --no-cache-dir -r requirements-docs.txt \
    && mkdocs build --clean

FROM nginx:1.27-alpine AS runner

COPY deploy/nginx-default.conf /etc/nginx/conf.d/default.conf
COPY --from=builder /src/site /usr/share/nginx/html

EXPOSE 8080

CMD ["nginx", "-g", "daemon off;"]
