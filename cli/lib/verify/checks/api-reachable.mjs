import { apiGet, HttpError } from "../../http.mjs";

export const id = "api-reachable";
export const category = "network";
export const description = "Ship methodology API responds on /health or /manifest";

/**
 * @param {import("../registry.mjs").CheckContext} ctx
 */
export async function run(ctx) {
  const baseUrl = ctx.baseUrl
    || (ctx.config && ctx.config.api && ctx.config.api.base_url)
    || process.env.SHIP_API_BASE
    || "https://ship.elmundi.com";

  // Try /health first (cheap endpoint in the FastAPI backend). If 404, fall back
  // to /manifest which is always published.
  try {
    await apiGet(baseUrl, "/health");
    return { status: "pass", detail: `${baseUrl}/health → 200` };
  } catch (e) {
    if (!(e instanceof HttpError) || e.status !== 404) {
      // network error OR other HTTP code — try /manifest
    }
  }

  try {
    await apiGet(baseUrl, "/manifest");
    return { status: "pass", detail: `${baseUrl}/manifest → 200` };
  } catch (e) {
    const msg = e instanceof HttpError ? `${e.status} ${e.statusText}` : e.message;
    return {
      status: "fail",
      detail: `${baseUrl} unreachable: ${msg}`,
      data: { baseUrl },
    };
  }
}
