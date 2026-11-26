// node --test로 돈다 (npm test). 대화방 사건 롤백 상태 카드(11/26, 읽기 전용): 열린 사건 고르기 · 단계별 상태 · 없는 값은 null.
import assert from "node:assert/strict";
import { test } from "node:test";
import type { OrchIncident, OrchStep } from "./agentChat.ts";
import { NO_OPEN_INCIDENT, REJECT_CONSEQUENCE, openRollbackIncidents, rollbackCardResult, rollbackDecisionOf, rollbackStatusOf, serviceNameMatches } from "./rollbackStatus.ts";

const step = (step_id: string, status: string, output: Record<string, unknown> = {}): OrchStep =>
  ({ step_id, skill: step_id === "rollback" ? "rollback_deployment" : "diagnose_incident", label: step_id, dept: "infra", status, attempts: 1, note: "", grade: null, started_at: null, finished_at: null, transferred_to: null, reason_code: null, output });
const SUSPECT = { suspects: [{ service: "shop-api", version: "v2.4.1", confidence: "high" }] };
const incident = (over: Partial<OrchIncident> & { rollback?: string | null; out?: Record<string, unknown> } = {}): OrchIncident => {
  const { rollback = "held", out = {}, ...rest } = over;
  return {
    context_id: "ctx_inc_a", kind: "scenario_a", title: "결제 장애", component: "payment", status: "open", opened_at: "2026-11-26T01:00:00Z", closed_at: null,
    steps: [step("diagnose", "completed", SUSPECT), ...(rollback ? [step("rollback", rollback, out)] : [])],
    events: [], summary: [], metrics: {}, pending_holds: [], ...rest,
  } as OrchIncident;
};
const HELD = { service: "shop-api", from_version: "v2.4.1", to_version: "v2.4.0", path_params: { suspect_count: 1, low_confidence: 0, irreversible_count: 2, prior_rollbacks: 1 } };

test("열린 사건: 롤백 단계가 있고 진행 중이거나 안 풀린 채 끝난 사건만, 최근 것부터", () => {
  const list = [
    incident({ context_id: "old", opened_at: "2026-11-26T01:00:00Z" }),
    incident({ context_id: "new", opened_at: "2026-11-26T03:00:00Z" }),
    incident({ context_id: "res", status: "resolved" }),
    incident({ context_id: "deg", status: "degraded", opened_at: "2026-11-26T02:00:00Z" }),
    incident({ context_id: "ok", status: "degraded", recovered_at: "2026-11-26T02:30:00Z" }),
    incident({ context_id: "norb", rollback: null }),
  ];
  assert.deepEqual(openRollbackIncidents(list).map((i) => i.context_id), ["new", "deg", "old"]);
});

test("열린 사건이 없으면 거절이 아니라 안내 문장을 돌려준다", () => {
  assert.deepEqual(rollbackCardResult([]), { kind: "none", text: NO_OPEN_INCIDENT });
  assert.match(NO_OPEN_INCIDENT, /열린 사건이 없어요/);
  assert.equal(rollbackCardResult([incident({ status: "resolved" })]).kind, "none");
});

test("승인 대기: 서비스·버전·확신도·불가역 변경·이전 롤백·마감·거절 시 이관 큐", () => {
  const inc = incident({ out: HELD, pending_holds: [{ step_id: "rollback", skill: "rollback_deployment", label: "롤백", review_id: "rev_1", expires_at: "2026-11-26T01:10:00Z" }] });
  const s = rollbackStatusOf(inc);
  assert.equal(s.state, "waiting");
  assert.deepEqual([s.service, s.fromVersion, s.toVersion, s.confidence, s.irreversibleCount, s.priorRollbacks], ["shop-api", "v2.4.1", "v2.4.0", "high", 2, 1]);
  assert.equal(s.expiresAt, "2026-11-26T01:10:00Z");
  assert.equal(s.openedAt, "2026-11-26T01:00:00Z");
  assert.equal(s.onReject, REJECT_CONSEQUENCE);
  assert.match(REJECT_CONSEQUENCE, /이관 큐/);
});

