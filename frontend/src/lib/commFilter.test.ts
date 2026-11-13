// node --test로 돈다 (npm test). 사건이 닫힌 뒤의 조회 줄만 숨기는지.
import assert from "node:assert/strict";
import { test } from "node:test";
import { closedTimes, isBackgroundRead } from "./commFilter.ts";
import type { CommLine } from "./types";

const CTX = "ctx_inc_3fc735633641";
const CLOSED = "2026-11-12T07:08:59Z";
const closed = closedTimes([
  { context_id: CTX, closed_at: CLOSED },
  { context_id: "ctx_inc_open", closed_at: null },
]);

const line = (over: Partial<CommLine>): CommLine => ({
  log_id: "log_1", occurred_at: "2026-11-12T07:43:00Z", from_agent: "finance", to_agent: "data",
  skill: "impact_analysis", outcome: "completed", context_id: CTX, execution_id: null, grade: "read", ...over,
});

test("닫힌 뒤 재무가 다시 물은 영향 분석 조회는 숨긴다", () => {
  assert.equal(isBackgroundRead(line({}), closed), true);
  assert.equal(isBackgroundRead(line({ from_agent: "data", to_agent: "finance" }), closed), true); // 답도
});

// 공통 로그 소통 줄의 실패는 rejected로 온다 (CommOutcome에 failed는 없다)
test("닫힌 뒤라도 실패(거절)·보류·사람 확인 줄은 보인다", () => {
  for (const outcome of ["rejected", "held", "needs_human"] as const) {
    assert.equal(isBackgroundRead(line({ outcome }), closed), false, outcome);
  }
});

test("닫힌 뒤라도 쓰기 작업(read가 아닌 등급) 줄은 보인다", () => {
  assert.equal(isBackgroundRead(line({ grade: "B", skill: "record_incident_finance" }), closed), false);
});

test("닫히기 전에 생긴 조회는 보인다 (일의 흐름)", () => {
  assert.equal(isBackgroundRead(line({ occurred_at: "2026-11-12T07:01:40Z" }), closed), false);
});

test("진행 중 사건의 조회는 보인다", () => {
  assert.equal(isBackgroundRead(line({ context_id: "ctx_inc_open" }), closed), false);
  assert.equal(isBackgroundRead(line({ context_id: "ctx_inc_unknown" }), closed), false);
});

test("닫힌 뒤라도 총괄이 오간 조회는 보인다 (복구 확인 등 닫힌 뒤 단계)", () => {
  assert.equal(isBackgroundRead(line({ from_agent: "orchestrator", to_agent: "incident", skill: "verify_recovery" }), closed), false);
});

test("사건·요청 밖 조회는 예전처럼 숨긴다 (team_headcount 등)", () => {
  const headcount = line({ from_agent: "finance", to_agent: "org", skill: "team_headcount", context_id: "ctx_team_budget_2026-11" });
  assert.equal(isBackgroundRead(headcount), true);
  assert.equal(isBackgroundRead(headcount, closed), true);
});

test("닫힌 시각 목록이 없으면 사건 안 조회는 보인다 (예전 동작)", () => {
  assert.equal(isBackgroundRead(line({})), false);
});
