# Ship landing

Next.js front door for the Ship methodology: hero, ElMundi previews, interactive setup studio, and a Together AI image lab (same server-side key pattern as ElMundi).

See `landing/.env.example` and the root `README.md` section **Marketing landing (Next.js)**.

## If you see “Internal Server Error”

1. Run from repo root: `npm install` then `npm run landing:dev` (or build/start the `ship-landing` workspace only from `landing/` with its own `node_modules`).
2. On Vercel / similar: set **Root Directory** to `landing` (or configure the build to use this app’s `package.json`), not the repo root unless your platform understands npm workspaces.
3. Delete a stale build: `rm -rf landing/.next` then `npm run landing:build`.
4. Broken `NEXT_PUBLIC_SITE_URL` used to crash metadata; values are now sanitized, but keep URLs valid (`https://…`) when you set them. The manual is served at `/docs` on the same origin (override `NEXT_PUBLIC_DOCS_URL` only if you split hosts).
5. If a red **global error** page appears, read the message at the bottom — that is the real server error text.
