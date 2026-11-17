import assert from "node:assert/strict";
import { test } from "node:test";
import { beforeAfter, canRevoke, countByAgent, deliveryOf, isQuiet, type AgentDiagnosisRow } from "./agentDiagnosis.ts";
import type { EvaluatedEvent } from "./agentEval.ts";

const row = (over: Partial<AgentDiagnosisRow> = {}): AgentDiagnosisRow => ({
  request_id: "diag-1", agent: "infra", step_id: "rollback", step_label: "문제 배포 되돌리기",
  window: 3, flagged: 2, criteria_version: 3, evidence: [], status: "diagnosed",
  received_at: "2026-11-17T01:00:00Z", updated_at: "2026-11-17T01:00:00Z",
  delivery: { note_id: "note-diag-1", status: "accepted", reason: null, attempts: 1, delivered_at: "2026-11-17T03:00:00Z" },
  ...over,
});

const event = (at: string, verdict: string, over: Partial<EvaluatedEvent> = {}): EvaluatedEvent => ({
  context_id: `ctx_${at}`, title: `이슈 ${at}`, event_type: null, at, criteria_version: 3,
  steps: [{
    step_id: "rollback", label: "되돌리기", dept: "infra", group: "g", verdict: verdict as never,
    work_seconds: 1, approval_wait_seconds: null, human_wait_seconds: null, attempts: 1, limit_seconds: 30,
    promised: true, max_silence_seconds: null, reason: null,
  }],
  ...over,
});

test("전달 시각 앞뒤로 같은 단계의 확인 필요를 센다", () => {
  const events = [
    event("2026-11-17T01:00:00Z", "check"),
    event("2026-11-17T02:00:00Z", "normal"),
    event("2026-11-17T04:00:00Z", "normal"),
    event("2026-11-17T05:00:00Z", "silent"),
    event("2026-11-17T06:00:00Z", "normal"),
  ];
  const c = beforeAfter(row(), events);
  assert.deepEqual(c?.before, { events: 2, late: 1 });
  assert.deepEqual(c?.after, { events: 3, late: 1 });
});

test("기준값 판이 다른 이슈, 다른 단계, 판정할 수 없는 기록은 세지 않는다", () => {
  const events = [
    event("2026-11-17T04:00:00Z", "check", { criteria_version: 2 }),
    event("2026-11-17T04:30:00Z", "handed_off"),
    event("2026-11-17T04:40:00Z", "no_record"),
    event("2026-11-17T04:50:00Z", "in_progress"),
    event("2026-11-17T05:00:00Z", "check", { steps: [{ ...event("x", "check").steps[0], step_id: "diagnose" }] }),
    event("2026-11-17T05:10:00Z", "check", { steps: [{ ...event("x", "check").steps[0], dept: "data" }] }),
  ];
  const c = beforeAfter(row(), events);
  assert.deepEqual(c?.before, { events: 0, late: 0 });
  assert.deepEqual(c?.after, { events: 0, late: 0 });
});

test("전달하지 않았거나 시각이 없거나 철회했으면 견주지 않는다", () => {
  const events = [event("2026-11-17T04:00:00Z", "check")];
  assert.equal(beforeAfter(row({ delivery: undefined }), events), null);
  assert.equal(beforeAfter(row({ delivery: { note_id: "n", status: "failed", reason: null, attempts: 1, delivered_at: null } }), events), null);
  assert.equal(beforeAfter(row({ delivery: { note_id: "n", status: "revoked", reason: null, attempts: 1, delivered_at: "2026-11-17T03:00:00Z" } }), events), null);
  assert.equal(beforeAfter(row({ delivery: { note_id: "n", status: "accepted", reason: null, attempts: 1, delivered_at: "아님" } }), events), null);
});

test("철회는 에이전트가 받은 노트만, 숨기는 건 철회·쉬는 것만", () => {
  assert.equal(canRevoke(row()), true);
  assert.equal(canRevoke(row({ delivery: { note_id: "n", status: "duplicate", reason: null, attempts: 1, delivered_at: "2026-11-17T03:00:00Z" } })), true);
  assert.equal(canRevoke(row({ delivery: { note_id: "n", status: "rejected", reason: "자기 일이 아님", attempts: 1, delivered_at: null } })), false);
  assert.equal(canRevoke(row({ delivery: undefined })), false);
  assert.equal(isQuiet(row({ delivery: { note_id: "n", status: "revoked", reason: null, attempts: 1, delivered_at: null } })), true);
  assert.equal(isQuiet(row({ delivery: { note_id: "n", status: "skipped", reason: "쉼", attempts: 0, delivered_at: null } })), true);
  assert.equal(isQuiet(row()), false);
});

test("전달 상태 문구와 에이전트별 건수", () => {
  assert.equal(deliveryOf(row())?.label, "에이전트가 받았어요");
  assert.equal(deliveryOf(row({ delivery: undefined })), null);
  assert.deepEqual(countByAgent([row(), row({ request_id: "diag-2" }), row({ agent: "data" })]), { infra: 2, data: 1 });
});
