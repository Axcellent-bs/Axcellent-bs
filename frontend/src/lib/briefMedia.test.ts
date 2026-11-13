// node --test로 돈다 (npm test). 미디어 만들기의 비용 줄·결과 문구가 서버 값만 쓰는지.
import assert from "node:assert/strict";
import { test } from "node:test";
import { estimateBlockKo, estimateLines, mediaHttpErrorKo, mediaResultKo, type MediaEstimate } from "./briefMedia.ts";

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
