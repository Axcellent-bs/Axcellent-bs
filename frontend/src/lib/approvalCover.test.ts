// C 승인 카드 썸네일 (11/27). node --test src/lib/approvalCover.test.ts
// 유튜브·인스타 올리기 승인 요청에 마케팅이 넣은 썸네일(params.cover_media_id, "승인자가 볼 썸네일")이
// 승인 카드에 연결되지 않았다. 카드 위에 그 그림을 보인다
import assert from "node:assert/strict";
import { readFileSync } from "node:fs";
import { test } from "node:test";
import { APPROVAL_COVER_KO, approvalCoverId } from "./requestVideoView.ts";

const A = "cov_aaaaaaaaaaaa";
const B = "cov_bbbbbbbbbbbb";

test("승인에 묶인 썸네일이 먼저, 없으면 지금 정해진 썸네일", () => {
  assert.equal(approvalCoverId({ cover_media_id: A }, { params: { cover_media_id: B } }), B);
  assert.equal(approvalCoverId({ cover_media_id: A }, { params: {} }), A);
  assert.equal(approvalCoverId({ cover_media_id: A }, null), A);
  assert.equal(approvalCoverId({ cover_media_id: A }), A);
});

test("형식 밖 값·썸네일 없음이면 null (그림을 넣지 않는다)", () => {
  assert.equal(approvalCoverId({ cover_media_id: A }, { params: { cover_media_id: "../x" } }), A);
  assert.equal(approvalCoverId({ cover_media_id: "bad" }, { params: { cover_media_id: 3 } }), null);
  assert.equal(approvalCoverId({ cover_media_id: null }, null), null);
});

test("유튜브·인스타 승인 카드 위에 썸네일 (결정 경로는 그대로 ChatReviewDecision 하나)", () => {
  const src = readFileSync(new URL("../components/office/RequestPublish.tsx", import.meta.url), "utf8");
  assert.match(src, /<ApprovalCover [^>]*approvalCoverId\(video, video\.youtube\)[^>]*\/>\s*<ChatReviewDecision reviewId=\{video\.youtube\.review_id\}/);
  assert.match(src, /<ApprovalCover [^>]*approvalCoverId\(video, video\.instagram\)[^>]*\/>\s*<ChatReviewDecision reviewId=\{video\.instagram\.review_id\}/);
  assert.match(src, /useAuthedMedia\(mediaId \? requestVideoCoverUrl\(postDraftId, mediaId\) : null\)/);
  assert.match(src, /if \(!src\) return null/); // 못 받으면 카드는 그대로
  assert.equal(APPROVAL_COVER_KO, "승인하면 이 썸네일로 올라가요");
});
