// node --test로 돈다 (npm test). 사용량 예산 화면의 순수 계산(원화 없음).
import assert from "node:assert/strict";
import { test } from "node:test";
import {
  amountKo, canApply, checkDraft, directionOf, emptyDraft, historyLineKo, keyOwnerKo, limitHttpErrorKo, limitResultKo, lowerPathKo, loweringSummary,
  parseAmount, policyValueKo, readCostLimits, remainingKo, requestSummaryKo, reviewStatusKo, visibleRequests, type CostLimitsState,
} from "./costLimits.ts";

const raw = (over: Record<string, unknown> = {}) => ({
  month: "2026-11",
  usage: {
    video: { unit: "seconds", used: 100, limit: 600, remaining_pct: 83 },
    image: { unit: "images", used: 8, limit: 200, remaining_pct: 96 },
    audio: { unit: "tracks", used: 0, limit: null, remaining_pct: null },
  },
  limits: {
    video_seconds_monthly: 600, image_count_monthly: 200, audio_count_monthly: null, video_source: "ui", image_source: "env", audio_source: "none",
    key_owner: "server", quota_applies: true, updated_by: "재헌", updated_at: "2026-11-19T00:00:00Z",
  },
  bounds: { min: 1, max: { video_seconds_monthly: 360000, image_count_monthly: 100000, audio_count_monthly: 20000 } },
  policy: { available: true, items: [
    { key: "video_duration", label: "영상 길이", value: 90, unit: "s", op: ">", scope: "video_duration", note: "", max_allowed: 89, default: 90, min: 1, absolute_max: 90, source: "default" },
  ] },
  requests: [], history: [], options: { lower_self_approve: false, updated_by: "총괄", updated_at: null }, viewer: { can_edit: true, can_edit_policy: false }, ...over,
});
const state = (over: Record<string, unknown> = {}): CostLimitsState => readCostLimits(raw(over)) as CostLimitsState;
const draft = (over: Partial<ReturnType<typeof emptyDraft>>) => ({ ...emptyDraft(), ...over });

test("상태 읽기: 핵심 칸이 없으면 null, 모르는 값은 안전하게", () => {
  assert.equal(readCostLimits(null), null);
  assert.equal(readCostLimits({ limits: {} }), null);
  // 예전 원화 응답(범위 칸이 다름)은 읽지 않는다
  assert.equal(readCostLimits({ limits: { video_per_item_krw: 1500 }, bounds: { min_krw: 1, video_item_max_krw: 10000, monthly_max_krw: 5000000 } }), null);
  const s = state({ requests: [{ id: "r1", kind: "raise", review_status: "weird", payload: { video_seconds_monthly: 800 } }, { kind: "x" }], viewer: {} });
  assert.equal(s.requests.length, 1);
  assert.equal(s.requests[0].review_status, "unknown");
  assert.equal(s.viewer.can_edit, false);
  assert.equal(s.policy.items[0].scope, "video_duration");
  assert.equal(s.policy.items[0].absolute_max, 90);
  assert.equal(s.options.lower_self_approve, false);
  assert.equal(s.viewer.can_edit_policy, false);
  assert.equal(state({ options: { lower_self_approve: true } }).options.lower_self_approve, true);
  assert.deepEqual(s.usage.video, { unit: "seconds", used: 100, limit: 600, remaining_pct: 83 });
  assert.equal(s.limits.audio_count_monthly, null);
  assert.equal(s.limits.key_owner, "server");
});

test("종류마다 단위가 달라 합치지 않고 남은 비율도 따로 보여 준다", () => {
  const s = state();
  assert.equal(amountKo(s.usage.video?.used, "seconds"), "100초");
  assert.equal(amountKo(s.usage.image?.used, "images"), "8장");
  assert.equal(amountKo(0, "tracks"), "0곡");
  assert.equal(amountKo(null, "images"), "미설정");
  assert.equal(remainingKo(s.usage.video), "남은 사용량 83%");
  assert.equal(remainingKo(s.usage.audio), "남은 비율을 알 수 없음"); // 예산이 없으면 지어내지 않는다
  assert.equal(remainingKo(undefined), "남은 비율을 알 수 없음");
});

