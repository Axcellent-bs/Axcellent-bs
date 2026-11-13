// node --test로 돈다 (npm test). 값은 251 시나리오 시험 사건(A·B·C)의 기록을 줄여 옮긴 것.
import assert from "node:assert/strict";
import { test } from "node:test";
import type { OrchIncident, OrchStep } from "./agentChat.ts";
import { compareByType, faultKindOf, pickedByRule, recoveryEndOf, verifiedAtOf } from "./incidentFacts.ts";

function step(step_id: string, status: string, output: Record<string, unknown> = {}, finished_at: string | null = null): OrchStep {
  return { step_id, skill: step_id, label: step_id, dept: null, status, attempts: 1, note: "", grade: null, started_at: null,
    finished_at, transferred_to: null, reason_code: null, output } as OrchStep;
}

function incident(over: Partial<OrchIncident>): OrchIncident {
  return { context_id: "ctx_inc_t", kind: "scenario_a", title: "결제", component: "payment", status: "resolved", opened_at: null,
    closed_at: null, steps: [], events: [], summary: [], metrics: {}, pending_holds: [], ...over };
}

// A: 장애 스위치 → 인프라 AI 해제 (ctx_inc_72b559d45bf5)
const A = incident({
  context_id: "ctx_inc_72b559d45bf5", event_type: "payment:error_rate_5xx",
  metrics: { mttr_seconds: 37.96 },
  timeline: { detected_at: "2026-11-13T05:45:59.154633+00:00", recovered_at: "2026-11-13T05:46:37.114443+00:00" },
  shared: { recovery: { recovery: "fault_released", released_by: "infra" } },
  steps: [
    step("past_fix", "completed", { fault_released: true, post_check_ok: true }),
    step("recovery", "completed", { recovered: true, checked_at: "2026-11-13T05:47:02.360090Z" }, "2026-11-13T05:47:02Z"),
  ],
});
// B: 결제사 지연 → 사람 이관, 조치 기록 없음 (ctx_inc_8e86d790a7db)
const B = incident({
  context_id: "ctx_inc_8e86d790a7db", event_type: "payment:p95_latency_ms",
  metrics: { mttr_seconds: 206.32 },
  timeline: { detected_at: "2026-11-13T05:48:42.925809+00:00", recovered_at: "2026-11-13T05:52:09.242244+00:00" },
  shared: { recovery: { recovery: "metrics_normal", released_by: null } },
  steps: [step("rollback", "transfer_queued"), step("recovery", "skipped")],
});
// C: 잘못된 배포 → 되돌리기 v2 → v1 (ctx_inc_eaf5f0f70fe1)
const C = incident({
  context_id: "ctx_inc_eaf5f0f70fe1", event_type: "payment:error_rate_5xx",
  metrics: { mttr_seconds: 83.33 },
  timeline: { detected_at: "2026-11-13T05:58:45.186893+00:00", recovered_at: "2026-11-13T06:00:08.521675+00:00" },
  steps: [
    step("rollback", "completed", { from_version: "v2", to_version: "v1", payment_version: { before: "v2", after: "v1" }, post_check_ok: true }),
    step("recovery", "completed", { recovered: true, checked_at: "2026-11-13T06:00:19.674340Z" }),
  ],
});

test("장애 종류: 쇼핑몰 신호가 먼저, 없으면 기록된 조치로, 그것도 없으면 모른다", () => {
  assert.equal(faultKindOf(A), "장애 스위치");
  assert.equal(faultKindOf(C), "잘못된 배포");
  assert.equal(faultKindOf(B), null); // 예전 B에는 결제사 지연이었다는 기록이 없다
  assert.equal(faultKindOf({ ...B, fault_kind: "pg_slow" }), "결제사 지연");
  assert.equal(faultKindOf({ ...B, fault_kind: "모르는 값" }), null);
});

test("정상 복귀는 복구 시간 카드와 같은 끝 시각이다", () => {
  // 예전 총괄(recovery_end_at 없음): 감지 + 복구 시간 = 쇼핑몰 복구 신호와 같다
  assert.equal(recoveryEndOf(A), "2026-11-13T05:46:37.114Z");
  assert.equal(recoveryEndOf(B), "2026-11-13T05:52:09.245Z");
  assert.equal(recoveryEndOf({ ...A, recovery_end_at: "2026-11-13T05:46:37.114443Z" }), "2026-11-13T05:46:37.114443Z");
  assert.equal(recoveryEndOf(incident({ timeline: { recovered_at: "2026-11-13T05:00:00Z" } })), "2026-11-13T05:00:00Z");
});

test("복구 검증 끝은 검증이 복구됨으로 끝났을 때만", () => {
  assert.equal(verifiedAtOf(A), "2026-11-13T05:47:02.360090Z");
  assert.equal(verifiedAtOf(B), null);
  const noChecked = incident({ steps: [step("recovery", "completed", { recovered: true }, "2026-11-13T05:47:02Z")] });
  assert.equal(verifiedAtOf(noChecked), "2026-11-13T05:47:02Z");
  assert.equal(verifiedAtOf(incident({ steps: [step("recovery", "completed", { recovered: false })] })), null);
});

test("시간 순서 규칙으로 고른 원인 후보를 안다 (예전 결과는 요약 문구로)", () => {
  assert.equal(pickedByRule({ method: "rule" }), true);
  assert.equal(pickedByRule({ method: "llm", summary: "시간 순서 규칙으로 고름" }), false);
  assert.equal(pickedByRule({ summary: "원인 후보 2개 (근거 사실 2개) · 시간 순서 규칙으로 고름 · 사람 확인 필요" }), true);
  assert.equal(pickedByRule(null), false);
});

test("복구 시간 비교는 같은 지표 종류끼리만, 하나뿐인 종류는 문장 없이", () => {
  const typeOf = (id: string) => [A, B, C].find((i) => i.context_id === id)?.event_type;
  const rows = [A, B, C].map((i) => ({ context_id: i.context_id, mttr_seconds: i.metrics.mttr_seconds }));
  // A(38초)와 C(83초)는 같은 결제 오류, B(206초)는 결제 응답 지연 하나뿐 → B는 비교에 들어가지 않는다
  assert.deepEqual(compareByType(rows, typeOf), ["결제 오류(5xx): 1차보다 마지막 사건이 120% 느려졌어요"]);
  assert.deepEqual(compareByType(rows.slice(0, 2), typeOf), []);
  assert.deepEqual(compareByType(rows, () => null), []); // 종류를 모르면 비교하지 않는다
});
