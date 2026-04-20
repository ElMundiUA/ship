# syntax=docker/dockerfile:1
# Next.js (landing + `/docs` manual + `/book`). The Next app reads
# `documentation/`, `artifacts/` and `VERSION` straight off disk via
# `REPO_ROOT=/app`, so we copy the whole working tree (minus what
# `.dockerignore` excludes) rather than enumerating moving subfolders.

FROM node:20-alpine
WORKDIR /app

RUN apk add --no-cache libc6-compat

# Install + build need devDependencies (e.g. TypeScript for next.config.ts); set production after build.
ENV NEXT_TELEMETRY_DISABLED=1
ENV REPO_ROOT=/app
ENV HOSTNAME=0.0.0.0

COPY package.json package-lock.json VERSION ./
COPY landing/package.json ./landing/
COPY cli/package.json ./cli/

RUN npm ci

COPY landing ./landing
COPY documentation ./documentation
COPY artifacts ./artifacts
COPY backend ./backend
COPY scripts ./scripts
# `/cli` page in the landing reads cli/README.md at build time via
# readFileSync (see landing/src/app/cli/page.tsx::readCliReadme). We
# already COPY cli/package.json above for `npm ci`; the README has to
# land in the workspace before `next build` runs.
COPY cli ./cli

RUN npm run landing:build

ENV NODE_ENV=production
# Bunny MC template maps CDN → container port 8080 (scripts/bunny-ship-docs.mjs).
ENV PORT=8080

EXPOSE 8080

CMD ["npm", "run", "landing:start"]
