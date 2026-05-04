"use client";

/**
 * StaticUploadDropzone — drag/drop or click-to-pick text files. Each
 * file becomes one entry in ``config.documents``: ``{title, filename,
 * body_md}``. Title defaults to the filename without extension, but is
 * editable per file. Removing a file is one click.
 *
 * Files are read in the browser (FileReader). Static-upload sources are
 * one-shot, so this is the only operator-facing path that needs a real
 * upload — there's no server-side file store; the content lives inside
 * the source's ``config`` row.
 */

import { useRef, useState } from "react";

export type UploadedDoc = {
  title: string;
  filename: string;
  body_md: string;
};

const ACCEPTED_EXTENSIONS = [".md", ".mdx", ".txt", ".rst", ".adoc"];
// Keep documents reasonable so we don't bloat the source row's JSON
// past Postgres' practical 1MB jsonb sweet spot. ~200kB per doc allows
// a handful of substantial handbooks before the operator should switch
// to a docs-repo or website source.
const MAX_BYTES_PER_DOC = 200 * 1024;
const MAX_DOCS = 25;

export function StaticUploadDropzone({
  value,
  onChange,
}: {
  value: UploadedDoc[];
  onChange: (next: UploadedDoc[]) => void;
}) {
  const [dragOver, setDragOver] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const inputRef = useRef<HTMLInputElement>(null);

  async function ingestFiles(files: FileList | null) {
    if (!files || files.length === 0) return;
    setError(null);
    const additions: UploadedDoc[] = [];
    for (const file of Array.from(files)) {
      const lower = file.name.toLowerCase();
      if (!ACCEPTED_EXTENSIONS.some((ext) => lower.endsWith(ext))) {
        setError(`${file.name} — only ${ACCEPTED_EXTENSIONS.join(", ")} are accepted`);
        continue;
      }
      if (file.size > MAX_BYTES_PER_DOC) {
        setError(`${file.name} — too large (max ${Math.round(MAX_BYTES_PER_DOC / 1024)}KB)`);
        continue;
      }
      const body = await file.text();
      additions.push({
        title: stripExtension(file.name),
        filename: file.name,
        body_md: body,
      });
    }
    if (additions.length === 0) return;
    const merged = dedupeByFilename([...value, ...additions]).slice(0, MAX_DOCS);
    onChange(merged);
  }

  function updateTitle(idx: number, title: string) {
    const next = value.map((doc, i) => (i === idx ? { ...doc, title } : doc));
    onChange(next);
  }

  function removeAt(idx: number) {
    onChange(value.filter((_, i) => i !== idx));
  }

  return (
    <div className="space-y-3" data-testid="static-upload-dropzone">
      <div
        onDragOver={(event) => {
          event.preventDefault();
          setDragOver(true);
        }}
        onDragLeave={() => setDragOver(false)}
        onDrop={(event) => {
          event.preventDefault();
          setDragOver(false);
          void ingestFiles(event.dataTransfer.files);
        }}
        onClick={() => inputRef.current?.click()}
        role="button"
        tabIndex={0}
        onKeyDown={(event) => {
          if (event.key === "Enter" || event.key === " ") {
            event.preventDefault();
            inputRef.current?.click();
          }
        }}
        className={`cursor-pointer rounded border-2 border-dashed px-4 py-6 text-center transition ${
          dragOver
            ? "border-aqua/60 bg-aqua/[0.06]"
            : "border-white/15 bg-black/30 hover:border-white/30"
        }`}
      >
        <p className="text-sm text-white/85">
          Drop files here or <span className="text-aqua">click to browse</span>
        </p>
        <p className="mt-1 text-[11px] text-white/45">
          {ACCEPTED_EXTENSIONS.join(", ")} · up to {Math.round(MAX_BYTES_PER_DOC / 1024)}KB each · {MAX_DOCS} max
        </p>
        <input
          ref={inputRef}
          type="file"
          multiple
          accept={ACCEPTED_EXTENSIONS.join(",")}
          className="hidden"
          onChange={(event) => void ingestFiles(event.target.files)}
        />
      </div>

      {error && <p className="text-xs text-coral">{error}</p>}

      {value.length > 0 && (
        <ul className="divide-y divide-white/5 rounded border border-white/10 bg-black/30">
          {value.map((doc, idx) => (
            <li key={`${doc.filename}-${idx}`} className="grid grid-cols-[1fr_auto] items-center gap-3 px-3 py-2 text-xs">
              <div className="min-w-0">
                <input
                  value={doc.title}
                  onChange={(event) => updateTitle(idx, event.target.value)}
                  className="w-full rounded border border-transparent bg-transparent px-1 py-0.5 text-sm text-white/90 hover:border-white/15 focus:border-aqua/40 focus:outline-none"
                  aria-label={`Title for ${doc.filename}`}
                />
                <div className="mt-0.5 truncate text-[11px] text-white/45">
                  {doc.filename} · {Math.round(doc.body_md.length / 1024) || 1}KB
                </div>
              </div>
              <button
                type="button"
                onClick={() => removeAt(idx)}
                className="rounded-full border border-white/10 px-2 py-0.5 text-[11px] text-white/55 hover:border-coral/40 hover:text-coral"
                aria-label={`Remove ${doc.filename}`}
              >
                Remove
              </button>
            </li>
          ))}
        </ul>
      )}
    </div>
  );
}


function stripExtension(filename: string): string {
  const dot = filename.lastIndexOf(".");
  if (dot <= 0) return filename;
  return filename.slice(0, dot);
}


function dedupeByFilename(docs: UploadedDoc[]): UploadedDoc[] {
  // Re-uploading a file replaces the previous one — preserves manual
  // title edits if they kept the same filename, but the body refreshes.
  const seen = new Map<string, UploadedDoc>();
  for (const doc of docs) seen.set(doc.filename, doc);
  return Array.from(seen.values());
}
