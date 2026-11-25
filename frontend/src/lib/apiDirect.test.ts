// node --test로 돈다 (npm test). 읽기 API 직접 호스트 길: 켜는 조건, 허용 경로, 토큰 규칙, 실패 시 되돌아가기.
import assert from "node:assert/strict";
import { test } from "node:test";
import { DIRECT_ROUTES, installApiDirect, readDirectConfig, routeDirect, tokenFree, type DirectConfig } from "./apiDirect.ts";

const BASE = "https://api-app.axcellent.cloud";
const cfg = readDirectConfig({ VITE_API_DIRECT_BASE: BASE, VITE_API_DIRECT_SERVICES: "dashboard,orch,safety,planning,dev" }) as DirectConfig;
const S = "axs1.abc";
const go = (url: string, o: { method?: string; auth?: string | null; session?: string | null } = {}) =>
  routeDirect({ url, method: o.method ?? "GET", authorization: o.auth ?? null, sessionToken: o.session ?? null, cfg });

test("환경값이 비거나 틀리면 꺼진다", () => {
  assert.equal(readDirectConfig({}), null);
  assert.equal(readDirectConfig({ VITE_API_DIRECT_BASE: BASE }), null);
  assert.equal(readDirectConfig({ VITE_API_DIRECT_SERVICES: "dashboard" }), null);
  assert.equal(readDirectConfig({ VITE_API_DIRECT_BASE: BASE, VITE_API_DIRECT_SERVICES: "nope" }), null);
  assert.equal(readDirectConfig({ VITE_API_DIRECT_BASE: "http://api.example.com", VITE_API_DIRECT_SERVICES: "orch" }), null);
  assert.equal(readDirectConfig({ VITE_API_DIRECT_BASE: `${BASE}/x`, VITE_API_DIRECT_SERVICES: "orch" }), null);
  assert.equal(readDirectConfig({ VITE_API_DIRECT_BASE: "https://u:p@a.example.com", VITE_API_DIRECT_SERVICES: "orch" }), null);
  assert.equal(readDirectConfig({ VITE_API_DIRECT_BASE: `${BASE}/`, VITE_API_DIRECT_SERVICES: "orch, dev" })?.base, BASE);
});

test("꺼져 있으면 fetch 를 바꾸지 않는다", () => {
  const before = globalThis.fetch;
  assert.equal(installApiDirect(null), null);
  assert.equal(globalThis.fetch, before);
});

test("서비스를 고른 것만 직접 간다", () => {
  const only = readDirectConfig({ VITE_API_DIRECT_BASE: BASE, VITE_API_DIRECT_SERVICES: "orch" }) as DirectConfig;
  assert.equal(routeDirect({ url: "/orch/api/incidents", method: "GET", authorization: null, sessionToken: null, cfg: only })?.url, `${BASE}/orch/api/incidents`);
  assert.equal(routeDirect({ url: "/safety-api/api/safety/executions", method: "GET", authorization: null, sessionToken: null, cfg: only }), null);
});

test("토큰이 필요 없는 읽기는 헤더를 건드리지 않는다", () => {
  assert.deepEqual(go("/orch/api/incidents"), { url: `${BASE}/orch/api/incidents` });
  assert.deepEqual(go("/safety-api/api/safety/executions"), { url: `${BASE}/safety-api/api/safety/executions` });
  assert.deepEqual(go("/dashboard-api/api/dashboard/auth/company"), { url: `${BASE}/dashboard-api/api/dashboard/auth/company` });
});

test("기획·개발 state는 세션 토큰을 실어 직접 간다 (11/25 회사 분리: 서버가 세션 회사 상태를 준다)", () => {
  for (const u of ["/planning-api/api/planning/state", "/dev-api/api/planning/state"]) {
    assert.equal(go(u), null); // 세션이 없으면 같은 출처
    assert.deepEqual(go(u, { session: S }), { url: `${BASE}${u}`, authorization: `Bearer ${S}` });
  }
});

test("dashboard 읽기는 세션 토큰이 있을 때만 직접 간다", () => {
  const u = "/dashboard-api/api/dashboard/communications?dept=a";
  assert.equal(go(u), null);
  assert.equal(go(u, { auth: "Bearer human-via-nginx" }), null);
  assert.equal(go(u, { auth: "Bearer human-via-nginx", session: S }), null);
  assert.deepEqual(go(u, { auth: `Bearer ${S}` }), { url: `${BASE}${u}` });
  assert.deepEqual(go(u, { session: S }), { url: `${BASE}${u}`, authorization: `Bearer ${S}` });
});

