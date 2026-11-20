// node --test로 돈다 (npm test). 개발 AI 대화 패널: 갱신 순서·다시 읽기·안내·도구 연결 버튼.
import assert from "node:assert/strict";
import { test } from "node:test";
import type { ChatThread } from "./planningData.ts";
import {
  KEY_HINT, LIVE_ANSWER_POLL_MS, POLL_MS, canClose, chatNotice, createLatestGate, isShown, mergeThreads, needsTools, pollInterval, sendErrorText, stampKey, toStamps,
} from "./devChatView.ts";

const th = (id: string, status: ChatThread["status"], updatedAt: string, n = 1): ChatThread => ({
  id, title: id, status, messages: Array.from({ length: n }, (_, i) => ({ id: `m${i + 1}`, role: "user" as const, text: "x", at: updatedAt, kind: "text" as const })),
  taskId: null, connection: null, createdAt: updatedAt, updatedAt,
});

test("늦게 끝난 오래된 SSE 갱신은 버린다 (최신 talking을 오래된 answering이 덮지 않음)", () => {
  const g = createLatestGate();
  const older = g.start();
  const newer = g.start();
  assert.equal(g.fresh(newer), true);
  assert.equal(g.fresh(older), false);
  const next = g.start();
  assert.equal(g.fresh(next), true);
});

test("같은 대화는 더 최근 것을 쓴다", () => {
  const sse = [th("chat-1", "answering", "2026-11-20T01:00:00Z")];
  const local = [th("chat-1", "talking", "2026-11-20T01:00:05Z", 2)];
  assert.equal(mergeThreads(sse, local)[0].status, "talking");
  assert.equal(mergeThreads(local, sse)[0].status, "talking");
});

test("SSE가 있어도 답을 기다리는 동안은 다시 읽는다", () => {
  assert.equal(pollInterval(true, true, false), LIVE_ANSWER_POLL_MS);
  assert.equal(pollInterval(true, false, true), null);
  assert.equal(pollInterval(false, false, true), POLL_MS);
  assert.equal(pollInterval(false, false, false), null);
});

test("키 오류·AI 미설정·레포 없음에는 도구 연결 버튼", () => {
  assert.ok(needsTools("LLM_NOT_CONFIGURED"));
  assert.ok(needsTools("NO_VCS"));
  assert.ok(needsTools(null, `AI가 키를 받지 않았어요 (401). ${KEY_HINT}`));
  assert.ok(!needsTools(null, "AI 응답을 받지 못했어요 (LLMResponseError). 다시 보내 주세요."));
  assert.ok(sendErrorText("LLM_NOT_CONFIGURED", "x").includes(KEY_HINT));
  assert.equal(sendErrorText("ELSE", "서버 글"), "서버 글");
});

test("AI 미설정이면 보내기를 막는 안내, 레포가 없으면 대화는 되는 안내, 모르면 없음", () => {
  assert.equal(chatNotice(null, 1), "no_llm");
  assert.equal(chatNotice("m", 0), "no_repo");
  assert.equal(chatNotice("m", 2), null);
  assert.equal(chatNotice(undefined, null), null); // 설정·연결 목록을 아직 못 읽음
});

test("끝내기는 답하는 중·끝난 대화에서 못 한다, 숨긴 대화는 안 보인다", () => {
  assert.ok(canClose(th("a", "talking", "t")));
  assert.ok(canClose(th("a", "handed", "t")));
  assert.ok(!canClose(th("a", "answering", "t")));
  assert.ok(!canClose(th("a", "closed", "t")));
  assert.ok(!canClose(undefined));
  assert.ok(isShown({ removedAt: null }) && !isShown({ removedAt: "2026-11-20T00:00:00Z" }));
});

test("SSE 대화는 id·상태·시각만 읽고, 그게 바뀌면 열쇠가 바뀐다 (원문은 /dev-chat으로)", () => {
  const stamps = toStamps([{ id: "chat-1", status: "talking", updatedAt: "t1", messages: [{ text: "원문" }] }, "x", { status: "talking" }]);
  assert.deepEqual(stamps, [{ id: "chat-1", status: "talking", updatedAt: "t1" }]);
  assert.deepEqual(toStamps(null), []);
  const before = stampKey(stamps);
  assert.equal(stampKey([{ id: "chat-1", status: "talking", updatedAt: "t1" }]), before);
  assert.notEqual(stampKey([{ id: "chat-1", status: "answering", updatedAt: "t2" }]), before);
  assert.equal(stampKey(undefined), "");
});
