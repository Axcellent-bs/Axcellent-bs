// node --test로 돈다 (npm test). 병합 미리보기 문구·오류 문구.
import assert from "node:assert/strict";
import { test } from "node:test";
import { NGINX_BLOCKED_CODE, NGINX_BLOCKED_MESSAGE } from "./apiError.ts";
import { mergeHttpErrorKo, mergeSummaryKo, previewDraftIds, skipReasonKo, type MergeAnswer } from "./outageMerge.ts";

const answer = (over: Partial<MergeAnswer> = {}): MergeAnswer => ({
  dry_run: true, merged_count: 3, remaining_count: 2, skipped: [],
  groups: [{ representative: "spd_a", merged_from: ["spd_b", "spd_c"], representative_caption: "글", trigger: "apology_notice" },
    { representative: "spd_d", merged_from: ["spd_e"], representative_caption: "글2", trigger: "compensation" }],
  ...over,
});

test("미리보기 요약은 서버 숫자를 그대로 쓴다", () => {
  assert.equal(mergeSummaryKo(answer()), "2묶음에서 3건을 대표 초안 아래로 접습니다. 병합 뒤 목록에는 2건이 남습니다.");
  assert.equal(mergeSummaryKo(answer({ dry_run: false })), "2묶음, 3건을 병합했습니다. 목록에는 2건이 남았습니다.");
  assert.equal(mergeSummaryKo(answer({ groups: [], merged_count: 0 })), "병합할 비슷한 초안이 없습니다.");
});

test("확인 때는 미리보기에서 본 초안만 보낸다", () => {
  assert.deepEqual(previewDraftIds(answer()), ["spd_a", "spd_b", "spd_c", "spd_d", "spd_e"]);
});

test("오류 문구에는 경로·주소가 없고 nginx 차단은 공용 안내를 쓴다", () => {
  assert.equal(mergeHttpErrorKo(403, NGINX_BLOCKED_CODE), NGINX_BLOCKED_MESSAGE);
  for (const status of [401, 403, 400, 422, 502, 504, 500, undefined]) {
    assert.doesNotMatch(mergeHttpErrorKo(status), /\/api\/|https?:|localhost|\d+\.\d+\.\d+\.\d+/);
  }
  assert.match(mergeHttpErrorKo(502), /마케팅 AI에 연결/);
});

test("건너뜀 이유는 모르는 코드도 코드를 노출하지 않는다", () => {
  assert.match(skipReasonKo("publish_in_progress_or_done"), /게시/);
  assert.equal(skipReasonKo("zzz"), "병합하지 않음");
});
