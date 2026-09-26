import assert from "node:assert/strict";
import { after, afterEach, test } from "node:test";
import { webcrypto } from "node:crypto";

import worker from "../cloud/slicer-worker/src/index.js";

const originalFetch = globalThis.fetch;
const issuer = "https://access.example.test";
const { privateKey, publicKey } = await crypto.subtle.generateKey({ name: "RSASSA-PKCS1-v1_5", modulusLength: 2048, publicExponent: new Uint8Array([1, 0, 1]), hash: "SHA-256" }, true, ["sign", "verify"]);
const jwk = { ...await crypto.subtle.exportKey("jwk", publicKey), kid: "test-key", use: "sig", alg: "RS256" };

afterEach(() => { globalThis.fetch = originalFetch; });

function b64(value) { return Buffer.from(typeof value === "string" ? value : JSON.stringify(value)).toString("base64url"); }
async function token(claims = {}) {
  const header = b64({ alg: "RS256", typ: "JWT", kid: "test-key" });
  const payload = b64({ iss: issuer, aud: "slicer-audience", exp: Math.floor(Date.now() / 1000) + 300, sub: "chatgpt-user", ...claims });
  const input = `${header}.${payload}`;
  const signature = await crypto.subtle.sign("RSASSA-PKCS1-v1_5", privateKey, new TextEncoder().encode(input));
  return `${input}.${Buffer.from(signature).toString("base64url")}`;
}
function environment() {
  return {
    GITHUB_CODESPACES_TOKEN: "github-token", ORIGIN_BEARER_TOKEN: "origin-token",
    CODESPACE_NAME: "test-codespace", CODESPACE_OWNER: "owner", CODESPACE_PORT: "8000",
    CODESPACE_ORIGIN: "https://origin.example.test", ACCESS_ISSUER: issuer,
    ACCESS_AUDIENCE: "slicer-audience", STARTUP_POLLS: "3", STARTUP_POLL_MS: "0",
    PORT_POLLS: "3", PORT_POLL_MS: "0", HEALTH_POLLS: "1", HEALTH_POLL_MS: "0",
  };
}
function mockInfrastructure(originHandler = () => new Response("healthy", { status: 200 })) {
  return async (input, init = {}) => {
    const url = String(input);
    if (url === `${issuer}/cdn-cgi/access/certs`) return Response.json({ keys: [jwk] });
    if (url === "https://api.github.com/user/codespaces/test-codespace") return Response.json({ state: "available" });
    if (url === "https://api.github.com/user/codespaces/test-codespace?internal=true&refresh=true") return Response.json({ connection: { tunnelProperties: { serviceUri: "https://global.rel.tunnels.api.visualstudio.com/", clusterId: "uks1", tunnelId: "test-tunnel", managePortsAccessToken: "manage-ports-token" } } });
    if (url.startsWith("https://uks1.rel.tunnels.api.visualstudio.com/tunnels/test-tunnel/ports/8000")) return new Response(null, { status: 204 });
    if (url === "https://origin.example.test/health") return new Response("healthy", { status: 200 });
    if (url === "https://origin.example.test/mcp") return originHandler(init);
    throw new Error(`unexpected fetch: ${url}`);
  };
}

test("rejects missing assertion before calling upstreams and reports missing verifier config", async () => {
  globalThis.fetch = async () => { throw new Error("upstream should not be called"); };
  const response = await worker.fetch(new Request("https://slicer.example.test/mcp"), environment());
  assert.equal(response.status, 401);
  assert.equal(response.headers.get("www-authenticate"), "Bearer");
  const noConfig = await worker.fetch(new Request("https://slicer.example.test/mcp"), { ...environment(), ACCESS_AUDIENCE: "" });
  assert.equal(noConfig.status, 503);
});

test("rejects invalid signature, issuer and audience before Codespace lifecycle calls", async () => {
  let githubCalls = 0;
  globalThis.fetch = async (input) => {
    if (String(input) === `${issuer}/cdn-cgi/access/certs`) return Response.json({ keys: [jwk] });
    githubCalls += 1;
    throw new Error("Codespace call not expected");
  };
  const valid = await token();
  const invalidSignature = `${valid.slice(0, valid.lastIndexOf(".") + 1)}${valid.endsWith("A") ? "B" : "A"}`;
  for (const jwt of [await token({ iss: "https://wrong.example.test" }), await token({ aud: "wrong" }), invalidSignature, "a.b.c"]) {
    const response = await worker.fetch(new Request("https://slicer.example.test/mcp", { headers: { "Cf-Access-Jwt-Assertion": jwt } }), environment());
    assert.equal(response.status, 401);
  }
  assert.equal(githubCalls, 0);
});

test("valid Access JWT replaces all external credentials with the private origin bearer", async () => {
  let receivedHeaders;
  globalThis.fetch = mockInfrastructure((init) => {
    receivedHeaders = new Headers(init.headers);
    return new Response("mcp response", { status: 200 });
  });
  const jwt = await token();
  const response = await worker.fetch(new Request("https://slicer.example.test/mcp", {
    method: "POST",
    headers: { "Cf-Access-Jwt-Assertion": jwt, "Cf-Access-Authenticated-User-Email": "owner@example.test", authorization: "Bearer external-oauth", cookie: "session=secret", "x-auth-request-access-token": "external-token", "content-type": "application/json" },
    body: JSON.stringify({ jsonrpc: "2.0", id: 1, method: "initialize" }),
  }), environment());
  assert.equal(response.status, 200);
  assert.equal(await response.text(), "mcp response");
  assert.equal(receivedHeaders.get("authorization"), "Bearer origin-token");
  assert.equal(receivedHeaders.get("cf-access-jwt-assertion"), null);
  assert.equal(receivedHeaders.get("cf-access-authenticated-user-email"), null);
  assert.equal(receivedHeaders.get("cookie"), null);
  assert.equal(receivedHeaders.get("x-auth-request-access-token"), null);
});

test("accepts a configured additional audience", async () => {
  globalThis.fetch = mockInfrastructure(() => new Response("ok", { status: 200 }));
  const jwt = await token({ aud: "extra-audience" });
  const response = await worker.fetch(new Request("https://slicer.example.test/health", { headers: { "Cf-Access-Jwt-Assertion": jwt } }), { ...environment(), ACCESS_ADDITIONAL_AUDIENCES: "extra-audience" });
  assert.equal(response.status, 200);
});

test("starts a stopped Codespace before configuring its port", async () => {
  const calls = [];
  let lookupCount = 0;
  const base = mockInfrastructure(() => new Response("healthy", { status: 200 }));
  globalThis.fetch = async (input, init = {}) => {
    const url = String(input);
    calls.push({ url, method: init.method || "GET" });
    if (url === "https://api.github.com/user/codespaces/test-codespace") {
      lookupCount += 1;
      return Response.json({ state: lookupCount === 1 ? "shutdown" : "available" });
    }
    if (url.endsWith("/start")) return new Response(null, { status: 202 });
    return base(input, init);
  };
  const response = await worker.fetch(new Request("https://slicer.example.test/health", { headers: { "Cf-Access-Jwt-Assertion": await token() } }), environment());
  assert.equal(response.status, 200);
  const startIndex = calls.findIndex((call) => call.url.endsWith("/start"));
  const tunnelIndex = calls.findIndex((call) => call.url.includes("/tunnels/test-tunnel/ports/8000"));
  assert.ok(startIndex >= 0);
  assert.ok(tunnelIndex > startIndex);
});
