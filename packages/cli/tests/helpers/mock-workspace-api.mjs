import http from "node:http";

/**
 * Bind an ephemeral mock workspace API on 127.0.0.1.
 *
 * @param {(req: import("node:http").IncomingMessage, res: import("node:http").ServerResponse, ctx: { body: unknown|null, record: Record<string, unknown> }) => void} handler
 */
export function startMockServer(handler) {
  /** @type {Array<{ method: string, url: string, headers: Record<string, string|string[]|undefined>, body: unknown|null }>} */
  const requests = [];

  const server = http.createServer((req, res) => {
    const chunks = [];
    req.on("data", (c) => chunks.push(c));
    req.on("end", () => {
      let body = null;
      const raw = Buffer.concat(chunks).toString("utf8");
      if (raw) {
        try {
          body = JSON.parse(raw);
        } catch {
          body = raw;
        }
      }
      const record = {
        method: req.method || "GET",
        url: req.url || "/",
        headers: req.headers,
        body,
      };
      requests.push(record);
      handler(req, res, { body, record });
    });
  });

  return new Promise((resolve) => {
    server.listen(0, "127.0.0.1", () => {
      const addr = server.address();
      const port = typeof addr === "object" && addr ? addr.port : 0;
      resolve({
        server,
        baseUrl: `http://127.0.0.1:${port}`,
        requests,
      });
    });
  });
}

export async function closeMockServer(server) {
  await new Promise((resolve, reject) => {
    server.close((err) => (err ? reject(err) : resolve()));
  });
}

export const TEST_WS_ID = "aaaaaaaa-bbbb-4ccc-dddd-eeeeeeeeeeee";
export const TEST_REPO_ID = "bbbbbbbb-bbbb-4ccc-dddd-eeeeeeeeeeee";
export const TEST_TOKEN = "fake-test-token";

export function authEnv(overrides = {}) {
  return {
    SHIP_API_TOKEN: TEST_TOKEN,
    SHIP_WORKSPACE_ID: TEST_WS_ID,
    SHIP_API_BASE: "",
    SHIP_WORKSPACE_API_BASE: "",
    ...overrides,
  };
}
