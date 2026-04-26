type MailosaurAddress = { email?: string; name?: string };
type MailosaurLink = { href?: string; text?: string };
type MailosaurBody = { body?: string; links?: MailosaurLink[] };

export type MailosaurMessage = {
  id: string;
  subject?: string;
  received?: string;
  sentTo?: MailosaurAddress[];
  from?: MailosaurAddress[];
  html?: MailosaurBody;
  text?: MailosaurBody;
};

type SearchResponse = { items?: MailosaurMessage[] };

const MAILOSAUR_API = "https://mailosaur.com/api";

export function mailosaurConfigured(): boolean {
  return Boolean(
    process.env.MAILOSAUR_API_KEY?.trim() &&
      process.env.MAILOSAUR_SERVER_ID?.trim(),
  );
}

export function mailosaurRunId(): string {
  return (
    process.env.E2E_RUN_ID?.trim() ||
    process.env.GITHUB_RUN_ID?.trim() ||
    Date.now().toString(36)
  ).replace(/[^a-zA-Z0-9-]/g, "-");
}

export function uniqueMailosaurEmail(prefix = "ship-e2e"): string {
  const serverId = requireEnv("MAILOSAUR_SERVER_ID");
  const domain =
    process.env.E2E_EMAIL_DOMAIN?.trim() || `${serverId}.mailosaur.net`;
  const local = `${prefix}.${mailosaurRunId()}.${Math.random()
    .toString(36)
    .slice(2, 8)}`;
  return `${local}@${domain}`;
}

export async function waitForMailosaurMessage(opts: {
  sentTo: string;
  subject?: RegExp;
  timeoutMs?: number;
  intervalMs?: number;
}): Promise<MailosaurMessage> {
  const started = Date.now();
  const timeoutMs = opts.timeoutMs ?? 120_000;
  const intervalMs = opts.intervalMs ?? 5_000;
  let lastError: string | null = null;

  while (Date.now() - started < timeoutMs) {
    try {
      const summary = await searchLatestMessage(opts.sentTo);
      if (summary?.id) {
        const message = await getMailosaurMessage(summary.id);
        if (!opts.subject || opts.subject.test(message.subject ?? "")) {
          return message;
        }
        lastError = `latest subject ${JSON.stringify(message.subject)} did not match`;
      }
    } catch (err) {
      lastError = err instanceof Error ? err.message : String(err);
    }
    await new Promise((resolve) => setTimeout(resolve, intervalMs));
  }

  throw new Error(
    `Timed out waiting for Mailosaur message to ${opts.sentTo}` +
      (lastError ? ` (${lastError})` : ""),
  );
}

export function extractInviteUrl(message: MailosaurMessage): string | null {
  const links = [
    ...(message.html?.links ?? []),
    ...(message.text?.links ?? []),
  ]
    .map((link) => link.href)
    .filter((href): href is string => Boolean(href));
  const bodies = [message.html?.body, message.text?.body].filter(
    (body): body is string => Boolean(body),
  );
  for (const body of bodies) {
    for (const match of body.matchAll(/https?:\/\/[^\s"'<>]+/g)) {
      links.push(match[0]);
    }
  }
  return links.find((url) => /\/invite\?token=/.test(url)) ?? null;
}

async function searchLatestMessage(sentTo: string): Promise<MailosaurMessage | null> {
  const server = requireEnv("MAILOSAUR_SERVER_ID");
  const res = await fetch(`${MAILOSAUR_API}/messages/search?server=${server}`, {
    method: "POST",
    headers: mailosaurHeaders(),
    body: JSON.stringify({ sentTo }),
  });
  if (!res.ok) {
    throw new Error(`Mailosaur search ${res.status}: ${await res.text()}`);
  }
  const json = (await res.json()) as SearchResponse;
  return json.items?.[0] ?? null;
}

async function getMailosaurMessage(id: string): Promise<MailosaurMessage> {
  const res = await fetch(`${MAILOSAUR_API}/messages/${encodeURIComponent(id)}`, {
    headers: mailosaurHeaders(),
  });
  if (!res.ok) {
    throw new Error(`Mailosaur message ${id} ${res.status}: ${await res.text()}`);
  }
  return (await res.json()) as MailosaurMessage;
}

function mailosaurHeaders(): Record<string, string> {
  const token = Buffer.from(`${requireEnv("MAILOSAUR_API_KEY")}:`).toString(
    "base64",
  );
  return {
    Authorization: `Basic ${token}`,
    Accept: "application/json",
    "Content-Type": "application/json",
  };
}

function requireEnv(name: string): string {
  const value = process.env[name]?.trim();
  if (!value) throw new Error(`Missing ${name}`);
  return value;
}
