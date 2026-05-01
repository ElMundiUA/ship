# Notion integration token

**What it is.** Notion uses "internal integrations" rather than traditional API keys. You create an integration in Notion's developer settings, share specific pages or databases with it, and paste its token into Ship.

**Where to get it.** Sign in at `notion.so`. Go to `notion.so/profile/integrations` (or Settings → Integrations → Develop your own). Click "New integration", give it a name like "Ship", and copy the "Internal Integration Token" — it looks like `secret_...` or starts with `ntn_`. Then go to each Notion page or database you want Ship to access, click "Share", and add your integration by name.

**Where it goes in Ship.** Settings → Integrations → Notion → token field.

**Safety.** Scope narrowly — share only the specific pages or databases that Ship needs, never your entire workspace. You can revoke an integration's access at any time from the Notion integrations page.

Back to [Appendix index](/docs/appendix)
