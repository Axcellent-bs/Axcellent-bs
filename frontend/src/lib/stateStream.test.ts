// 기획·개발 상태 스트림 (11/25 회사 분리): 세션이 있으면 세션 토큰을 실은 fetch 스트림, 없으면 EventSource
import { test } from "node:test";
import assert from "node:assert/strict";
import { openStateStream, splitSse } from "./stateStream.ts";

test("SSE 글 나누기: state 이벤트·여러 data 줄·keepalive 주석·끝나지 않은 조각", () => {
  const { events, rest } = splitSse('id: 3\nevent: state\ndata: {"a":1}\n\n: keepalive\n\nevent: state\ndata: {"b":\ndata: 2}\n\nevent: sta');
  assert.deepEqual(events, [{ event: "state", data: '{"a":1}' }, { event: "state", data: '{"b":\n2}' }]);
  assert.equal(rest, "event: sta");
});

test("세션이 있으면 Authorization을 실어 fetch로 받고, 받은 state를 넘긴다", async () => {
  const seen: { url: string; auth: string | null }[] = [];
  const body = new ReadableStream<Uint8Array>({
    start(c) {
      const enc = new TextEncoder();
      c.enqueue(enc.encode('event: state\ndata: {"company":"nubi"}\n\n'));
      c.close();
    },
  });
  const fakeFetch = (async (url: string, init?: RequestInit) => {
    seen.push({ url, auth: new Headers(init?.headers).get("authorization") });
    return new Response(body, { status: 200, headers: { "Content-Type": "text/event-stream" } });
  }) as unknown as typeof fetch;
  const states: string[] = [];
  let opened = 0;
  const done = new Promise<void>((resolve) => {
    const close = openStateStream("/planning-api/api/planning/events", "Bearer axs1.nubi", {
      onState: (d) => { states.push(d); close(); resolve(); },
      onOpen: () => { opened += 1; },
      onError: () => {},
    }, { fetch: fakeFetch, retryMs: 10_000 });
  });
  await done;
  assert.deepEqual(seen, [{ url: "/planning-api/api/planning/events", auth: "Bearer axs1.nubi" }]);
  assert.deepEqual(states, ['{"company":"nubi"}']);
  assert.equal(opened, 1);
});

test("세션이 없으면 예전처럼 EventSource (fetch는 부르지 않는다)", () => {
  const made: string[] = [];
  class FakeES {
    static OPEN = 1;
    readyState = 0;
    onopen: (() => void) | null = null;
    onerror: (() => void) | null = null;
    constructor(url: string) { made.push(url); }
    addEventListener() {}
    close() { made.push("closed"); }
  }
  const close = openStateStream("/dev-api/api/planning/events", null, { onState: () => {}, onOpen: () => {}, onError: () => {} }, {
    EventSource: FakeES as unknown as typeof EventSource,
    fetch: (() => { throw new Error("fetch를 부르면 안 됨"); }) as unknown as typeof fetch,
  });
  close();
  assert.deepEqual(made, ["/dev-api/api/planning/events", "closed"]);
});
