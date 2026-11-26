// 스토리보드 그림 밑 칸별 글 (11/26 C). node --test src/lib/boardPanelNotes.test.ts
import assert from "node:assert/strict";
import { test } from "node:test";
import { noteColumns, panelNotes } from "./boardPanelNotes.ts";
import type { StoryboardShot } from "./storyboard.ts";

const shot = (no: number, more: Partial<StoryboardShot> = {}): StoryboardShot => ({
  no, scene_ko: `장면 ${no}`, shot_size: "medium", shot_size_ko: "허리 위", angle: "eye_level", angle_ko: "눈높이",
  composition_ko: "", has_character: true, prompt_en: "p", ...more,
});

test("칸마다 시간·샷·동작·자막", () => {
  const notes = panelNotes([
    shot(1, { start_s: 0, end_s: 4, caption_ko: "여행 준비 끝!" }),
    shot(2, { start_s: 4, end_s: 8, shot_size_ko: "얼굴", angle: "high", angle_ko: "위에서 내려다봄" }),
    shot(3, { start_s: 13, end_s: 15.5 }),
  ]);
  assert.deepEqual(notes[0], { no: 1, time: "0~4초", shot: "허리 위", action: "장면 1", caption: "여행 준비 끝!" });
  assert.equal(notes[1].shot, "얼굴 · 위에서 내려다봄");
  assert.equal(notes[2].time, "13~15.5초");
});

test("시간이 없는 옛 칸은 시간 빈 칸", () => {
  assert.equal(panelNotes([shot(1)])[0].time, "");
});

test("열 수는 구도판과 같다: 4칸 이하 한 줄, 5~8칸 4열", () => {
  assert.deepEqual([1, 3, 4, 5, 8].map(noteColumns), [1, 3, 4, 4, 4]);
});
