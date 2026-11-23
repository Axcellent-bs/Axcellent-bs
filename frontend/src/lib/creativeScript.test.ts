// node --test로 돈다 (npm test). 영상 대본 칸의 순수 계산 (11/23).
import assert from "node:assert/strict";
import { test } from "node:test";
import { nearestCount, saveBody, scriptProblem, sourceLabel, spanLabel, stageChips, updateScene, type CreativeScript } from "./creativeScript.ts";

const SCRIPT: CreativeScript = {
  title_ko: "들판의 하루",
  total_seconds: 10,
  cta_ko: "지금 만나 보세요",
  source: "llm",
  dropped_ko: [],
  scenes: [
    { no: 1, start_s: 0, end_s: 5, visual_ko: "들판의 소녀", speaker_ko: "", line_ko: "가을이에요", caption_ko: "가을" },
    { no: 2, start_s: 5, end_s: 10, visual_ko: "노을", speaker_ko: "소녀", line_ko: "예쁘다", caption_ko: "" },
  ],
};

test("장면 하나를 고치면 새 대본이 나오고 원래 대본은 그대로다", () => {
  const next = updateScene(SCRIPT, 1, { caption_ko: "노을빛" });
  assert.equal(next.scenes[1].caption_ko, "노을빛");
  assert.equal(SCRIPT.scenes[1].caption_ko, "");
  assert.equal(next.scenes[0], SCRIPT.scenes[0]);
});

test("저장 본문은 앞뒤 빈칸을 지우고 장면 번호를 빼고 보낸다(서버가 다시 매긴다)", () => {
  const body = saveBody({ ...SCRIPT, title_ko: "  제목 ", scenes: [{ ...SCRIPT.scenes[0], line_ko: " 안녕 " }] });
  assert.equal(body.title_ko, "제목");
  assert.deepEqual(body.scenes, [{ start_s: 0, end_s: 5, visual_ko: "들판의 소녀", speaker_ko: "", line_ko: "안녕", caption_ko: "가을" }]);
  assert.equal(body.cta_ko, "지금 만나 보세요");
});

test("저장할 수 없는 대본은 이유를 준다", () => {
  assert.equal(scriptProblem(SCRIPT), null);
  assert.equal(scriptProblem({ ...SCRIPT, title_ko: " " }), "제목을 적어 주세요");
  assert.equal(scriptProblem({ ...SCRIPT, scenes: [] }), "장면이 하나는 있어야 해요");
  assert.equal(scriptProblem(updateScene(SCRIPT, 0, { visual_ko: "" })), "1번 장면의 화면 설명을 적어 주세요");
  assert.equal(scriptProblem(updateScene(SCRIPT, 1, { end_s: 5 })), "2번 장면은 끝 시간이 시작보다 뒤여야 해요");
  assert.equal(scriptProblem(updateScene(SCRIPT, 1, { end_s: Number.NaN })), "2번 장면의 시간을 확인해 주세요");
  assert.equal(scriptProblem(updateScene(SCRIPT, 0, { caption_ko: "가".repeat(61) })), "1번 장면의 글이 너무 길어요");
});

test("시간·출처 표시", () => {
  assert.equal(spanLabel({ start_s: 0, end_s: 2.5 }), "0~2.5초");
  assert.equal(sourceLabel("rule"), "AI 없이 줄거리 문장으로 나눈 대본");
  assert.equal(sourceLabel("human"), "사람이 고친 대본");
});

test("목록 칩: 대본·스토리보드 단계, 둘 다 없으면 시트만", () => {
  assert.deepEqual(stageChips({ script_scenes: 6, shot_count: 6 }), ["대본 6장면", "스토리보드 6칸"]);
  assert.deepEqual(stageChips({ script_scenes: 6, shot_count: 0 }), ["대본 6장면"]);
  assert.deepEqual(stageChips({ shot_count: 9 }), ["스토리보드 9칸"]); // 옛 기록(대본 칸 없음)
  assert.deepEqual(stageChips({ script_scenes: 0, shot_count: 0 }), ["시트만"]);
});

test("다시 쓰기 장면 수는 6·9·12 중 가장 가까운 값", () => {
  assert.equal(nearestCount(5), 6);
  assert.equal(nearestCount(8), 9);
  assert.equal(nearestCount(12), 12);
  assert.equal(nearestCount(1), 6);
});
