// node --test로 돈다 (npm test). 제작 기록 영상 썸네일 후보 3장 → 사람이 고르기 (lib/creativeThumbnailView.ts):
// 선택 상태(체크·눌러 둔 것) · [이걸로 정하기] · 바꾸기 전 확인 · 잠김 · 만들기 버튼 · 목록 칩 · 오류 문구.
import assert from "node:assert/strict";
import { test } from "node:test";
import {
  THUMB_CONFIRM_KO, canCommitThumb, needsThumbConfirm, thumbAction, thumbChip, thumbCells, thumbErrorText, thumbsLocked,
  type ThumbnailsState,
} from "./creativeThumbnailView.ts";

const A = "thm_aaaaaaaaaaaa";
const B = "thm_bbbbbbbbbbbb";
const C = "thm_cccccccccccc";
const done = (over: Partial<ThumbnailsState> = {}): ThumbnailsState => ({
  status: "done",
  candidates: [{ media_id: A, kind: "closeup" }, { media_id: B, kind: "scene" }, { media_id: C, kind: "moment" }],
  chosen: null,
  ...over,
});

test("선택 상태: 고르지 않았으면 첫 장이 정해진 것, 누른 칸만 selected", () => {
  const cells = thumbCells(done(), null);
  assert.deepEqual(cells.map((c) => [c.media_id, c.kind, c.chosen, c.selected]), [
    [A, "캐릭터 클로즈업", true, true], [B, "핵심 장면", false, false], [C, "액션·감정 순간", false, false],
  ]);
  assert.deepEqual(thumbCells(done(), C).map((c) => [c.chosen, c.selected]), [[true, false], [false, false], [false, true]]);
  // 서버가 고른 값을 따른다. 후보 밖·형식 밖 값은 무시(의뢰 영상 후보 번호 cov_도 받지 않는다)
  assert.equal(thumbCells(done({ chosen: B }), null).find((c) => c.chosen)?.media_id, B);
  assert.equal(thumbCells(done({ chosen: "thm_zzzz" }), "../x").find((c) => c.selected)?.media_id, A);
  assert.deepEqual(thumbCells({ candidates: [{ media_id: "cov_aaaaaaaaaaaa" }] }, null), []);
  assert.deepEqual(thumbCells(null, null), []);
});

test("[이걸로 정하기]와 확인 상자: 유튜브 승인 대기 중 다른 후보로 바꿀 때만 묻는다", () => {
  assert.equal(canCommitThumb(thumbCells(done(), null), false), true); // 기본값(첫 장) 확정
  assert.equal(canCommitThumb(thumbCells(done({ chosen: A }), null), true), false); // 이미 고른 그대로
  assert.equal(canCommitThumb(thumbCells(done({ chosen: A }), B), true), true);
  assert.equal(THUMB_CONFIRM_KO, "썸네일을 바꾸면 유튜브 올리기 승인을 다시 받아야 해요.");
  assert.equal(needsThumbConfirm(thumbCells(done(), B), "awaiting_approval"), true);
  assert.equal(needsThumbConfirm(thumbCells(done(), B), "draft"), false);
  assert.equal(needsThumbConfirm(thumbCells(done(), A), "awaiting_approval"), false); // 같은 것이면 승인이 안 풀린다
});

test("잠김·만들기 버튼: 올린 뒤에는 둘 다 막고, 진행 중이거나 영상이 없으면 버튼이 없다", () => {
  assert.equal(thumbsLocked("done"), true);
  assert.equal(thumbsLocked("uploading"), true);
  assert.equal(thumbsLocked("awaiting_approval"), false);
  assert.equal(thumbAction(undefined, true, false), "make");
  assert.equal(thumbAction({ status: "unavailable", error: "not_connected" }, true, false), "make");
  assert.equal(thumbAction(done(), true, false), "remake");
  assert.equal(thumbAction({ status: "awaiting_approval" }, true, false), null);
  assert.equal(thumbAction({ status: "running" }, true, false), null);
  assert.equal(thumbAction(undefined, false, false), null);
  assert.equal(thumbAction(done(), true, true), null);
});

test("목록 칩과 오류 문구", () => {
  assert.equal(thumbChip("done"), "썸네일 3장");
  assert.equal(thumbChip("awaiting_approval"), "썸네일 사람 결정 대기");
  assert.equal(thumbChip(undefined), null);
  assert.match(thumbErrorText("not_connected") ?? "", /Higgsfield 계정 연결도 키도 없어/);
  assert.equal(thumbErrorText("mcp_job_failed"), "mcp_job_failed");
  assert.equal(thumbErrorText(null), null);
});
