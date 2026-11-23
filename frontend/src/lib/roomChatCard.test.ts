// node --test로 돈다 (npm test). 대화방에서 데이터분석 "분석 의뢰 카드"가 안 그려지던 문제 (11/23): 대화방 항목 변환.
import assert from "node:assert/strict";
import { test } from "node:test";
import { roomCardField } from "./officeTalkView.ts";

const request = { kind: "trend_request", request_text: "20대 여성 그림책 트렌드 분석해 줘", brief: {} };

test("대화방 항목에 trend_request 카드가 남는다 (1:1과 같은 메시지 id·대화 id)", () => {
  assert.deepEqual(roomCardField({ id: "msg_1", card: request }, "th_9"), {
    chatCard: { card: request, messageId: "msg_1", threadId: "th_9" },
  });
});

test("진행·완료 카드도 남고, 대화 id가 없으면 null", () => {
  const progress = { kind: "trend_report_progress", report_id: "tr_1" };
  const done = { kind: "trend_report_done", report_id: "tr_1", status: "done", summary_line: "", top_evidence: [], report_path: "/data/trend#tr_1" };
  assert.equal(roomCardField({ id: "m2", card: progress }, "th_9").chatCard?.card.kind, "trend_report_progress");
  assert.equal(roomCardField({ id: "m3", card: done }, undefined).chatCard?.threadId, null);
});

test("분석 카드가 아니면 칸을 더하지 않는다 (이미지 시트 카드는 따로 그린다)", () => {
  assert.deepEqual(roomCardField({ id: "m4" }, "th_9"), {});
  assert.deepEqual(roomCardField({ id: "m5", card: { kind: "creative", record_id: "CR-0123456789ab" } }, "th_9"), {});
  assert.deepEqual(roomCardField({ id: "m6", card: { kind: "unknown" } }, "th_9"), {});
});
