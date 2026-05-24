---
version: 1
project_type: mobile
display_name: Mobile application
delivery: app-store
environments: [internal, production]
detect:
  project_type_signals:
    - "react-native"
    - "expo"
    - "flutter"
    - "pubspec.yaml"
    - "android/app/build.gradle"
    - "ios/*.xcodeproj"
    - "*.xcworkspace"
  capabilities:
    unit_tests: ["jest", "flutter test", "**/*.test.*", "**/*_test.dart"]
    e2e_tests: ["detox", "maestro", "appium", "*.maestro.yaml", ".detoxrc*"]
    build_artifacts: ["fastlane", "eas.json", "**/Fastfile"]
    internal_channel: ["fastlane", "eas.json"]
    store_release: [".github/workflows/*release*", "fastlane"]
required:
  - unit_tests
  - e2e_tests
  - build_artifacts
  - internal_channel
  - store_release
secrets:
  required:
    - CURSOR_API_KEY
  optional:
    - APP_STORE_CONNECT_API_KEY
    - MATCH_PASSWORD
    - ANDROID_KEYSTORE_BASE64
    - ANDROID_KEYSTORE_PASSWORD
---

# Mobile application — SDLC blueprint (minimal)

## Where this gets you

A connected mobile repo that: runs unit + device/emulator e2e tests in
CI, builds **signed** Android (`.aab`) and iOS (`.ipa`) artifacts from
one commit, ships to an **internal track** (Play Internal / TestFlight)
automatically, and **promotes the same build to the stores** on your
approval. Docker is NOT the delivery here — stores are. (A backend API,
if any, follows the **web** blueprint.)

---

## What YOU set up outside Ship and hand over

App stores need accounts and signing material only you can create. Do
these once, add the secrets to the repo (Settings → Secrets and
variables → Actions). Ship reads them at build time; values are never
stored.

1. **Apple (if iOS)** — Apple Developer Program membership +
   App Store Connect **API key** (`.p8`) → add `APP_STORE_CONNECT_KEY_ID`,
   `APP_STORE_CONNECT_ISSUER_ID`, `APP_STORE_CONNECT_API_KEY` (base64).
   - iOS builds need a **macOS CI runner** — confirm your plan has one.
2. **Google (if Android)** — Play Console account + a **service-account
   JSON** with release permissions → add `PLAY_SERVICE_ACCOUNT_JSON`.
3. **Code signing** — the secrets that sign release builds:
   - iOS: distribution cert + provisioning profile (or let fastlane
     `match` manage them via a private repo → `MATCH_GIT_URL`,
     `MATCH_PASSWORD`).
   - Android: upload **keystore** (base64) + `ANDROID_KEYSTORE_PASSWORD`,
     `ANDROID_KEY_ALIAS`, `ANDROID_KEY_PASSWORD`.
4. **App identity** — bundle id / application id, and the registered app
   records in App Store Connect and Play Console (Ship can't create the
   store listing for you).
5. **Backend config per env** (if the app talks to an API) — `INTERNAL_*`
   / `PROD_*` base URLs + keys, so internal builds hit a non-prod
   backend.
6. **Promotion policy** — **manual** (you approve internal→store) via a
   GitHub Environment `production` with required reviewer, or **rule**
   (e.g. tag `v*`). 
7. **Agent keys** — `CURSOR_API_KEY` etc. on the repo (Ship onboarding).

> Note which platforms (iOS / Android / both) in the bootstrap ticket so
> the devops agent scaffolds only what applies.

---

## What Ship scaffolds for you (devops agent)

- Fastlane (or EAS) lanes: `test`, `build_internal`, `release`.
- CI: unit tests + e2e (Detox/Maestro on emulator/simulator) on PRs.
- Signed build pipeline producing `.aab` / `.ipa` from `sha-<commit>`.
- Internal-distribution step (Play Internal track / TestFlight) on merge.
- Store-release step promoting the **same build number** behind your gate.
- `.env.example` + a `SIGNING.md` runbook listing every required secret.

---

## Execution checklist (control)

- [ ] **(you)** Store accounts created; app records registered.
- [ ] **(you)** API keys added (App Store Connect / Play service account).
- [ ] **(you)** Signing material added (cert/profile or `match`; keystore).
- [ ] **(you)** macOS runner available (iOS).
- [ ] **(you)** `production` GitHub Environment gated (or release rule).
- [ ] Unit tests run + pass in CI.
- [ ] E2e runs on emulator/simulator in CI (smoke path).
- [ ] CI produces a **signed** `.aab` and/or `.ipa`.
- [ ] Merge ships to **internal track** (TestFlight / Play Internal).
- [ ] Store release promotes the **same build** (no rebuild), gated.
- [ ] `SIGNING.md` documents every secret + rotation.
