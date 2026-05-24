---
version: 1
project_type: desktop
display_name: Desktop application
delivery: installer
environments: [nightly, stable]
detect:
  project_type_signals:
    - "electron"
    - "electron-builder"
    - "@tauri-apps/cli"
    - "src-tauri/tauri.conf.json"
    - "*.csproj"
    - "*.sln"
    - "PyInstaller"
    - "Qt"
  capabilities:
    unit_tests: ["jest", "vitest", "**/*.test.*", "cargo test", "pytest"]
    e2e_tests: ["playwright", "spectron", "wdio", "tauri-driver", "*.e2e.*"]
    installers: ["electron-builder", "tauri", "**/electron-builder.*", "src-tauri"]
    code_signing: ["CSC_LINK", "notarize", "signtool", "APPLE_ID"]
    release_channel: [".github/workflows/*release*", "latest.yml", "latest-mac.yml"]
required:
  - unit_tests
  - e2e_tests
  - installers
  - code_signing
  - release_channel
secrets:
  required:
    - CURSOR_API_KEY
  optional:
    - CSC_LINK
    - CSC_KEY_PASSWORD
    - APPLE_ID
    - APPLE_APP_SPECIFIC_PASSWORD
---

# Desktop application — SDLC blueprint (minimal)

## Where this gets you

A connected desktop repo that: runs unit + e2e tests in CI, builds
**signed + notarized** per-OS installers (`.dmg` / `.exe` / `.AppImage`)
from one commit, publishes to a **nightly** channel automatically, and
**promotes the same build to the stable** channel on your approval —
with auto-update wired so users actually receive it. Docker is NOT the
delivery here — installers + an update feed are. (A companion backend, if
any, follows the **web** blueprint.)

---

## What YOU set up outside Ship and hand over

Signing certs and a distribution channel only you can provision. Do these
once, add the secrets to the repo (Settings → Secrets and variables →
Actions). Values are read at build time, never stored.

1. **macOS signing + notarization (if you ship mac)** — Apple Developer
   ID Application cert (`.p12`) + an app-specific password / API key →
   add `CSC_LINK` (base64 .p12), `CSC_KEY_PASSWORD`, `APPLE_ID`,
   `APPLE_APP_SPECIFIC_PASSWORD` (or `APPLE_API_KEY`).
   - mac builds + notarization need a **macOS CI runner**.
2. **Windows signing (if you ship win)** — Authenticode cert (`.pfx`) →
   add `WIN_CSC_LINK` (base64) + `WIN_CSC_KEY_PASSWORD`. (For EV /
   cloud-HSM signing, provide the provider token instead.)
3. **Distribution channel + auto-update feed** — pick where installers
   live and where the updater checks:
   - GitHub Releases (simplest; uses `GITHUB_TOKEN`); **or**
   - an S3/CDN bucket → add `UPDATE_BUCKET`, `UPDATE_AWS_KEY` /
     `UPDATE_AWS_SECRET`.
4. **Target OSes** — confirm which of macOS / Windows / Linux you ship
   (drives which runners + signing apply).
5. **Promotion policy** — **manual** (you approve nightly→stable) via a
   GitHub Environment `stable` with required reviewer, or **rule**
   (e.g. tag `v*`).
6. **Agent keys** — `CURSOR_API_KEY` etc. on the repo (Ship onboarding).

> Note OSes + channel choice in the bootstrap ticket so the devops agent
> scaffolds only what applies.

---

## What Ship scaffolds for you (devops agent)

- Packager config (electron-builder / tauri / per-stack) for the chosen
  OSes, producing `.dmg` / `.exe` / `.AppImage`.
- CI: unit tests + e2e (Playwright-electron / tauri-driver) on PRs.
- Signed + notarized build pipeline from `sha-<commit>`.
- Auto-update feed (`latest*.yml` / update manifest) + a **nightly**
  publish on merge.
- A **stable** promotion step that re-publishes the **same artifacts**
  (no rebuild) behind your gate.
- `.env.example` + `SIGNING.md` runbook for every required secret.

---

## Execution checklist (control)

- [ ] **(you)** Signing certs added (macOS `.p12` + notarization; Win `.pfx`).
- [ ] **(you)** macOS runner available (if shipping mac).
- [ ] **(you)** Distribution channel + update-feed creds added.
- [ ] **(you)** `stable` GitHub Environment gated (or release rule).
- [ ] Unit tests run + pass in CI.
- [ ] E2e runs in CI (smoke launch path).
- [ ] CI builds **signed + notarized** installers for each target OS.
- [ ] Merge publishes to the **nightly** channel; auto-update resolves it.
- [ ] Stable promotion re-publishes the **same build** (no rebuild), gated.
- [ ] `SIGNING.md` documents every secret + cert expiry/rotation.
