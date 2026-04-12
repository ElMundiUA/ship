# ElMundi screenshots (optional)

Add PNG captures to this folder:

- `elmundi-board.png` — tracker board (Linear-style columns).
- `elmundi-pr.png` — pull request + agent / QA thread.

Then set in `landing/.env.local`:

```bash
NEXT_PUBLIC_USE_ELMUNDI_SCREENSHOTS=true
```

Rebuild or restart `next dev` so the flag is picked up.