test("고객사 키면 예산이 걸리지 않는다고 알린다", () => {
  assert.match(keyOwnerKo(state().limits), /우리 키.*예산이 걸립니다/);
  const customer = state({ limits: { ...raw().limits, key_owner: "customer", quota_applies: false } });
  assert.equal(customer.limits.quota_applies, false);
  assert.match(keyOwnerKo(customer.limits), /고객사 키로 만드는 중이라 예산이 걸리지 않습니다/);
});

test("방향: 같으면 same, 미설정에서 값을 넣으면 up", () => {
  assert.equal(directionOf(600, 600), "same");
  assert.equal(directionOf(600, null), "same");
  assert.equal(directionOf(600, 300), "down");
  assert.equal(directionOf(600, 900), "up");
  assert.equal(directionOf(null, 100), "up");
});

test("입력 글자: 쉼표 허용, 빈칸은 null, 소수·글자는 NaN", () => {
  assert.equal(parseAmount("1,500"), 1500);
  assert.equal(parseAmount(" "), null);
  assert.ok(Number.isNaN(parseAmount("1.5")));
  assert.ok(Number.isNaN(parseAmount("abc")));
});

test("검사: 0·절대 상한·숫자 아님 거부, 정상 값은 통과", () => {
  const s = state();
  assert.equal(checkDraft(s, draft({ video_seconds_monthly: 700 })).errors.length, 0);
  assert.match(checkDraft(s, draft({ video_seconds_monthly: 0 })).errors[0], /0으로는 막을 수 없습니다/);
  assert.match(checkDraft(s, draft({ image_count_monthly: 100001 })).errors[0], /코드 절대 상한 100,000장/);
  assert.match(checkDraft(s, draft({ audio_count_monthly: 20001 })).errors[0], /코드 절대 상한 20,000곡/);
  assert.match(checkDraft(s, draft({ video_seconds_monthly: Number.NaN })).errors[0], /숫자/);
});

test("검사: 올림·낮춤을 섞으면 막고, 예산을 이번 달 사용량보다 낮추면 경고", () => {
  const s = state();
  const mixed = checkDraft(s, draft({ video_seconds_monthly: 300, image_count_monthly: 300 }));
  assert.equal(mixed.mixed, true);
  assert.ok(mixed.errors.some((e) => /하나씩/.test(e)));
  const warn = checkDraft(s, draft({ video_seconds_monthly: 50 }));
  assert.equal(warn.errors.length, 0);
  assert.match(warn.warnings[0], /영상 월 예산\(초\) 50초이 이번 달 사용량 100초보다 낮아/);
  assert.deepEqual(warn.changes.map((c) => c.direction), ["down"]);
  assert.equal(checkDraft(s, draft({ video_seconds_monthly: 300 })).warnings.length, 0);
});

test("낮추기 확인창: 바뀌는 값·적용 시점·진행 중 영향 없음·상향 승인 안내", () => {
  const s = state();
  const c = checkDraft(s, draft({ video_seconds_monthly: 300 }));
  const lines = loweringSummary(c.changes, c.warnings).join("\n");
  assert.match(lines, /영상 월 예산\(초\): 600초 → 300초/);
  assert.match(lines, /진행 중인 생성은 영향이 없습니다/);
  assert.match(lines, /다른 담당자의 승인/);
});

