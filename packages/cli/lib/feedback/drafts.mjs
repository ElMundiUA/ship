import fs from "node:fs";
import path from "node:path";
import YAML from "yaml";

export function draftsDir(shipRoot) {
  return path.join(shipRoot, ".ship", "feedback-drafts");
}

function safeSlug(s) {
  return String(s || "draft")
    .toLowerCase()
    .replace(/[^a-z0-9._-]+/g, "-")
    .replace(/^-+|-+$/g, "")
    .slice(0, 64) || "draft";
}

function timestamp() {
  const iso = new Date().toISOString();
  return iso.replace(/[:T]/g, "-").replace(/\..+$/, "");
}

/**
 * Create a new feedback draft on disk.
 *
 * @param {string} shipRoot
 * @param {{
 *   kind: string, id: string, version?: string,
 *   title: string, summary: string,
 *   recommendation?: string, stack?: object, tags?: string[]
 * }} fields
 * @returns {string} the absolute path of the draft file
 */
export function createDraft(shipRoot, fields) {
  const kind = String(fields.kind || "").trim();
  const id = String(fields.id || "").trim();
  if (!kind || !id) {
    throw new Error("createDraft: kind and id are required");
  }

  const dir = draftsDir(shipRoot);
  fs.mkdirSync(dir, { recursive: true });

  const name = `${timestamp()}-${safeSlug(kind)}-${safeSlug(id)}.md`;
  const fp = path.join(dir, name);

  const meta = {
    kind,
    id,
    version: fields.version || null,
    tags: Array.isArray(fields.tags) ? fields.tags : [],
    created_at: new Date().toISOString(),
  };
  if (fields.title) meta.title = fields.title;

  const stack = fields.stack || {};
  const stackLine = `tracker=${stack.tracker || "-"}, ci=${stack.ci || "-"}, agents=${
    Array.isArray(stack.agents) ? stack.agents.join("+") || "-" : stack.agents || "-"
  }, preset=${stack.preset || "-"}`;

  const body =
    `# ${fields.title || ""}\n\n` +
    `**Summary**: ${fields.summary || ""}\n\n` +
    `**Recommendation**: ${fields.recommendation || ""}\n\n` +
    `**Stack context**: ${stackLine}\n\n` +
    `<!-- ship-feedback: v1 -->\n`;

  const front = YAML.stringify(meta, { lineWidth: 0 });
  const text = `---\n${front}---\n\n${body}`;
  fs.writeFileSync(fp, text, "utf8");
  return fp;
}

/**
 * List all draft files under `.ship/feedback-drafts/`, including under `sent/`.
 * Returns absolute paths, sorted ascending by filename (timestamp-prefixed).
 */
export function listDrafts(shipRoot) {
  const dir = draftsDir(shipRoot);
  if (!fs.existsSync(dir)) return [];
  const out = [];
  for (const entry of fs.readdirSync(dir, { withFileTypes: true })) {
    if (entry.isFile() && entry.name.endsWith(".md")) {
      out.push(path.join(dir, entry.name));
    }
  }
  const sentDir = path.join(dir, "sent");
  if (fs.existsSync(sentDir)) {
    for (const entry of fs.readdirSync(sentDir, { withFileTypes: true })) {
      if (entry.isFile() && entry.name.endsWith(".md")) {
        out.push(path.join(sentDir, entry.name));
      }
    }
  }
  out.sort();
  return out;
}

export function readDraft(filePath) {
  const raw = fs.readFileSync(filePath, "utf8");
  const match = raw.match(/^---\n([\s\S]*?)\n---\n?([\s\S]*)$/);
  if (!match) {
    return { meta: {}, body: raw };
  }
  let meta = {};
  try {
    meta = YAML.parse(match[1]) || {};
  } catch (e) {
    throw new Error(`feedback draft: failed to parse front-matter in ${filePath}: ${e.message}`);
  }
  const body = (match[2] || "").replace(/^\n+/, "");
  return { meta, body };
}

export function removeDraft(filePath) {
  fs.unlinkSync(filePath);
}

/**
 * Move a draft into `.ship/feedback-drafts/sent/` preserving history.
 * Returns the new path.
 */
export function moveDraftToSent(shipRoot, filePath) {
  const dir = path.join(draftsDir(shipRoot), "sent");
  fs.mkdirSync(dir, { recursive: true });
  const base = path.basename(filePath);
  const dest = path.join(dir, base);
  fs.renameSync(filePath, dest);
  return dest;
}
