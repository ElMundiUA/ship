# Anthropic API key

**What it is.** A secret that lets Ship call Anthropic's Claude models on your behalf. Same shape as the OpenAI key.

**Where to get it.** Sign in at `console.anthropic.com`. Go to "API Keys" (or navigate directly to `console.anthropic.com/settings/keys`). Create a new key, give it a name like "Ship", and copy the value.

**Where it goes in Ship.** Settings → Repos → Agent secrets, under the field `ANTHROPIC_API_KEY`.

**Safety.** You can rotate keys at any time from the same page. Set a usage cap on your Anthropic account to protect against runaway charges.

Back to [Appendix index](/docs/appendix)