test("응답 문구: 보류·완료·확인 필요·원화 칸·그 밖의 실패", () => {
  assert.deepEqual(limitResultKo({ status: "input-required", outcome: "held", output: { review_id: "rev_1" } }).reviewId, "rev_1");
  assert.equal(limitResultKo({ status: "completed", outcome: "lowered", output: null }).kind, "done");
  assert.equal(limitResultKo({ status: "completed", outcome: "option_saved", output: null }).kind, "done");
  const need = limitResultKo({
    status: "failed", outcome: "confirmation_required",
    output: { warnings: [{ code: "quota_below_month_usage", media_kind: "video", unit: "seconds", month_used: 400, limit: 300 }] },
  });
  assert.equal(need.needsConfirm, true);
  assert.match(need.warnings?.[0] ?? "", /영상 예산 300초이 이번 달 사용량 400초보다 낮아/);
  const top = limitResultKo({ status: "failed", outcome: "over_absolute_max", output: { field: "image_count_monthly", max: 100000 } });
  assert.match(top.text, /이미지 월 예산\(장\)이 코드 절대 상한 100,000장을 넘습니다/);
  assert.match(limitResultKo({ status: "failed", outcome: "deprecated_krw_limit", output: null }).text, /원화 상한은 없어졌습니다/);
  assert.match(limitResultKo({ status: "failed", outcome: "mixed_direction", output: null }).text, /하나씩/);
  assert.match(limitResultKo({ status: "failed", outcome: "zzz", output: null }).text, /zzz/);
  // 안전 정책 상한(원화) 초과 코드는 더 없다
  assert.match(limitResultKo({ status: "failed", outcome: "over_policy_ceiling", output: null }).text, /over_policy_ceiling/);
});

test("HTTP 오류: 403은 운영자·관리자 안내, 권한 코드는 별도 안내", () => {
  assert.match(limitHttpErrorKo(403), /운영자·관리자/);
  assert.match(limitHttpErrorKo(403, "LIMIT_EDITOR_REQUIRED"), /권한/);
  assert.match(limitHttpErrorKo(401), /로그인/);
  assert.match(limitHttpErrorKo(500), /닿지 못/);
});

test("요청 줄: 상태 문구, 적용 가능, 거절 만료 숨김, 요약, 상태를 모르면 눌러 볼 수 있다", () => {
  const s = state({ requests: [
    { id: "a", kind: "raise", review_status: "approved", payload: { video_seconds_monthly: 900 } },
    { id: "b", kind: "lower", review_status: "pending", payload: { image_count_monthly: 100 } },
    { id: "c", kind: "raise", review_status: "rejected", payload: { audio_count_monthly: 50 } },
    { id: "d", kind: "lower", payload: { video_seconds_monthly: 300 } },
    { id: "e", kind: "policy_change", payload: {} },
  ] });
  assert.equal(s.requests.length, 4);
  assert.equal(canApply(s.requests[0]), true);
  assert.equal(canApply(s.requests[1]), false);
  assert.equal(canApply(s.requests[3]), true);
  assert.match(reviewStatusKo(s.requests[0]), /같은 요청을 다시 눌러 적용/);
  assert.equal(reviewStatusKo(s.requests[1]), "승인 대기");
  assert.deepEqual(visibleRequests(s.requests).map((r) => r.id), ["a", "b", "d"]);
  assert.match(requestSummaryKo(s.requests[0]), /상향 요청.*영상 월 예산\(초\) 900초/);
  assert.match(requestSummaryKo(s.requests[1]), /하향 요청.*이미지 월 예산\(장\) 100장/);
  assert.equal(s.requests[0].payload.video_seconds_monthly, 900);
});

test("낮추기 길 안내: 셀프 승인 켬이면 바로 적용, 끔이면 다른 담당자 승인", () => {
  assert.match(lowerPathKo(true), /바로 적용/);
  assert.match(lowerPathKo(false), /다른 담당자의 승인/);
});

test("이력 줄과 정책 값 표기", () => {
  assert.match(historyLineKo({ at: "x", by: "재헌", action: "lower", field: "video_seconds_monthly", old: 600, new: 300, reason: "절감" }), /영상 월 예산\(초\) 낮춤: 600 → 300 \(절감\)/);
  assert.match(historyLineKo({ at: "x", by: "총괄", action: "option", field: "lower_self_approve", old: 0, new: 1, reason: "r" }), /하향 셀프 승인 켬/);
  assert.equal(policyValueKo(state().policy.items[0]), "90초");
});
