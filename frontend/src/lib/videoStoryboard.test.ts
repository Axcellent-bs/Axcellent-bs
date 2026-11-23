// node --test로 돈다 (npm test). 영상 제작안 스토리보드 계산 (lib/videoStoryboard.ts)
import assert from "node:assert/strict";
import { test } from "node:test";
import { requestOfSearch, sceneRole, sceneShares, storyBasis } from "./videoStoryboard.ts";

test("장면 역할: 4장면은 훅·전환·증거·마무리, 3장면은 훅·증거·마무리, 1장면은 훅", () => {
  assert.deepEqual([0, 1, 2, 3].map((i) => sceneRole(i, 4)), ["훅", "전환", "증거", "마무리"]);
  assert.deepEqual([0, 1, 2].map((i) => sceneRole(i, 3)), ["훅", "증거", "마무리"]);
  assert.deepEqual([0, 1, 2, 3, 4].map((i) => sceneRole(i, 5)), ["훅", "전환", "증거", "증거", "마무리"]);
  assert.equal(sceneRole(0, 1), "훅");
});

test("시간표 막대 폭: 장면 길이 비율이고 합은 100", () => {
  const w = sceneShares([{ startS: 0, endS: 3 }, { startS: 3, endS: 7 }, { startS: 7, endS: 12 }, { startS: 12, endS: 15 }]);
  assert.deepEqual(w, [20, 26.7, 33.3, 20]);
  const odd = sceneShares([{ startS: 0, endS: 1 }, { startS: 1, endS: 2 }, { startS: 2, endS: 3 }]);
  assert.equal(Math.round(odd.reduce((a, b) => a + b, 0) * 10) / 10, 100);
  assert.deepEqual(sceneShares([]), []);
});

test("근거 줄: 보고서에서 온 제안은 TR 번호 + 근거 앞 2개, 아니면 번호 없이", () => {
  const facts = [{ label: "소설 검색", value: "+361%" }, { label: "#북토크", value: "" }, { label: "세 번째", value: "1" }];
  assert.deepEqual(storyBasis({ insightId: "trend_report:tr_3f0d9a", facts }), { report: "TR-3F0D", points: ["소설 검색 +361%", "#북토크"] });
  assert.deepEqual(storyBasis({ insightId: "ins-1", facts: [] }), { report: null, points: [] });
});

test("?request= 는 의뢰 번호 형식만 받는다", () => {
  assert.equal(requestOfSearch("?request=wr_d8cf3a9691"), "wr_d8cf3a9691");
  assert.equal(requestOfSearch("?request=../x"), null);
  assert.equal(requestOfSearch(""), null);
});
