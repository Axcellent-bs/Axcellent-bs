// node --test로 돈다 (npm test). 미디어 만들기의 비용 줄·결과 문구가 서버 값만 쓰는지.
import assert from "node:assert/strict";
import { test } from "node:test";
import { estimateBlockKo, estimateLines, mediaHttpErrorKo, mediaResultKo, publishHttpErrorKo, publishReasonKo, publishResultKo, publishStatusLine, remainingSeconds, remainingKo, canDecideNow, decideErrorKo, approvalLink, type MediaEstimate, type PublishState } from "./briefMedia.ts";

const estimate = (over: Partial<MediaEstimate> = {}): MediaEstimate => ({
  status: "ok", reason_code: null, media_kind: "video", cost_krw: 835, settled: false, basis: "price_table",
  per_item_limit_krw: 2000, monthly_budget_krw: 30000, month_total_krw: 1670, ...over,
});

test("비용 줄은 서버가 준 숫자를 그대로 쓴다", () => {
  assert.deepEqual(estimateLines(estimate()), [
    "예상 비용 835원 (단가표 기준 추정)",
    "건당 상한 2,000원",
    "이달 누적 1,670원 / 월 상한 30,000원",
  ]);
});

test("서버가 주지 않은 값은 지어내지 않고 읽을 수 없다고 적는다", () => {
  const lines = estimateLines(estimate({ cost_krw: null, basis: null, per_item_limit_krw: null, monthly_budget_krw: null, month_total_krw: null }));
  assert.deepEqual(lines, ["예상 비용을 계산할 수 없습니다.", "건당 상한 설정을 읽을 수 없음", "이달 누적을 읽을 수 없음 / 월 상한 설정을 읽을 수 없음"]);
  assert.ok(!lines.join(" ").match(/\d+원/));
});

test("이미 만든 미디어는 새 비용이 나지 않는다고 적는다", () => {
  assert.match(estimateLines(estimate({ settled: true, basis: "ledger" }))[0], /새 비용이 나지 않습니다/);
});

test("막힌 이유는 사람이 읽는 문구로, 모르는 코드는 코드만 덧붙인다", () => {
  assert.match(estimateBlockKo("carousel_items_undecided"), /캐러셀/);
  assert.match(estimateBlockKo("budget_exceeded"), /이달 미디어 비용 상한/);
  assert.equal(estimateBlockKo("new_code"), "지금은 만들 수 없습니다 (new_code).");
});

test("만들기 결과: 준비됨·검사 걸림·보류·실패", () => {
  const ready = mediaResultKo({ status: "completed", outcome: null, output: { stage: "draft_ready" } });
  assert.equal(ready.kind, "ready");
  assert.equal(mediaResultKo({ status: "completed", outcome: "already_prepared", output: { stage: "draft_ready" } }).kind, "ready");
  assert.equal(mediaResultKo({ status: "completed", outcome: "rejected_by_check", output: { stage: "check_rejected" } }).kind, "check_rejected");
  const held = mediaResultKo({ status: "input-required", outcome: "held", output: { review_id: "rev_1" } });
  assert.equal(held.kind, "held");
  assert.match(held.text, /사람 확인으로 보류/);
  const over = mediaResultKo({ status: "failed", outcome: "budget_exceeded", output: null });
  assert.equal(over.kind, "failed");
  assert.match(over.text, /상한/);
  assert.match(mediaResultKo({ status: "failed", outcome: "in_progress", output: null }).text, /이미 만드는 중/);
});

test("서버 오류 문장은 화면에 그대로 나오지 않는다", () => {
  const raw = "Traceback: GeminiVideoError 503 UNAVAILABLE https://example.invalid/v1";
  const failed = mediaResultKo({ status: "failed", outcome: "provider_failed", output: { reason: raw } });
  assert.ok(!failed.text.includes("Traceback") && !failed.text.includes("https://"));
  for (const status of [401, 403, 404, 422, 502, 504, 500, undefined]) {
    const text = mediaHttpErrorKo(status);
    assert.ok(text.length > 0 && !text.includes("Traceback"));
  }
  assert.match(mediaHttpErrorKo(504), /두 번 만들지 않습니다/);
});

const publish = (over: Partial<PublishState> = {}): PublishState => ({
  status: "not_requested", can_request: true, reason_code: null, review_id: null, failure_code: null, permalink: null, caption: "글", ...over,
});

test("게시 요청 상태: 요청 전에는 줄이 없고, 대기·게시·거절을 구분한다", () => {
  assert.equal(publishStatusLine(publish()), null);
  assert.equal(publishStatusLine(null), null);
  assert.match(publishStatusLine(publish({ status: "held", can_request: false }))!.text, /10분 안에.*만료/);
  assert.match(publishStatusLine(publish({ status: "publishing", can_request: false }))!.text, /승인됐습니다/);
  assert.equal(publishStatusLine(publish({ status: "published", can_request: false }))!.tone, "ok");
  assert.match(publishStatusLine(publish({ status: "rejected", can_request: false }))!.text, /거절됐거나.*만료/);
  assert.match(publishStatusLine(publish({ status: "failed", failure_code: "content_changed" }))!.text, /내용이 바뀌어/);
  assert.match(publishStatusLine(publish({ status: "failed", failure_code: "new_code" }))!.text, /new_code/);
});

