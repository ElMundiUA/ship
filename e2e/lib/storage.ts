import { existsSync } from "fs";

/** Saved Playwright storage (Auth0 + optionally github.com), see e2e/README.md */
export function hasPlaywrightStorageState(): boolean {
  const p = process.env.E2E_STORAGE_STATE?.trim();
  return Boolean(p && existsSync(p));
}
