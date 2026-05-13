# syntax=docker/dockerfile:1
# Ship console (operator UI). Built once, run as the `console` service in
# docker-compose. Marketing landing/ is intentionally NOT in compose — it
# lives on the public web. This Dockerfile only ships the operator surface.

# ----------------------------------------------------------------------
# 1. deps — install once, layer-cache friendly
# ----------------------------------------------------------------------
FROM node:20-alpine AS deps
WORKDIR /app

COPY console/package.json console/package-lock.json* ./
RUN npm ci

# ----------------------------------------------------------------------
# 2. builder — compile Next.js to standalone output
# ----------------------------------------------------------------------
FROM node:20-alpine AS builder
WORKDIR /app

COPY --from=deps /app/node_modules ./node_modules
COPY console/ ./

ENV NEXT_TELEMETRY_DISABLED=1
RUN npm run build

# ----------------------------------------------------------------------
# 3. runner — minimal runtime image
# ----------------------------------------------------------------------
FROM node:20-alpine AS runner
WORKDIR /app

ENV NODE_ENV=production \
    NEXT_TELEMETRY_DISABLED=1 \
    PORT=3001 \
    HOSTNAME=0.0.0.0

# Non-root user — Next docs example
RUN addgroup --system --gid 1001 nodejs \
 && adduser --system --uid 1001 --ingroup nodejs nextjs

COPY --from=builder --chown=nextjs:nodejs /app/public ./public
COPY --from=builder --chown=nextjs:nodejs /app/.next/standalone ./
COPY --from=builder --chown=nextjs:nodejs /app/.next/static ./.next/static

USER nextjs
EXPOSE 3001

CMD ["node", "server.js"]
