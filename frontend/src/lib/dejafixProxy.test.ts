// node --test로 돈다. 개발 서버의 /dejafix-api(dejafix-proxy.ts): 기본은 백엔드 dejafix-proxy(8030)로
// 로그인 없이 그대로 넘기고(보관함 계정은 그 프록시가 쓴다), 계정을 적었을 때만 대신 로그인한다.
import assert from "node:assert/strict";
import { afterEach, test } from "node:test";
import { DEFAULT_TARGET, dejafixHandler } from "../../dejafix-proxy.ts";

type Call = { url: string; method: string; auth: string | null };
const realFetch = globalThis.fetch;
afterEach(() => {
  globalThis.fetch = realFetch;
});

function stub(reply: (url: string) => Response): Call[] {
  const calls: Call[] = [];
  globalThis.fetch = async (input: string | URL | Request, init?: RequestInit) => {
    const url = String(input);
    const headers = new Headers(init?.headers);
    calls.push({ url, method: init?.method ?? "GET", auth: headers.get("authorization") });
    return reply(url);
  };
  return calls;
}

function fakeRes() {
  const out = { statusCode: 0, headers: {} as Record<string, string>, body: "" };
  return {
    out,
    res: {
      get statusCode() { return out.statusCode; },
      set statusCode(v: number) { out.statusCode = v; },
      setHeader(name: string, value: string) { out.headers[name] = value; },
      end(body: string) { out.body = body; },
    },
  };
}

test("기본: 백엔드 dejafix-proxy(8030)로 로그인 없이 그대로 넘긴다", async () => {
  assert.equal(DEFAULT_TARGET, "http://127.0.0.1:8030");
  const calls = stub(() => new Response('{"data":[]}', { status: 200, headers: { "content-type": "application/json" } }));
  const { out, res } = fakeRes();
  await dejafixHandler({})({ method: "GET", url: "/api/incidents?status=problem" }, res);
  assert.deepEqual(calls, [{ url: "http://127.0.0.1:8030/api/incidents?status=problem", method: "GET", auth: null }]);
  assert.equal(out.statusCode, 200);
  assert.equal(out.body, '{"data":[]}');
});

test("기본 경로의 401·503은 다시 로그인하지 않고 그대로 돌려준다 (연결 안내는 화면이 띄운다)", async () => {
  const body = '{"error":{"code":"DEJAFIX_NOT_CONNECTED","message":"연결 없음"}}';
  const calls = stub(() => new Response(body, { status: 503 }));
  const { out, res } = fakeRes();
  await dejafixHandler({ DEJAFIX_PROXY_TARGET: "" })({ method: "GET", url: "/api/incidents" }, res);
  assert.equal(calls.length, 1);
  assert.equal(out.statusCode, 503);
  assert.equal(out.body, body);
});

test("주소와 계정을 직접 적으면 예전처럼 대신 로그인해서 토큰을 붙인다", async () => {
  const calls = stub((url) =>
    url.endsWith("/api/auth/login")
      ? new Response('{"data":{"token":"jwt-1"}}', { status: 200 })
      : new Response("[]", { status: 200 }),
  );
  const { res } = fakeRes();
  const env = { DEJAFIX_PROXY_TARGET: "http://127.0.0.1:18000/", DEJAFIX_USERNAME: "viewer", DEJAFIX_PASSWORD: "pw" };
  await dejafixHandler(env)({ method: "GET", url: "/api/incidents" }, res);
  assert.deepEqual(calls.map((c) => [c.method, c.url]), [
    ["POST", "http://127.0.0.1:18000/api/auth/login"],
    ["GET", "http://127.0.0.1:18000/api/incidents"],
  ]);
  assert.equal(calls[1].auth, "Bearer jwt-1");
});

test("GET이 아니면 넘기지 않는다", async () => {
  const calls = stub(() => new Response("", { status: 200 }));
  const { out, res } = fakeRes();
  await dejafixHandler({})({ method: "POST", url: "/api/incidents" }, res);
  assert.equal(calls.length, 0);
  assert.equal(out.statusCode, 405);
});