test("직전 health 값이 배포 기록에 없으면 null(확인 안 됨), 있으면 그대로", () => {
  assert.equal(rollbackStatusOf(incident({ out: HELD })).toVersionHealthy, null);
  assert.equal(rollbackStatusOf(incident({ out: { ...HELD, to_version_healthy: true } })).toVersionHealthy, true);
  assert.equal(rollbackStatusOf(incident({ out: { ...HELD, to_version_healthy: "yes" } })).toVersionHealthy, null);
});

test("승인 대기 기록이 없어도(재시작 등) 마감은 지어내지 않는다", () => {
  const s = rollbackStatusOf(incident({ out: HELD }));
  assert.equal(s.state, "waiting");
  assert.equal(s.expiresAt, null);
});

test("롤백 단계가 아직 없으면 롤백 제안 없음이고 원인 후보만으로 값을 보이지 않는다", () => {
  for (const status of ["pending", "skipped"] as const) {
    const s = rollbackStatusOf(incident({ rollback: status }));
    assert.equal(s.state, status === "pending" ? "no_proposal" : "skipped");
  }
  const none = rollbackStatusOf(incident({ rollback: "pending" }));
  assert.equal(none.headline, "롤백 제안 없음");
  assert.deepEqual([none.service, none.fromVersion, none.confidence, none.onReject, none.expiresAt], [null, null, null, null, null]);
  assert.equal(rollbackStatusOf(incident({ rollback: "running" })).state, "checking");
});

test("끝난 결과: 실행됨 · 사람이 거절 · 이관 큐 · 실패", () => {
  assert.equal(rollbackStatusOf(incident({ rollback: "completed", out: HELD })).state, "done");
  const rej = rollbackStatusOf(incident({ rollback: "rejected_by_human", out: HELD }));
  assert.equal(rej.state, "rejected");
  assert.equal(rej.onReject, null); // 이미 끝난 일이라 "거절하면"을 보이지 않는다
  assert.equal(rej.service, "shop-api");
  assert.equal(rollbackStatusOf(incident({ rollback: "transfer_queued" })).state, "handed");
  assert.equal(rollbackStatusOf(incident({ rollback: "failed" })).state, "failed");
});

test("값 모양이 틀리면 버린다(음수·문자 숫자·문장)", () => {
  const s = rollbackStatusOf(incident({ out: { service: "", path_params: { irreversible_count: -1, prior_rollbacks: "1" } } }));
  assert.deepEqual([s.irreversibleCount, s.priorRollbacks], [null, null]);
  assert.equal(s.service, "shop-api"); // 단계 결과에 없으면 진단 후보의 서비스
});

test("가장 최근 열린 사건을 보이고 열린 사건 수를 같이 준다", () => {
  const r = rollbackCardResult([incident({ context_id: "a", opened_at: "2026-11-26T01:00:00Z" }), incident({ context_id: "b", opened_at: "2026-11-26T02:00:00Z" })]);
  assert.equal(r.kind, "status");
  if (r.kind === "status") assert.deepEqual([r.status.contextId, r.openCount], ["b", 2]);
});

const HOLD = { step_id: "rollback", skill: "rollback_deployment", label: "롤백", review_id: "rev_1", expires_at: "2026-11-26T01:10:00Z" };

test("결정 영역: 승인 대기(held)이고 review_id·서비스가 있을 때만", () => {
  const d = rollbackDecisionOf(rollbackStatusOf(incident({ out: HELD, pending_holds: [HOLD] })));
  assert.equal(d?.reviewId, "rev_1");
  assert.equal(d?.service, "shop-api");
  assert.equal(d?.confirmText, "운영에서 shop-api v2.4.1→v2.4.0 롤백이 실행돼요. 되돌릴 수 없어요.");
  // review_id 없음 / 승인 대기 아님 / 서비스 없음 -> 읽기 전용
  assert.equal(rollbackDecisionOf(rollbackStatusOf(incident({ out: HELD, pending_holds: [{ ...HOLD, review_id: null }] }))), null);
  assert.equal(rollbackDecisionOf(rollbackStatusOf(incident({ out: HELD }))), null);
  for (const st of ["completed", "rejected_by_human", "transfer_queued", "skipped", "failed", "running"]) {
    assert.equal(rollbackDecisionOf(rollbackStatusOf(incident({ rollback: st, out: HELD, pending_holds: [HOLD] }))), null, st);
  }
  assert.equal(rollbackDecisionOf(rollbackStatusOf(incident({ rollback: null, pending_holds: [HOLD] }))), null);
  // 다른 단계의 보류 review_id는 쓰지 않는다
  assert.equal(rollbackDecisionOf(rollbackStatusOf(incident({ out: HELD, pending_holds: [{ ...HOLD, step_id: "other" }] }))), null);
});

