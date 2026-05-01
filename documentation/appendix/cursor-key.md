# Cursor API key

**What it is.** A key for the Cursor IDE or Cursor cloud agent path. Only needed if your team uses Cursor as one of its agents.

**Where to get it.** Open Cursor and go to Settings. Look for the API keys or authentication section. Cursor's exact UI changes between versions, so check [Cursor's official documentation](https://docs.cursor.sh) if you don't see it immediately.

**Where it goes in Ship.** Settings → Repos → Agent secrets, under the field `CURSOR_API_KEY`.

**Safety.** Rotate when team members leave; the key is tied to an individual Cursor account.

Back to [Appendix index](/docs/appendix)
