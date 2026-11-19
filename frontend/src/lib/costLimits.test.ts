// node --test로 돈다 (npm test). 비용 상한 화면의 순수 계산.
import assert from "node:assert/strict";
import { test } from "node:test";
import {
  canApply, checkDraft, lowerPathKo, directionOf, historyLineKo, limitHttpErrorKo, limitResultKo, loweringSummary, parseKrw, policyCeilingText,
  readCostLimits, requestSummaryKo, reviewStatusKo, visibleRequests, krw, type CostLimitsState,
} from "./costLimits.ts";

const raw = (over: Record<string, unknown> = {}) => ({
  month: "2026-11", month_total_krw: 4000,
  limits: { video_per_item_krw: 1500, video_source: "ui", monthly_budget_krw: 50000, monthly_source: "env", image_audio_per_item_krw: 300, updated_by: "재헌", updated_at: "2026-11-19T00:00:00Z" },
  bounds: { min_krw: 1, video_item_max_krw: 10000, monthly_max_krw: 5000000 },
  policy: { available: true, items: [
    { key: "video_cost_single", label: "영상 건당", value: 2000, unit: "krw", op: ">=", scope: "video_item", note: "", max_allowed: 1999, default: 2000, min: 100, absolute_max: 10000, source: "default" },
    { key: "media_cost_daily", label: "일일 합계", value: 10000, unit: "krw", op: ">=", scope: "daily_all_media", note: "이미지·영상·BGM 합산", max_allowed: null },
  ] },
  requests: [], history: [], options: { lower_self_approve: false, updated_by: "총괄", updated_at: null }, viewer: { can_edit: true, can_edit_policy: false }, ...over,
});
const state = (over: Record<string, unknown> = {}): CostLimitsState => readCostLimits(raw(over)) as CostLimitsState;

test("상태 읽기: 핵심 칸이 없으면 null, 모르는 값은 안전하게", () => {
  assert.equal(readCostLimits(null), null);
  assert.equal(readCostLimits({ limits: {} }), null);
  const s = state({ requests: [{ id: "r1", kind: "raise", review_status: "weird", payload: { video_per_item_krw: 1800 } }, { kind: "x" }], viewer: {} });
  assert.equal(s.requests.length, 1);
  assert.equal(s.requests[0].review_status, "unknown");
  assert.equal(s.viewer.can_edit, false);
  assert.equal(s.policy.items[1].scope, "daily_all_media");
  assert.equal(s.policy.items[0].absolute_max, 10000);
  assert.equal(s.options.lower_self_approve, false);
  assert.equal(s.viewer.can_edit_policy, false);
  assert.equal(state({ options: { lower_self_approve: true } }).options.lower_self_approve, true);
});

test("방향: 같으면 same, 미설정에서 값을 넣으면 up", () => {
  assert.equal(directionOf(1500, 1500), "same");
  assert.equal(directionOf(1500, null), "same");
  assert.equal(directionOf(1500, 1000), "down");
  assert.equal(directionOf(1500, 1800), "up");
  assert.equal(directionOf(null, 1000), "up");
});

test("입력 글자: 쉼표 허용, 빈칸은 null, 소수·글자는 NaN", () => {
  assert.equal(parseKrw("1,500"), 1500);
  assert.equal(parseKrw(" "), null);
  assert.ok(Number.isNaN(parseKrw("1.5")));
  assert.ok(Number.isNaN(parseKrw("abc")));
});

test("검사: 정책 상한 초과는 정해진 문구, 0원·절대 상한·월<건당 거부", () => {
  const s = state();
  const over = checkDraft(s, { video_per_item_krw: 2000, monthly_budget_krw: null });
  assert.equal(over.policyCeiling, 2000);
  assert.ok(over.errors.includes("정책 상한은 2,000원이라 그 이상은 정책 변경(관리자 승인)이 먼저 필요합니다."));
  assert.equal(policyCeilingText(2000), over.errors[0]);
  assert.equal(checkDraft(s, { video_per_item_krw: 1999, monthly_budget_krw: null }).errors.length, 0);
  assert.match(checkDraft(s, { video_per_item_krw: 0, monthly_budget_krw: null }).errors[0], /0원으로는/);
  assert.match(checkDraft(s, { video_per_item_krw: null, monthly_budget_krw: 9_000_000 }).errors[0], /절대 상한/);
  assert.match(checkDraft(s, { video_per_item_krw: null, monthly_budget_krw: 1000 }).errors.join(), /보다 작을 수 없습니다/);
  assert.match(checkDraft(s, { video_per_item_krw: Number.NaN, monthly_budget_krw: null }).errors[0], /숫자/);
});

