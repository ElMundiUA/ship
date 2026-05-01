# OpenAI API key

**What it is.** A secret that lets Ship call OpenAI's GPT models on your behalf. You are billed for the usage on your OpenAI account.

**Where to get it.** Sign in at `platform.openai.com`. Go to "API keys" in the sidebar (or navigate directly to `platform.openai.com/api-keys`). Click "Create new secret key", give it a name like "Ship", and copy the value — it starts with `sk-`. You will only see the full value once, so save it before you leave the page.

**Where it goes in Ship.** Settings → Repos → Agent secrets, under the field `OPENAI_API_KEY`. Some teams set it per repository; others set it workspace-wide in organization settings.

**Safety.** OpenAI lets you rotate keys at any time from the same API keys page. Set a monthly billing limit on your OpenAI account; runaway usage from a buggy routine is a real failure mode.

Back to [Appendix index](/docs/appendix)