test("게시 요청 결과: 요청됨은 아직 게시되지 않았다고 알리고, 실패는 코드별 문구", () => {
  const ok = publishResultKo({ status: "completed", outcome: "requested", output: null });
  assert.equal(ok.kind, "requested");
  assert.match(ok.text, /아직 게시되지 않았습니다/);
  assert.equal(publishResultKo({ status: "completed", outcome: "already_requested", output: null }).kind, "already");
  for (const code of ["daily_limit_reached", "check_not_passed", "media_kind_unsupported", "adapter_unavailable", "publish_halted"]) {
    const r = publishResultKo({ status: "failed", outcome: code, output: null });
    assert.equal(r.kind, "failed");
    assert.ok(!r.text.includes(code), code);
  }
  assert.equal(publishReasonKo("new_code"), "게시를 요청하지 못했습니다. 잠시 뒤 다시 시도해 주세요 (new_code).");
});

test("게시 요청 연결 오류는 서버 문장 없이 상태별 문구", () => {
  assert.match(publishHttpErrorKo(403), /권한/);
  assert.match(publishHttpErrorKo(404), /지원하지 않/);
  assert.match(publishHttpErrorKo(504), /두 번 만들지 않습니다/);
  assert.match(publishHttpErrorKo(undefined), /다시 시도/);
});

test("남은 시간은 서버가 준 마감만 쓰고 1분 단위로 적는다", () => {
  const now = Date.parse("2030-01-01T00:00:00Z");
  assert.equal(remainingSeconds(null, now), null);
  assert.equal(remainingSeconds("not-a-date", now), null);
  assert.equal(remainingSeconds("2030-01-01T00:10:00Z", now), 600);
  assert.equal(remainingKo(null), "남은 시간을 알 수 없습니다");
  assert.equal(remainingKo(600), "약 10분 남음");
  assert.equal(remainingKo(61), "약 2분 남음");
  assert.equal(remainingKo(30), "1분 미만 남음");
  assert.match(remainingKo(0), /지났습니다/);
});

test("승인 버튼은 확인 체크·이름·마감 전일 때만 켜진다", () => {
  const ok = { tokenSet: true, reviewed: true, approver: "병환", remaining: 300, busy: false };
  assert.equal(canDecideNow(ok), true);
  assert.equal(canDecideNow({ ...ok, reviewed: false }), false);
  assert.equal(canDecideNow({ ...ok, approver: null }), false);
  assert.equal(canDecideNow({ ...ok, tokenSet: false }), false);
  assert.equal(canDecideNow({ ...ok, busy: true }), false);
  assert.equal(canDecideNow({ ...ok, remaining: 0 }), false);
  assert.equal(canDecideNow({ ...ok, remaining: null }), true);
});

test("만료와 사람 거절은 다른 문구, 만료만 다시 요청을 안내한다", () => {
  const expired = publishStatusLine(publish({ status: "expired", can_retry: true }))!.text;
  assert.match(expired, /만료/);
  assert.match(expired, /다시 요청/);
  assert.doesNotMatch(publishStatusLine(publish({ status: "expired", can_retry: false }))!.text, /다시 요청/);
  assert.match(publishStatusLine(publish({ status: "rejected", rejected_cause: "human" }))!.text, /사람이 거절/);
  assert.match(publishStatusLine(publish({ status: "rejected", rejected_cause: "unknown" }))!.text, /구분할 수 없습니다/);
});

test("재요청 거부 코드는 코드별 한국어 안내", () => {
  for (const code of ["retry_not_expired", "retry_rejected_by_human", "retry_cause_unknown", "retry_already_published", "retry_other_held"]) {
    assert.ok(!publishReasonKo(code).includes(code), code);
  }
  assert.match(publishResultKo({ status: "failed", outcome: "retry_rejected_by_human", output: null }).text, /사람이 거절/);
});

test("승인 호출 오류는 본인 승인·이미 결정·권한을 가른다", () => {
  assert.match(decideErrorKo(403, "SELF_APPROVAL"), /본인/);
  assert.match(decideErrorKo(409, "ALREADY_DECIDED"), /이미 결정/);
  assert.match(decideErrorKo(403, "HUMAN_ONLY"), /권한/);
  assert.match(decideErrorKo(404, null), /찾을 수 없/);
});

test("승인 화면 링크는 건을 강조할 뿐 승인 정보를 담지 않는다", () => {
  assert.equal(approvalLink("rev_1"), "/marketing/campaign?review=rev_1");
  assert.equal(approvalLink(null), "/marketing/campaign");
  assert.ok(!/approve|decision|token/i.test(approvalLink("rev_1")));
});
