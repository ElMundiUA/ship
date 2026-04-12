/**
 * @param {string} baseUrl
 * @param {string} path
 * @param {Record<string, unknown>} body
 */
export async function apiPost(baseUrl, path, body) {
  const url = `${baseUrl}${path.startsWith("/") ? path : `/${path}`}`;
  const res = await fetch(url, {
    method: "POST",
    headers: { "Content-Type": "application/json", Accept: "application/json" },
    body: JSON.stringify(body),
  });
  const text = await res.text();
  let data;
  try {
    data = text ? JSON.parse(text) : null;
  } catch {
    data = text;
  }
  if (!res.ok) {
    const msg = typeof data === "string" ? data : JSON.stringify(data);
    throw new Error(`HTTP ${res.status} ${res.statusText} for POST ${url}\n${msg}`);
  }
  return data;
}

/**
 * @param {string} baseUrl
 * @param {string} path
 */
export async function apiGet(baseUrl, path) {
  const url = `${baseUrl}${path.startsWith("/") ? path : `/${path}`}`;
  const res = await fetch(url, { headers: { Accept: "application/json" } });
  const text = await res.text();
  let data;
  try {
    data = text ? JSON.parse(text) : null;
  } catch {
    data = text;
  }
  if (!res.ok) {
    const msg = typeof data === "string" ? data : JSON.stringify(data);
    throw new Error(`HTTP ${res.status} ${res.statusText} for GET ${url}\n${msg}`);
  }
  return data;
}
