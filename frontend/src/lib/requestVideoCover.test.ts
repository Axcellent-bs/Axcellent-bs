// node --test로 돈다 (npm test). 의뢰 영상 썸네일 후보 3장 → 사람이 고르기 (lib/requestVideoView.ts):
// 선택 상태(체크·눌러 둔 것) · [이걸로 정하기] · 바꾸기 전 확인 상자 · ?request= 스크롤 표시 · 주소.
import assert from "node:assert/strict";
import { test } from "node:test";
import { COVER_CONFIRM_KO, VIDEO_NOT_FOUND_KO, canCommitCover, coverCells, coverErrorKo, focusDraftId, needsCoverConfirm, type RequestVideo } from "./requestVideoView.ts";

const A = "cov_aaaaaaaaaaaa";
const B = "cov_bbbbbbbbbbbb";
const C = "cov_cccccccccccc";
const covers = [{ media_id: A, at_s: 2 }, { media_id: B, at_s: 65.4 }, { media_id: C, at_s: null }];
const video = (over: Partial<RequestVideo> = {}): Pick<RequestVideo, "covers" | "cover_media_id" | "cover_picked" | "approval_waiting"> => ({
  covers, cover_media_id: A, cover_picked: false, approval_waiting: false, ...over,
});

test("선택 상태: 정해진 썸네일은 체크(chosen), 아무것도 안 눌렀으면 정해진 것이 선택", () => {
  const cells = coverCells(video(), null);
  assert.deepEqual(cells.map((c) => [c.media_id, c.chosen, c.selected, c.at]), [
    [A, true, true, "0:02"], [B, false, false, "1:05"], [C, false, false, ""],
  ]);
  // 다른 것을 누르면 그 칸만 selected, 체크는 정해진 것 그대로
  const picked = coverCells(video(), B);
  assert.deepEqual(picked.map((c) => [c.chosen, c.selected]), [[true, false], [false, true], [false, false]]);
  // 서버가 고른 값(cover_media_id)을 따른다, 후보 밖·형식 밖 값은 무시
  assert.equal(coverCells(video({ cover_media_id: C }), null).find((c) => c.chosen)?.media_id, C);
  assert.equal(coverCells(video({ cover_media_id: "cov_zzzz" }), "../x").find((c) => c.selected)?.media_id, A);
  assert.deepEqual(coverCells({ covers: [{ media_id: "bad", at_s: 1 }], cover_media_id: null }, null), []);
  assert.deepEqual(coverCells({ cover_media_id: null }, null), []);
});

test("[이걸로 정하기]: 다른 후보를 눌렀거나, 아직 사람이 안 골랐을 때만", () => {
  assert.equal(canCommitCover(coverCells(video(), null), false), true); // 기본값(첫 장)을 그대로 확정
  assert.equal(canCommitCover(coverCells(video({ cover_picked: true }), null), true), false); // 이미 고른 그대로
  assert.equal(canCommitCover(coverCells(video({ cover_picked: true }), B), true), true);
  assert.equal(canCommitCover([], false), false);
});

test("확인 상자: 승인 대기·승인된 요청이 있고 다른 후보로 바꿀 때만 (문구 그대로)", () => {
  assert.equal(COVER_CONFIRM_KO, "썸네일을 바꾸면 게시 승인을 다시 받아야 해요.");
  assert.equal(needsCoverConfirm(coverCells(video({ approval_waiting: true }), B), video({ approval_waiting: true })), true);
  assert.equal(needsCoverConfirm(coverCells(video(), B), video()), false); // 승인 요청 전이면 묻지 않는다
  // 같은 것을 확정하면 승인이 풀리지 않으니 묻지 않는다
  assert.equal(needsCoverConfirm(coverCells(video({ approval_waiting: true }), A), video({ approval_waiting: true })), false);
});

test("?request= 스크롤 표시: 그 의뢰의 가장 최근 영상 하나, 없으면 null(한 줄 안내)", () => {
  const items = [
    { post_draft_id: "spd_new", request_id: "wr_aaaaaaaaaa" },
    { post_draft_id: "spd_old", request_id: "wr_aaaaaaaaaa" },
    { post_draft_id: "spd_x", request_id: "wr_bbbbbbbbbb" },
  ];
  assert.equal(focusDraftId(items, "wr_aaaaaaaaaa"), "spd_new");
  assert.equal(focusDraftId(items, "wr_cccccccccc"), null);
  assert.equal(focusDraftId(items, null), null);
  assert.equal(VIDEO_NOT_FOUND_KO, "이 의뢰의 영상을 아직 찾지 못했어요. 영상 제작이 끝났는지 의뢰 진행을 확인해 주세요.");
});

test("후보를 못 만든 사유: ffmpeg가 없으면 이유를 보여 준다, 모르는 코드도 숨기지 않는다", () => {
  assert.match(coverErrorKo("ffmpeg_missing") ?? "", /ffmpeg가 없어/);
  assert.match(coverErrorKo("ffprobe_missing") ?? "", /ffprobe/);
  assert.equal(coverErrorKo("weird_code"), "썸네일 후보를 만들지 못했어요 (weird_code)");
  assert.equal(coverErrorKo(null), null);
});