test("쓰기 메서드와 허용 밖 경로는 같은 출처", () => {
  assert.equal(go("/dashboard-api/api/dashboard/claims/1/approve", { method: "POST", auth: `Bearer ${S}` }), null);
  assert.equal(go("/orch/api/incidents", { method: "POST" }), null);
  assert.equal(go("/dashboard-api/api/dashboard/claims", { auth: `Bearer ${S}` }), null);
  assert.equal(go("/dashboard-api/api/dashboard/auth/accounts", { auth: `Bearer ${S}` }), null);
  assert.equal(go("/orch/api/requests", { auth: `Bearer ${S}` }), null);
  assert.equal(go("/orch/api/incidents/abc"), null);
  assert.equal(go("/dashboard-api/api/dashboard/communications/../claims", { session: S }), null);
  assert.equal(go("//evil.example.com/orch/api/incidents"), null);
  assert.equal(go("https://x.example.com/orch/api/incidents"), null);
});

test("허용 표에는 읽기 전용 경로만 있다 (쓰기·관리자 경로 이름이 없다)", () => {
  for (const r of DIRECT_ROUTES) assert.ok(!/accounts|secret|cost-limits|cost-policy|marketing|settlements|claims|approve|decision|demo/.test(r.path.source), r.path.source);
});

test("직접 호스트로 감: 주소·헤더 확인, 세션 토큰 실음", async () => {
  const seen: { url: unknown; init?: RequestInit }[] = [];
  const restore = installApiDirect(cfg, { fetch: (async (u: unknown, i?: RequestInit) => { seen.push({ url: u, init: i }); return new Response("{}"); }) as typeof fetch, sessionToken: () => S });
  assert.ok(restore);
  try {
    await globalThis.fetch("/dashboard-api/api/dashboard/office-room", { cache: "no-store" });
    assert.equal(seen[0].url, `${BASE}/dashboard-api/api/dashboard/office-room`);
    assert.equal(new Headers(seen[0].init?.headers).get("authorization"), `Bearer ${S}`);
    const init = { cache: "no-store" as const };
    await globalThis.fetch("/orch/api/incidents", init);
    assert.equal(seen[1].url, `${BASE}/orch/api/incidents`);
    assert.equal(seen[1].init, init); // 헤더를 건드리지 않아 단순 요청 그대로
    await globalThis.fetch("/dashboard-api/api/dashboard/claims");
    assert.equal(seen[2].url, "/dashboard-api/api/dashboard/claims");
  } finally { restore(); }
});

test("직접 호스트 실패(TypeError)면 같은 출처로 다시 보내고 쉬는 동안은 바로 같은 출처", async () => {
  const calls: string[] = [];
  let t = 1000;
  const f = (async (u: unknown) => { calls.push(String(u)); if (String(u).startsWith(BASE)) throw new TypeError("Failed to fetch"); return new Response("{}"); }) as typeof fetch;
  const restore = installApiDirect(cfg, { fetch: f, now: () => t, sessionToken: () => null })!;
  try {
    await globalThis.fetch("/orch/api/incidents");
    assert.deepEqual(calls, [`${BASE}/orch/api/incidents`, "/orch/api/incidents"]);
    await globalThis.fetch("/orch/api/incidents");
    assert.equal(calls.length, 3);
    assert.equal(calls[2], "/orch/api/incidents");
    t += 61_000;
    await globalThis.fetch("/orch/api/incidents");
    assert.equal(calls[3], `${BASE}/orch/api/incidents`);
  } finally { restore(); }
});

test("중단 신호는 되돌리지 않고 그대로 던진다", async () => {
  const f = (async () => { throw new DOMException("aborted", "AbortError"); }) as typeof fetch;
  const restore = installApiDirect(cfg, { fetch: f })!;
  try { await assert.rejects(globalThis.fetch("/orch/api/incidents"), { name: "AbortError" }); } finally { restore(); }
});

test("토큰을 안 보는 읽기(tokenFree): 표의 auth none 경로만 참, 쿼리는 무시, 다른 경로·서비스는 거짓", () => {
  assert.equal(tokenFree("safety", "/api/safety/executions"), true);
  assert.equal(tokenFree("orch", "/api/incidents?limit=5"), true);
  // 기획·개발 state는 세션 회사로 그 회사 상태를 고르므로 세션 토큰을 싣는다 (11/25 회사 분리)
  assert.equal(tokenFree("planning", "/api/planning/state"), false);
  assert.equal(tokenFree("dev", "/api/planning/state"), false);
  assert.equal(tokenFree("safety", "/api/safety/reviews"), false);
  assert.equal(tokenFree("orch", "/api/requests"), false);
  assert.equal(tokenFree("planning", "/api/planning/review-queue"), false);
  assert.equal(tokenFree("dashboard", "/api/dashboard/auth/me"), false);
});
