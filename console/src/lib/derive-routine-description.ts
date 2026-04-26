/**
 * Human-readable one-line description from a prompt, without an LLM.
 * Uses the first line, up to 160 characters.
 */
const MAX = 160;

export function deriveDescriptionFromPrompt(prompt: string): string {
  const t = prompt.trim();
  if (!t) return "";
  const firstLine = t.split(/\r\n|\n|\r/)[0]?.trim() ?? t;
  if (firstLine.length <= MAX) return firstLine;
  const slice = firstLine.slice(0, MAX - 1);
  const lastSpace = slice.lastIndexOf(" ");
  const head = lastSpace > 40 ? slice.slice(0, lastSpace) : slice;
  return `${head}…`;
}
