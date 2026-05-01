# Integrations beyond the tracker

The tracker you bound in the wizard — Linear, Jira, GitHub Issues, GitLab, or Azure DevOps — defines the schema for your Ship workspace: it is the system of record for work and its field structure. But Ship is not just a read-only mirror. Ship also connects to auxiliary systems: Notion for knowledge ingest, Slack or Teams for daily output and alerts, and telemetry sinks like OpenTelemetry, S3, or custom webhooks for observability. This chapter walks through what each integration is for and how to wire it from Settings → Integrations.

## Knowledge sources

### Notion

Ship can ingest pages and databases from Notion as a knowledge source—the raw material for clarification notes and decision logs that flow through the distiller. Authentication requires a Notion internal integration token (usually prefixed `secret_` or `ntn_`) that you create in your Notion workspace settings. Once connected, you choose which databases or page collections to share with Ship; the integration reads only from what you explicitly grant access to, so scope it narrowly to the resources that belong in your knowledge bucket rather than sharing your entire workspace.

Notion is commonly used as both a tracker and a knowledge source in the same workspace; if your team runs the backlog in Notion, you can connect it as a tracker to populate the work schema. For most teams, though, the better pattern is to treat Notion as a knowledge source first—a place where past decisions, architecture notes, and operational runbooks live—and let the tracker stay in Linear, Jira, or GitHub where the work queue lives.

## Comms

### Slack

Ship can post the daily digest, retro summaries, and high-severity alerts to a Slack channel of your choice. Authentication requires a bot token (prefixed `xoxb-`) and the name of the channel where you want the output (e.g., `#ship-daily`). The daily digest is a summary of what moved through the workflow today; retro summaries collate any retrospectives that ran; alerts fire when an event meets a severity threshold you define.

The strongest pattern is to dedicate a channel to Ship output rather than cross-posting into `#general` or a shared channel. A channel that exists specifically to be muted—checked once a day or skipped some days—is healthy; a channel that is supposed to be read but becomes full of noise gets muted by accident, and then nobody sees the alerts that actually matter.

### Microsoft Teams

Ship can post to Teams in the same way—digest, retro summaries, and alerts. Authentication is heavier than Slack because each Teams tenant has a different flow: you either register a webhook URL and grant it the right permissions in your tenant, or you provide an app password and team ID. The webhook approach is usually simpler; allow extra time in your first setup to confirm that the permissions are right and that the webhook can actually post to the channel you choose.

## Telemetry

### OpenTelemetry

If your organization already sends traces and metrics to an observability platform—Honeycomb, Datadog, Tempo, New Relic, or another vendor—Ship can forward its internal events as OpenTelemetry spans. Authentication requires an OTLP endpoint URL and a bearer token or API key for the platform that receives the spans. This is useful if you want to correlate Ship's events with the rest of your infrastructure, or if you want to build custom panels and queries on top of the event stream. The Ship dashboard is opinionated about what's worth showing; the OTLP feed is for teams who want full flexibility.

### Custom webhook

Ship can POST events to a custom webhook endpoint that you own. Authentication requires an endpoint URL and an HMAC signing secret; Ship signs each payload with the secret so that the receiver can verify the request came from your workspace and not from an attacker. The webhook is fire-and-forget: each event class (work created, clarification distilled, alert fired, and so on) triggers a separate POST, so the receiver can stay stateless and process events as they arrive. This pattern is useful for lightweight integrations where you want Ship to be one of many systems sending data into a collector or aggregator.

### S3 export

Ship can write event records to Amazon S3 on a regular schedule—one JSONL file per hour, dropped into a bucket you control. Authentication requires the bucket name, the region it lives in, and an access key secret with permission to write objects. This is the right choice if you want offline analytics on Ship's events, if you need archival for compliance reasons, or if you have a data warehouse that prefers to ingest from files rather than from streams. The JSONL format is human-readable and bulk-import-friendly.

## Secrets and credential rotation

Every credential you paste into the Integrations panel—Notion tokens, Slack bot tokens, API keys for S3 or OpenTelemetry—lives in your workspace's encrypted secret store, not in the `.ship/config.yml` file that lives in your repository. Rotating a credential is a settings change, not a code change; you replace it in the Integrations UI, and the new credential takes effect immediately. If a credential leaks or is compromised, first revoke it on the provider side (disable the token in Notion settings, regenerate the Slack bot token, delete the S3 access key), and then replace it in Ship—this order ensures that the old token cannot be used even if a revocation process is slow.
