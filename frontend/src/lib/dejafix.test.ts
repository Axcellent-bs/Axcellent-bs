// node --test로 돈다. 장애 대응 화면의 DejaFix 호출(dejafix.ts)이 다른 백엔드 호출처럼 사람 토큰을 붙이는지 본다.
// env.ts는 Vite 전용 값(import.meta.env)을 읽어 node에서 그대로 못 불러오므로, 그 자리에 시험용 env를 끼운다.
// 시험용 env도 실제 env.ts처럼 SAFETY_HUMAN_TOKEN을 살아 있는 바인딩으로 내보낸다(로그인하면 바뀌는 값).
import assert from "node:assert/strict";
import { registerHooks } from "node:module";
import { afterEach, test } from "node:test";

const TEST_ENV = `data:text/javascript,${encodeURIComponent(
  'export const DEJAFIX_API_URL = "/dejafix-api"; export let SAFETY_HUMAN_TOKEN = ""; export function setToken(t) { SAFETY_HUMAN_TOKEN = t }',
)}`;

registerHooks({
  resolve(specifier, context, nextResolve) {
    const fromDejafix = context.parentURL?.endsWith("/lib/dejafix.ts");
    if (fromDejafix && specifier === "./env") return { url: TEST_ENV, shortCircuit: true };
    if (fromDejafix && specifier === "./toolForms") return nextResolve("./toolForms.ts", context);
    return nextResolve(specifier, context);
  },
});

const env = (await import(TEST_ENV)) as { setToken(t: string): void };
const dejafix = await import("./dejafix.ts");

type Call = { url: string; auth: string | null };
const realFetch = globalThis.fetch;
afterEach(() => {
  globalThis.fetch = realFetch;
  env.setToken("");
});

function stub(reply: () => Response): Call[] {
  const calls: Call[] = [];
  globalThis.fetch = async (input: string | URL | Request, init?: RequestInit) => {
    calls.push({ url: String(input), auth: new Headers(init?.headers).get("authorization") });
    return reply();
  };
  return calls;
}

const ok = (body: unknown) => () => new Response(JSON.stringify(body), { status: 200, headers: { "content-type": "application/json" } });

test("모든 DejaFix 호출(현재·최근 장애, 현황, 장애 상세, 연결 상태)에 사람 토큰을 Authorization으로 붙인다", async () => {
  env.setToken("axs1.session.sig");
  const calls = stub(ok({ data: [] }));
  await dejafix.currentIncidents();
  await dejafix.recentIncidents();
  await dejafix.dashboardSummary();
  await dejafix.incidentDetail(42);
  await dejafix.dejafixStatus();
  assert.deepEqual(calls.map((c) => c.url), [
    "/dejafix-api/api/incidents?status=problem&limit=100",
    "/dejafix-api/api/incidents?limit=100",
    "/dejafix-api/api/dashboard/summary",
    "/dejafix-api/api/incidents/42",
    "/dejafix-api/status",
  ]);
  assert.ok(calls.every((c) => c.auth === "Bearer axs1.session.sig"));
});

test("로그인·로그아웃으로 바뀐 토큰을 다음 호출부터 쓴다 (살아 있는 바인딩)", async () => {
  const calls = stub(ok({ data: [] }));
  env.setToken("shared-token");
  await dejafix.currentIncidents();
  env.setToken("axs1.after.login");
  await dejafix.currentIncidents();
  assert.deepEqual(calls.map((c) => c.auth), ["Bearer shared-token", "Bearer axs1.after.login"]);
});

test("사람 토큰이 없으면 헤더 없이 부른다 (예전과 같다)", async () => {
  const calls = stub(ok({ data: [] }));
  await dejafix.currentIncidents();
  await dejafix.dejafixStatus();
  assert.deepEqual(calls.map((c) => c.auth), [null, null]);
});

test("프록시가 토큰을 막은 401은 다시 로그인 안내, DejaFix 계정 문제의 401은 예전 안내", async () => {
  env.setToken("axs1.expired");
  stub(() => new Response(JSON.stringify({ error: { code: "SESSION_EXPIRED", message: "x" } }), { status: 401 }));
  await assert.rejects(dejafix.currentIncidents(), /다시 로그인/);
  stub(() => new Response(JSON.stringify({ detail: "bad credentials" }), { status: 401 }));
  await assert.rejects(dejafix.currentIncidents(), /DejaFix 로그인 정보/);
});

test("연결 상태를 못 읽으면(401 포함) null — 카드는 확인할 수 없음으로 둔다", async () => {
  stub(() => new Response(JSON.stringify({ error: { code: "AUTH_REQUIRED" } }), { status: 401 }));
  assert.equal(await dejafix.dejafixStatus(), null);
});
