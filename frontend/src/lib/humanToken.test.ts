// node --test로 돈다 (npm test). 화면이 쓰는 사람 토큰과 "쓰기 버튼을 켤지"(HUMAN_TOKEN_SET)가
// 로그인·로그아웃을 따라 바뀌는지 본다. 공용 사람 토큰을 꺼도(빈 값) 로그인한 사람의 버튼이 켜져야 한다.
import assert from "node:assert/strict";
import { afterEach, test } from "node:test";
import { setSession, setStoreForTests, type HumanSession, type KeyValueStore } from "./humanSession.ts";
import * as live from "./humanToken.ts";

const account = { id: "acc-1", name: "예진", email: "yj@example.com", role: "admin" as const, created_at: null };
const session = (): HumanSession => ({ token: "axs1.body.sig", expiresAt: Math.floor(Date.now() / 1000) + 3600, account });
const memoryStore = (): KeyValueStore => {
  const data: Record<string, string> = {};
  return { getItem: (k) => data[k] ?? null, setItem: (k, v) => { data[k] = v; }, removeItem: (k) => { delete data[k]; } };
};

afterEach(() => {
  setSession(null);
  setStoreForTests(null);
  live.setSharedHumanToken("");
});

test("공용 토큰도 세션도 없으면 사람 토큰이 비고 쓰기 버튼이 꺼진다", () => {
  setStoreForTests(memoryStore());
  live.setSharedHumanToken("");
  assert.equal(live.SAFETY_HUMAN_TOKEN, "");
  assert.equal(live.HUMAN_TOKEN_SET, false);
});

test("공용 토큰 없이 페이지를 연 뒤 로그인하면 바로 세션 토큰을 쓰고 버튼이 켜진다 (새로고침 없이)", () => {
  setStoreForTests(memoryStore());
  live.setSharedHumanToken("");
  assert.equal(live.HUMAN_TOKEN_SET, false);
  setSession(session());
  assert.equal(live.SAFETY_HUMAN_TOKEN, "axs1.body.sig");
  assert.equal(live.HUMAN_TOKEN_SET, true);
});

test("로그아웃하면 다시 꺼지고, 다시 로그인하면 다시 켜진다", () => {
  setStoreForTests(memoryStore());
  live.setSharedHumanToken("");
  setSession(session());
  assert.equal(live.HUMAN_TOKEN_SET, true);
  setSession(null);
  assert.equal(live.SAFETY_HUMAN_TOKEN, "");
  assert.equal(live.HUMAN_TOKEN_SET, false);
  setSession(session());
  assert.equal(live.HUMAN_TOKEN_SET, true);
});

test("공용 토큰이 있는 지금 동작은 그대로: 세션이 없으면 공용 토큰, 세션이 있으면 세션 토큰", () => {
  setStoreForTests(memoryStore());
  live.setSharedHumanToken("human-via-nginx");
  assert.equal(live.SAFETY_HUMAN_TOKEN, "human-via-nginx");
  assert.equal(live.HUMAN_TOKEN_SET, true);
  setSession(session());
  assert.equal(live.SAFETY_HUMAN_TOKEN, "axs1.body.sig");
  assert.equal(live.HUMAN_TOKEN_SET, true);
  setSession(null);
  assert.equal(live.SAFETY_HUMAN_TOKEN, "human-via-nginx");
  assert.equal(live.HUMAN_TOKEN_SET, true);
});

test("버튼이 켜짐·꺼짐으로 바뀔 때 듣는 쪽(화면 다시 그리기)에 알린다", () => {
  setStoreForTests(memoryStore());
  live.setSharedHumanToken("");
  const seen: boolean[] = [];
  const off = live.onHumanTokenChange(() => seen.push(live.humanTokenSetNow()));
  setSession(session());
  setSession(null);
  off();
  setSession(session());
  assert.deepEqual(seen, [true, false]);
});

test("읽기 머리(sessionAuth): 세션이 있을 때만 세션 토큰을 싣고, 없으면 비운다(공용 토큰은 싣지 않는다)", () => {
  setStoreForTests(memoryStore());
  live.setSharedHumanToken("human-via-nginx");
  assert.deepEqual(live.sessionAuth(), {});
  setSession(session());
  assert.deepEqual(live.sessionAuth(), { Authorization: "Bearer axs1.body.sig" });
  setSession(null);
  assert.deepEqual(live.sessionAuth(), {});
});