test("검사: 올림·낮춤을 섞으면 막고, 월 예산을 누적보다 낮추면 경고", () => {
  const s = state();
  const mixed = checkDraft(s, { video_per_item_krw: 1000, monthly_budget_krw: 60000 });
  assert.equal(mixed.mixed, true);
  assert.ok(mixed.errors.some((e) => /하나씩/.test(e)));
  const warn = checkDraft(s, { video_per_item_krw: null, monthly_budget_krw: 3000 });
  assert.equal(warn.errors.length, 0);
  assert.match(warn.warnings[0], /이번 달 누적/);
  assert.deepEqual(warn.changes.map((c) => c.direction), ["down"]);
});

test("낮추기 확인창: 바뀌는 값·적용 시점·진행 중 영향 없음·상향 승인 안내", () => {
  const s = state();
  const c = checkDraft(s, { video_per_item_krw: 1000, monthly_budget_krw: null });
  const lines = loweringSummary(c.changes, c.warnings).join("\n");
  assert.match(lines, /1,500원 → 1,000원/);
  assert.match(lines, /진행 중인 생성은 영향이 없습니다/);
  assert.match(lines, /다른 담당자의 승인/);
});

test("응답 문구: 보류·완료·확인 필요·정책 초과·그 밖의 실패", () => {
  assert.deepEqual(limitResultKo({ status: "input-required", outcome: "held", output: { review_id: "rev_1" } }).reviewId, "rev_1");
  assert.equal(limitResultKo({ status: "completed", outcome: "lowered", output: null }).kind, "done");
  assert.equal(limitResultKo({ status: "completed", outcome: "option_saved", output: null }).kind, "done");
  const need = limitResultKo({ status: "failed", outcome: "confirmation_required", output: { warnings: [{ code: "monthly_below_month_total", month_total_krw: 4000, monthly_krw: 3000 }] } });
  assert.equal(need.needsConfirm, true);
  assert.match(need.warnings?.[0] ?? "", /3,000원/);
  const pol = limitResultKo({ status: "failed", outcome: "over_policy_ceiling", output: { field: "video_per_item_krw", policy_krw: 2000, policy_key: "video_cost_single" } });
  assert.equal(pol.policyKey, "video_cost_single");
  assert.match(pol.text, /정책 상한은 2,000원/);
  assert.match(limitResultKo({ status: "failed", outcome: "mixed_direction", output: null }).text, /하나씩/);
  assert.match(limitResultKo({ status: "failed", outcome: "zzz", output: null }).text, /zzz/);
});

test("HTTP 오류: 403은 운영자·관리자 안내, 권한 코드는 별도 안내", () => {
  assert.match(limitHttpErrorKo(403), /운영자·관리자/);
  assert.match(limitHttpErrorKo(403, "LIMIT_EDITOR_REQUIRED"), /권한/);
  assert.match(limitHttpErrorKo(401), /로그인/);
  assert.match(limitHttpErrorKo(500), /닿지 못/);
});

test("요청 줄: 상태 문구, 적용 가능, 거절 만료 숨김, 요약, 상태를 모르면 눌러 볼 수 있다", () => {
  const s = state({ requests: [
    { id: "a", kind: "raise", review_status: "approved", payload: { video_per_item_krw: 1800 } },
    { id: "b", kind: "lower", review_status: "pending", payload: { monthly_budget_krw: 30000 } },
    { id: "c", kind: "raise", review_status: "rejected", payload: { monthly_budget_krw: 70000 } },
    { id: "d", kind: "lower", payload: { video_per_item_krw: 900 } },
    { id: "e", kind: "policy_change", payload: {} },
  ] });
  assert.equal(s.requests.length, 4);
  assert.equal(canApply(s.requests[0]), true);
  assert.equal(canApply(s.requests[1]), false);
  assert.equal(canApply(s.requests[3]), true);
  assert.match(reviewStatusKo(s.requests[0]), /같은 요청을 다시 눌러 적용/);
  assert.equal(reviewStatusKo(s.requests[1]), "승인 대기");
  assert.deepEqual(visibleRequests(s.requests).map((r) => r.id), ["a", "b", "d"]);
  assert.match(requestSummaryKo(s.requests[0]), /상향 요청.*1,800원/);
  assert.match(requestSummaryKo(s.requests[1]), /하향 요청.*월 예산 30,000원/);
});

test("낮추기 길 안내: 셀프 승인 켬이면 바로 적용, 끔이면 다른 담당자 승인", () => {
  assert.match(lowerPathKo(true), /바로 적용/);
  assert.match(lowerPathKo(false), /다른 담당자의 승인/);
});

test("이력 줄과 원 표기", () => {
  assert.equal(krw(null), "미설정");
  assert.equal(krw(12000), "12,000원");
  assert.match(historyLineKo({ at: "x", by: "재헌", action: "lower", field: "video_per_item_krw", old: 1500, new: 1000, reason: "절감" }), /영상 건당 상한 낮춤: 1500 → 1000 \(절감\)/);
  assert.match(historyLineKo({ at: "x", by: "총괄", action: "option", field: "lower_self_approve", old: 0, new: 1, reason: "r" }), /하향 셀프 승인 켬/);
});
