import path from "node:path";
import { spawn } from "node:child_process";

import { authEnv } from "./mock-workspace-api.mjs";

export const SHIPCTL_BIN = path.resolve(
  path.dirname(new URL(import.meta.url).pathname),
  "..",
  "..",
  "bin",
  "shipctl.mjs",
);

/**
 * Spawn shipctl without blocking the test event loop (mock servers need it).
 *
 * @param {string[]} args
 * @param {{ env?: Record<string, string>, cwd?: string, input?: string }} [opts]
 */
export function runShipctl(args, opts = {}) {
  return new Promise((resolve, reject) => {
    const env = opts.minimalEnv
      ? { PATH: process.env.PATH || "", HOME: process.env.HOME || "", ...authEnv(), ...(opts.env || {}) }
      : { ...process.env, ...authEnv(), ...(opts.env || {}) };
    for (const [key, value] of Object.entries(opts.env || {})) {
      if (value === "") delete env[key];
    }
    const child = spawn(process.execPath, [SHIPCTL_BIN, ...args], {
      cwd: opts.cwd,
      env,
    });
    let stdout = "";
    let stderr = "";
    child.stdout.on("data", (d) => (stdout += d.toString("utf8")));
    child.stderr.on("data", (d) => (stderr += d.toString("utf8")));
    if (opts.input != null) {
      child.stdin.write(opts.input);
    }
    child.stdin.end();
    child.on("error", reject);
    child.on("close", (code) => resolve({ status: code, stdout, stderr }));
  });
}
