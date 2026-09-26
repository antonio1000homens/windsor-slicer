const GITHUB_API = "https://api.github.com";

function json(body, status = 200) {
  return new Response(JSON.stringify(body), { status, headers: { "content-type": "application/json" } });
}

const jwksCache = new Map();

function decodePart(value) {
  const normalized = value.replace(/-/g, "+").replace(/_/g, "/");
  return JSON.parse(atob(normalized + "=".repeat((4 - normalized.length % 4) % 4)));
}

function audiences(env) {
  return [String(env.ACCESS_AUDIENCE || "").trim(), ...String(env.ACCESS_ADDITIONAL_AUDIENCES || "").split(",").map((value) => value.trim())].filter(Boolean);
}

async function accessPayload(request, env) {
  const issuer = String(env.ACCESS_ISSUER || "").replace(/\/$/, "");
  const acceptedAudiences = audiences(env);
  if (!issuer || acceptedAudiences.length === 0) throw new Error("configuration");
  const assertion = request.headers.get("Cf-Access-Jwt-Assertion");
  if (!assertion) throw new Error("unauthorized");
  const parts = assertion.split(".");
  if (parts.length !== 3) throw new Error("unauthorized");
  const header = decodePart(parts[0]);
  const payload = decodePart(parts[1]);
  if (header.alg !== "RS256" || typeof header.kid !== "string") throw new Error("unauthorized");
  let cached = jwksCache.get(issuer);
  if (!cached || cached.expiresAt <= Date.now()) {
    const response = await fetch(issuer + "/cdn-cgi/access/certs", { headers: { accept: "application/json" } });
    if (!response.ok) throw new Error("jwks");
    cached = { keys: await response.json(), expiresAt: Date.now() + 5 * 60 * 1000 };
    jwksCache.set(issuer, cached);
  }
  let jwk = (cached.keys.keys || []).find((item) => item.kid === header.kid && item.kty === "RSA" && item.use !== "enc");
  if (!jwk) {
    const response = await fetch(issuer + "/cdn-cgi/access/certs", { headers: { accept: "application/json" } });
    if (!response.ok) throw new Error("jwks");
    cached = { keys: await response.json(), expiresAt: Date.now() + 5 * 60 * 1000 };
    jwksCache.set(issuer, cached);
    jwk = (cached.keys.keys || []).find((item) => item.kid === header.kid && item.kty === "RSA" && item.use !== "enc");
  }
  if (!jwk) throw new Error("unauthorized");
  const key = await crypto.subtle.importKey("jwk", jwk, { name: "RSASSA-PKCS1-v1_5", hash: "SHA-256" }, false, ["verify"]);
  const signature = Uint8Array.from(atob(parts[2].replace(/-/g, "+").replace(/_/g, "/") + "=".repeat((4 - parts[2].length % 4) % 4)), (char) => char.charCodeAt(0));
  const signed = new TextEncoder().encode(parts[0] + "." + parts[1]);
  if (!await crypto.subtle.verify("RSASSA-PKCS1-v1_5", key, signature, signed)) throw new Error("unauthorized");
  const aud = Array.isArray(payload.aud) ? payload.aud : [payload.aud];
  const now = Math.floor(Date.now() / 1000);
  if (payload.iss !== issuer || !aud.some((value) => acceptedAudiences.includes(value)) || !Number.isFinite(payload.exp) || payload.exp <= now || (payload.nbf !== undefined && payload.nbf > now + 60)) throw new Error("unauthorized");
  return payload;
}

async function github(env, path, init = {}) {
  const headers = new Headers(init.headers || {});
  headers.set("authorization", "Bearer " + env.GITHUB_CODESPACES_TOKEN);
  headers.set("accept", "application/vnd.github+json");
  headers.set("x-github-api-version", "2022-11-28");
  headers.set("user-agent", "windsor-slicer");
  return fetch(GITHUB_API + path, { ...init, headers });
}

async function getCodespace(env) {
  const response = await github(env, "/user/codespaces/" + encodeURIComponent(env.CODESPACE_NAME));
  if (!response.ok) throw new Error("codespace lookup failed: " + response.status);
  const codespace = await response.json();
  if (env.CODESPACE_OWNER && codespace.owner?.login && codespace.owner.login.toLowerCase() !== env.CODESPACE_OWNER.toLowerCase()) {
    throw new Error("codespace owner does not match configured owner");
  }
  return codespace;
}

async function getTunnelProperties(env) {
  const path = "/user/codespaces/" + encodeURIComponent(env.CODESPACE_NAME) + "?internal=true&refresh=true";
  const response = await github(env, path);
  if (!response.ok) throw new Error("codespace tunnel lookup failed: " + response.status);
  const codespace = await response.json();
  const properties = codespace.connection?.tunnelProperties;
  if (!properties?.serviceUri || !properties?.clusterId || !properties?.tunnelId || !properties?.managePortsAccessToken) {
    throw new Error("codespace tunnel properties are incomplete");
  }
  return properties;
}

