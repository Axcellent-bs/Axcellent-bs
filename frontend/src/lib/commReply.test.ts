// node --test로 돈다 (npm test). 부서가 긴 작업으로 받았다고 답한 줄(B → A 진행 중)을 "부탁"이 아니라 "받았어요"로 가르는 규칙
// 11/24 운영: "데이터분석 → 총괄 · 트렌드 분석 보고서 부탁해요", "마케팅 → 총괄 · 영상 제작 부탁해요"처럼 되돌려 부탁하는 것으로 보였다
import assert from "node:assert/strict";
import { test } from "node:test";
import { replyLogIds } from "./commReply.ts";
import type { CommLine } from "./types";

let n = 0;
const line = (at: string, from: string, to: string | null, skill: string | null, outcome: CommLine["outcome"], context_id: string | null): CommLine =>
  ({ log_id: `L${++n}`, occurred_at: `2026-11-24T${at}+09:00`, from_agent: from, to_agent: to, skill, outcome, context_id, execution_id: null, grade: "B" });

const WR = "ctx_wr_trend01";

test("총괄 → 데이터 부탁 뒤 데이터 → 총괄 진행 중은 답장(받았어요)", () => {
  const ask = line("17:48:00", "orchestrator", "data", "trend_report_create", "in_progress", WR);
  const ack = line("17:48:02", "data", "orchestrator", "trend_report_create", "in_progress", WR);
  const done = line("17:49:00", "data", "orchestrator", "trend_report_create", "completed", WR);
  const ids = replyLogIds([ask, ack, done]);
  assert.deepEqual([...ids], [ack.log_id]);
});

test("총괄 → 마케팅 영상 제작 뒤 마케팅 → 총괄 진행 중은 답장(받았어요)", () => {
  const ask = line("17:50:00", "orchestrator", "marketing", "video_make", "in_progress", WR);
  const ack = line("17:50:01", "marketing", "orchestrator", "video_make", "in_progress", WR);
  assert.ok(replyLogIds([ask, ack]).has(ack.log_id));
  assert.ok(!replyLogIds([ask, ack]).has(ask.log_id));
});

test("시각이 같으면 받은 순서로 본다, 순서가 뒤섞여 와도 시각으로 짝을 찾는다", () => {
  const ask = line("17:50:00", "orchestrator", "marketing", "video_make", "in_progress", WR);
  const ack = line("17:50:00", "marketing", "orchestrator", "video_make", "in_progress", WR);
  assert.deepEqual([...replyLogIds([ask, ack])], [ack.log_id]);
  const late = line("17:50:05", "marketing", "orchestrator", "video_make", "in_progress", WR);
  assert.deepEqual([...replyLogIds([late, ask])], [late.log_id]);
});

test("짝 없는 진행 중은 부탁 그대로 (앞 부탁 없음 · 앞 줄이 같은 방향 · 다른 상대)", () => {
  const alone = line("17:51:00", "data", "orchestrator", "trend_report_create", "in_progress", WR);
  assert.equal(replyLogIds([alone]).size, 0);
  const a1 = line("17:51:00", "orchestrator", "data", "trend_report_create", "in_progress", WR);
  const a2 = line("17:51:05", "orchestrator", "data", "trend_report_create", "in_progress", WR);
  assert.equal(replyLogIds([a1, a2]).size, 0);
  const toData = line("17:52:00", "orchestrator", "data", "trend_report_create", "in_progress", WR);
  const fromPlanning = line("17:52:30", "planning", "orchestrator", "trend_report_create", "in_progress", WR);
  assert.equal(replyLogIds([toData, fromPlanning]).size, 0);
  // 앞 부탁이 끝난(completed) 줄이면 진행 중 부탁이 아니라 짝이 아니다
  const doneAsk = line("17:53:00", "orchestrator", "data", "trend_report_create", "completed", WR);
  const back = line("17:53:05", "data", "orchestrator", "trend_report_create", "in_progress", WR);
  assert.equal(replyLogIds([doneAsk, back]).size, 0);
});

test("다른 context·다른 skill이면 짝이 아니다 (context 기록이 없어도 짝이 아니다)", () => {
  const ask = line("17:54:00", "orchestrator", "data", "trend_report_create", "in_progress", WR);
  const otherCtx = line("17:54:01", "data", "orchestrator", "trend_report_create", "in_progress", "ctx_wr_other");
  const otherSkill = line("17:54:02", "data", "orchestrator", "keyword_trend", "in_progress", WR);
  assert.equal(replyLogIds([ask, otherCtx, otherSkill]).size, 0);
  const noCtxAsk = line("17:55:00", "orchestrator", "data", "trend_report_create", "in_progress", null);
  const noCtxAck = line("17:55:01", "data", "orchestrator", "trend_report_create", "in_progress", null);
  assert.equal(replyLogIds([noCtxAsk, noCtxAck]).size, 0);
  const noSkillAsk = line("17:56:00", "orchestrator", "data", null, "in_progress", WR);
  const noSkillAck = line("17:56:01", "data", "orchestrator", null, "in_progress", WR);
  assert.equal(replyLogIds([noSkillAsk, noSkillAck]).size, 0);
});

test("안전 관문(from safety) 줄은 답장으로 보지 않는다 (문구 그대로: 실행해도 되는지 확인하고 있어요)", () => {
  const ask = line("17:57:00", "support", "safety", "refund_request", "in_progress", WR);
  const check = line("17:57:01", "safety", "support", "refund_request", "in_progress", WR);
  assert.equal(replyLogIds([ask, check]).size, 0);
});

test("두 번째 부탁에도 다시 짝이 된다 (부탁 → 답장 → 다시 부탁 → 답장)", () => {
  const ask1 = line("18:00:00", "orchestrator", "data", "trend_report_create", "in_progress", WR);
  const ack1 = line("18:00:01", "data", "orchestrator", "trend_report_create", "in_progress", WR);
  const ask2 = line("18:05:00", "orchestrator", "data", "trend_report_create", "in_progress", WR);
  const ack2 = line("18:05:01", "data", "orchestrator", "trend_report_create", "in_progress", WR);
  assert.deepEqual([...replyLogIds([ask1, ack1, ask2, ack2])].sort(), [ack1.log_id, ack2.log_id].sort());
});