test("승인 문구: 카드에 없는 버전은 지어내지 않고 뺀다", () => {
  const d = rollbackDecisionOf(rollbackStatusOf(incident({ out: { service: "shop-api", from_version: "v2.4.1" }, pending_holds: [HOLD] })));
  assert.equal(d?.confirmText, "운영에서 shop-api 롤백이 실행돼요. 되돌릴 수 없어요.");
});

test("서비스 이름 확인: 앞뒤 공백만 무시, 대소문자 구분, 빈 값 불가", () => {
  assert.equal(serviceNameMatches("shop-api", "shop-api"), true);
  assert.equal(serviceNameMatches("  shop-api 	 ", "shop-api"), true);
  assert.equal(serviceNameMatches("Shop-API", "shop-api"), false);
  assert.equal(serviceNameMatches("shop api", "shop-api"), false);
  assert.equal(serviceNameMatches("shop-ap", "shop-api"), false);
  assert.equal(serviceNameMatches("", "shop-api"), false);
  assert.equal(serviceNameMatches("   ", "   "), false);
  assert.equal(serviceNameMatches("x", null), false);
});

test("카드 쓰기 경로는 ChatReviewDecision(finance.decide) 한 곳뿐이고 새 POST가 없다", async () => {
  const { readFileSync } = await import("node:fs");
  const src = readFileSync(new URL("../components/office/RequestProgressCard.tsx", import.meta.url), "utf8");
  const cell = src.slice(src.indexOf("function RollbackCell"), src.indexOf("function InfraCell"));
  assert.ok(cell.length > 200);
  assert.doesNotMatch(cell, /<button|onClick|finance\.decide|POST|method:|fetch\(/);
  assert.match(cell, /rollbackDecisionOf\(s\)/);
  assert.match(cell, /decision && \(/); // 결정 영역은 decision이 있을 때만
  assert.equal((cell.match(/<ChatReviewDecision/g) ?? []).length, 1);
  const lib = readFileSync(new URL("./rollbackStatus.ts", import.meta.url), "utf8");
  assert.doesNotMatch(lib, /fetch\(|finance\.decide|approve\(/);
  const dec = readFileSync(new URL("../components/office/ChatReviewDecision.tsx", import.meta.url), "utf8");
  assert.equal((dec.match(/finance\.decide\(/g) ?? []).length, 1);
  assert.doesNotMatch(dec, /fetch\(|method:|POST/);
});

test("ChatReviewDecision: 이름 확인은 승인하기·승인에만, 거절은 canDecideNow만 본다. props 없으면 기존 동작", async () => {
  const { readFileSync } = await import("node:fs");
  const dec = readFileSync(new URL("../components/office/ChatReviewDecision.tsx", import.meta.url), "utf8");
  assert.match(dec, /const approveEnabled = enabled && \(confirmName === undefined \|\| serviceNameMatches\(typedName, confirmName\)\)/);
  assert.match(dec, /disabled=\{!approveEnabled\} onClick=\{\(\) => setStep\("confirm"\)\}/);
  assert.match(dec, /className="btn-primary btn-card" disabled=\{!approveEnabled\} onClick=\{\(\) => void decide\("approved"\)\}/);
  assert.match(dec, /disabled=\{!enabled\} onClick=\{\(\) => void decide\("rejected"\)\}/);
  assert.match(dec, /confirmName !== undefined && \(/); // 이름 입력칸은 confirmName이 있을 때만
  for (const f of ["RequestCreative.tsx", "RequestPublish.tsx"]) {
    const u = readFileSync(new URL(`../components/office/${f}`, import.meta.url), "utf8");
    assert.doesNotMatch(u.match(/<ChatReviewDecision[^>]*>/)?.[0] ?? "", /confirmName|confirmText|doneText/, f);
  }
});