async function ensurePublicPort(env) {
  const port = Number(env.CODESPACE_PORT || "8000");
  const tunnel = await getTunnelProperties(env);
  const service = new URL(tunnel.serviceUri);
  if (service.protocol !== "https:" || !service.hostname.endsWith(".tunnels.api.visualstudio.com") || !/^[a-z0-9-]+$/i.test(tunnel.clusterId)) {
    throw new Error("codespace tunnel service URI is invalid");
  }
  if (!service.hostname.startsWith(tunnel.clusterId + ".")) {
    service.hostname = tunnel.clusterId + "." + service.hostname.replace(/^global\./, "");
  }
  const url = new URL("/tunnels/" + encodeURIComponent(tunnel.tunnelId) + "/ports/" + port, service);
  url.searchParams.set("api-version", "2023-09-27-preview");
  const maxPolls = Number(env.PORT_POLLS || env.HEALTH_POLLS || "10");
  const delayMs = Number(env.PORT_POLL_MS || env.HEALTH_POLL_MS || "2000");
  let lastStatus = 0;
  for (let i = 0; i < maxPolls; i += 1) {
    const response = await fetch(url, {
      method: "PUT",
      headers: {
        authorization: "Tunnel " + tunnel.managePortsAccessToken,
        "content-type": "application/json;charset=UTF-8",
        "if-match": "*",
        "user-agent": "windsor-slicer",
      },
      body: JSON.stringify({
        portNumber: port,
        accessControl: { entries: [{ type: "Anonymous", subjects: [], scopes: ["connect"] }] },
      }),
    });
    if (response.ok) return;
    lastStatus = response.status;
    // GitHub does not expose the visibility endpoint until the app's port is forwarded.
    if (lastStatus !== 404) break;
    if (i + 1 < maxPolls) await sleep(delayMs);
  }
  throw new Error("port visibility update failed: " + lastStatus);
}

async function startCodespace(env) {
  const path = "/user/codespaces/" + encodeURIComponent(env.CODESPACE_NAME) + "/start";
  const response = await github(env, path, { method: "POST" });
  if (!response.ok && response.status !== 409) throw new Error("codespace start failed: " + response.status);
}

function origin(env) {
  if (env.CODESPACE_ORIGIN) return env.CODESPACE_ORIGIN.replace(/\/$/, "");
  const port = env.CODESPACE_PORT || "8000";
  const domain = env.CODESPACE_FORWARDING_DOMAIN || "app.github.dev";
  return "https://" + env.CODESPACE_NAME + "-" + port + "." + domain;
}

async function sleep(ms) { await new Promise((resolve) => setTimeout(resolve, ms)); }

async function originHealthy(env) {
  try {
    const response = await fetch(origin(env) + "/health", {
      headers: { authorization: "Bearer " + env.ORIGIN_BEARER_TOKEN },
    });
    return response.ok;
  } catch {
    return false;
  }
}

async function ensureReady(env) {
  let codespace = await getCodespace(env);
  let state = String(codespace.state || "").toLowerCase();

  if (state !== "available") {
    if (!["starting", "rebuilding"].includes(state)) await startCodespace(env);
    const maxPolls = Number(env.STARTUP_POLLS || "12");
    const delayMs = Number(env.STARTUP_POLL_MS || "3000");
    for (let i = 0; i < maxPolls; i += 1) {
      await sleep(delayMs);
      codespace = await getCodespace(env);
      state = String(codespace.state || "").toLowerCase();
      if (state === "available") break;
    }
  }

  if (state !== "available") return { ok: false, state, error: "workspace_starting" };
  await ensurePublicPort(env);

  const healthPolls = Number(env.HEALTH_POLLS || "10");
  const healthDelayMs = Number(env.HEALTH_POLL_MS || "2000");
  for (let i = 0; i < healthPolls; i += 1) {
    if (await originHealthy(env)) return { ok: true, state };
    await sleep(healthDelayMs);
  }
  return { ok: false, state, error: "workspace_unavailable" };
}

async function proxy(request, env) {
  const incoming = new URL(request.url);
  const target = new URL(incoming.pathname + incoming.search, origin(env));
  const headers = new Headers(request.headers);
  for (const name of [...headers.keys()]) {
    const lower = name.toLowerCase();
    if (lower === "authorization" || lower === "cookie" || lower.startsWith("cf-access-") || lower.startsWith("x-auth-request-") || lower === "x-forwarded-access-token") headers.delete(name);
  }
  headers.set("authorization", "Bearer " + env.ORIGIN_BEARER_TOKEN);
  headers.delete("host");
  headers.delete("cf-connecting-ip");
  headers.delete("cf-ray");
  return fetch(target.toString(), {
    method: request.method, headers,
    body: ["GET", "HEAD"].includes(request.method) ? undefined : request.body,
    redirect: "manual",
  });
}

export default {
  async fetch(request, env) {
    if (!env.ORIGIN_BEARER_TOKEN || !env.GITHUB_CODESPACES_TOKEN || !env.CODESPACE_NAME || !env.CODESPACE_OWNER) {
      return json({ ok: false, error: "proxy_not_configured" }, 503);
    }
    try { await accessPayload(request, env); }
    catch (error) {
      if (error.message === "configuration") return json({ ok: false, error: "access_verifier_not_configured" }, 503);
      if (error.message === "jwks") return json({ ok: false, error: "access_verifier_unavailable" }, 503);
      return new Response(JSON.stringify({ ok: false, error: "unauthorized" }), { status: 401, headers: { "content-type": "application/json", "www-authenticate": "Bearer" } });
    }
    const url = new URL(request.url);
    if (!["/mcp", "/health"].includes(url.pathname)) return json({ ok: false, error: "not_found" }, 404);
    let ready;
    try { ready = await ensureReady(env); }
    catch { return json({ ok: false, error: "workspace_control_failed" }, 502); }
    if (!ready.ok) return json(ready, 503);
    return proxy(request, env);
  },
};
