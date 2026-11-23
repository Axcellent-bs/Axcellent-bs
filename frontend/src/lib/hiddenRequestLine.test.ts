// node --test로 돈다 (npm test). 삭제한 의뢰(hidden_at)는 대화방 진행 카드 대신 회색 한 줄 (lib/requestCards.ts hiddenRequestLine).
import assert from "node:assert/strict";
import { test } from "node:test";
import { hiddenRequestLine } from "./requestCards.ts";

const RID = "wr_bf16beceb9";

test("hidden_at이 있으면 한 줄: '삭제한 의뢰 · <제목> (<번호>)' + 사람 토큰이 있으면 되돌리기", () => {
  assert.deepEqual(hiddenRequestLine({ request_id: RID, hidden_at: "2026-11-23T10:00:00+00:00" }, "홍보 영상 만들기", true), {
    text: `삭제한 의뢰 · 홍보 영상 만들기 (${RID})`, canUndo: true,
  });
});

test("hidden_at이 없으면(null·빈 값·칸 없음) 접지 않는다 → 카드 전체", () => {
  assert.equal(hiddenRequestLine({ request_id: RID, hidden_at: null }, "홍보 영상 만들기", true), null);
  assert.equal(hiddenRequestLine({ request_id: RID, hidden_at: "" }, "홍보 영상 만들기", true), null);
  assert.equal(hiddenRequestLine({ request_id: RID }, "홍보 영상 만들기", true), null);
});

test("사람 토큰이 없으면 되돌리기를 보이지 않는다", () => {
  assert.equal(hiddenRequestLine({ request_id: RID, hidden_at: "2026-11-23T10:00:00+00:00" }, "제목", false)?.canUndo, false);
});
