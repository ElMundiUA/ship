import { describe, expect, it } from "vitest";

import {
  isAllowedAttachment,
  resolveMime,
} from "@/lib/attachment-policy";

describe("resolveMime", () => {
  it("maps empty browser MIME for .md to text/markdown", () => {
    expect(resolveMime("session-summary.md", "")).toBe("text/markdown");
  });

  it("maps application/octet-stream for .md to text/markdown", () => {
    expect(resolveMime("notes.md", "application/octet-stream")).toBe(
      "text/markdown",
    );
  });

  it("preserves explicit valid MIME", () => {
    expect(resolveMime("doc.md", "text/markdown")).toBe("text/markdown");
  });

  it("uses final extension for double extensions", () => {
    expect(resolveMime("archive.tar.md", "")).toBe("text/markdown");
  });

  it("is case-insensitive on extension", () => {
    expect(resolveMime("README.MD", "")).toBe("text/markdown");
  });

  it("falls back to extension when browser MIME is disallowed", () => {
    expect(resolveMime("doc.md", "image/heic")).toBe("text/markdown");
  });

  it("returns octet-stream for unsupported extensions", () => {
    expect(resolveMime("photo.heic", "")).toBe("application/octet-stream");
  });
});

describe("isAllowedAttachment", () => {
  it("accepts session-summary.md with empty browser type", () => {
    expect(isAllowedAttachment("session-summary.md", "")).toBe(true);
  });

  it("rejects unsupported extensions", () => {
    expect(isAllowedAttachment("photo.heic", "")).toBe(false);
  });
});
