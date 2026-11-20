// node --test로 돈다 (npm test). 단톡방 대화 공유: shared → 말풍선, restricted → 지금 문장, room_id는 safety·hr·archive 단톡방 보내기에만, 남의 대화에 이어 쓰지 않음.
import assert from "node:assert/strict";
import { test } from "node:test";
import { ROOM_ID_AGENTS, notThreadOwner, officeSendBody, ownsThread, senderName, threadDisplay } from "./officeTalkView.ts";

test("shared 대화는 남의 것이라도 말풍선, restricted(1:1·예전 줄)는 지금 문장", () => {
  assert.deepEqual(threadDisplay({ shared: true, restricted: false }, "재현", "총괄 AI"), { kind: "bubbles" });
  // shared가 먼저 (서버가 실수로 둘 다 보내도 원문을 그린다)
  assert.deepEqual(threadDisplay({ shared: true, restricted: true }, "재현", "총괄 AI"), { kind: "bubbles" });
  assert.deepEqual(threadDisplay({ restricted: true }, "재현", "총괄 AI"), { kind: "restricted", text: "재현님이 총괄 AI와 나눈 대화 (원문은 본인만 봐요)" });
  assert.deepEqual(threadDisplay({ restricted: true, summary: "홍보 영상 의뢰" }, "재현", "총괄 AI"), { kind: "restricted", text: "재현님이 총괄 AI와 나눈 대화: 홍보 영상 의뢰" });
  assert.deepEqual(threadDisplay({}, "나", "데이터분석 AI"), { kind: "bubbles" }); // 내 대화 (예전 서버: shared 칸 없음)
});

test("보낸 사람 줄: 내 대화면 내 이름, 남의 대화는 owner, 없으면 목록 from_name", () => {
  assert.equal(senderName({ mine: true, accountName: "태윤", owner: "재현", fromName: "재현" }), "태윤");
  assert.equal(senderName({ mine: false, accountName: "태윤", owner: "재현", fromName: "목록 이름" }), "재현");
  assert.equal(senderName({ mine: false, accountName: "태윤", owner: null, fromName: "재현" }), "재현");
  assert.equal(senderName({ mine: false, accountName: "태윤", owner: " ", fromName: "" }), "다른 사람");
});

test("room_id는 safety·hr·archive의 단톡방 보내기에만, 8곳 중계 부서는 body 그대로", () => {
  assert.deepEqual([...ROOM_ID_AGENTS].sort(), ["archive", "hr", "safety"]);
  assert.deepEqual(officeSendBody({ agent: "safety", room: "all", threadId: null, message: "안녕" }), { message: "안녕", room_id: "all" });
  assert.deepEqual(officeSendBody({ agent: "hr", room: "inc_42", threadId: "th_1", message: "이어서" }), { thread_id: "th_1", message: "이어서", room_id: "inc_42" });
  assert.deepEqual(officeSendBody({ agent: "archive", room: "all", threadId: null, message: "x" }).room_id, "all");
  for (const agent of ["dev", "data", "support", "finance", "planning", "marketing", "infra", "orchestrator"]) {
    assert.deepEqual(officeSendBody({ agent, room: "all", threadId: null, message: "m" }), { message: "m" }, agent);
  }
  // 방 번호가 비었거나 64자 넘으면 넣지 않는다 (공통 약속 1~64자)
  assert.equal("room_id" in officeSendBody({ agent: "safety", room: " ", threadId: null, message: "m" }), false);
  assert.equal("room_id" in officeSendBody({ agent: "safety", room: "x".repeat(65), threadId: null, message: "m" }), false);
});

test("남의 대화에 이어 쓰지 않는다: 목록 from_id · 이 브라우저 owners · 서버 owner가 모두 나여야", () => {
  const me = { id: "u_taeyun", name: "태윤" };
  assert.equal(ownsThread({ fromId: "u_taeyun", threadId: "th_1" }, me), true);
  assert.equal(ownsThread({ fromId: "u_taeyun", threadId: "th_1", owner: "태윤" }, me), true);
  assert.equal(ownsThread({ fromId: "u_taeyun", threadId: "th_1", owner: "u_taeyun" }, me), true);
  // 재현 님이 건 대화 (shared라 원문은 보이지만 이어 쓰지 않는다)
  assert.equal(ownsThread({ fromId: "u_jaehyeon", threadId: "th_2", owner: "재현" }, me), false);
  // 목록은 나로 올라 있어도 서버 owner가 다른 사람이면 쓰지 않는다
  assert.equal(ownsThread({ fromId: "u_taeyun", threadId: "th_3", owner: "재현" }, me), false);
  // 이 브라우저가 다른 계정의 대화로 기억하면 쓰지 않는다
  assert.equal(ownsThread({ fromId: "u_taeyun", threadId: "th_4" }, me, { th_4: "u_jaehyeon" }), false);
  assert.equal(ownsThread({ fromId: "u_taeyun", threadId: "th_5" }, me, { th_5: "u_taeyun" }), true);
});

test("POST /office-room 403 NOT_THREAD_OWNER만 '남의 대화' 거절로 본다", () => {
  assert.equal(notThreadOwner({ status: 403, code: "NOT_THREAD_OWNER" }), true);
  assert.equal(notThreadOwner({ status: 403, code: "FORBIDDEN" }), false);
  assert.equal(notThreadOwner({ status: 500, code: "NOT_THREAD_OWNER" }), false);
  assert.equal(notThreadOwner(new Error("x")), false);
  assert.equal(notThreadOwner(null), false);
});
