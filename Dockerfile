# syntax=docker/dockerfile:1
# Next.js (landing + `/docs` manual). `REPO_ROOT=/app` so server reads `documentation/` beside `landing/`.

FROM node:20-alpine
WORKDIR /app

RUN apk add --no-cache libc6-compat

# Install + build need devDependencies (e.g. TypeScript for next.config.ts); set production after build.
ENV NEXT_TELEMETRY_DISABLED=1
ENV REPO_ROOT=/app
ENV HOSTNAME=0.0.0.0

COPY package.json package-lock.json ./
COPY landing/package.json ./landing/
COPY cli/package.json ./cli/

RUN npm ci

COPY landing ./landing
COPY documentation ./documentation
COPY prompts ./prompts
COPY patterns ./patterns
COPY tools ./tools
COPY workflows ./workflows
COPY collections ./collections
COPY backend ./backend

RUN npm run landing:build

ENV NODE_ENV=production
# Bunny MC template maps CDN → container port 8080 (scripts/bunny-ship-docs.mjs).
ENV PORT=8080

EXPOSE 8080

CMD ["npm", "run", "landing:start"]
