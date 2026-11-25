// node --test로 돈다 (npm test).
import assert from "node:assert/strict";
import { test } from "node:test";
import { CAPTION_MAX, canReapply, canToggleOriginal, captionDrafts, overlayChip, overlayVisibleFor, pickVideoPart, playPartFor, reapplyBody } from "./videoOverlay.ts";

const done = { status: "done" };
const ov = (status: string, source_part: string, error: string | null = null) => ({ status, source_part, error, captions: 3 });

test("고르는 규칙 4가지", () => {
  assert.equal(pickVideoPart({ video: done, final_video: done, overlay: ov("applied", "final_video") }), "overlay_video");
  assert.equal(pickVideoPart({ video: done, overlay: ov("applied", "video") }), "overlay_video");
  // 바탕이 가장 나중 영상이 아니면(완성 영상이 새로 올라옴) 가장 나중 영상
  assert.equal(pickVideoPart({ video: done, final_video: done, overlay: ov("applied", "video") }), "final_video");
  // 건너뜀·실패·진행 중이면 가장 나중 영상
  assert.equal(pickVideoPart({ video: done, final_video: done, overlay: ov("failed", "final_video") }), "final_video");
  assert.equal(pickVideoPart({ video: done, overlay: ov("running", "video") }), "video");
  assert.equal(pickVideoPart({ video: done, overlay: ov("skipped", "video") }), "video");
});

test("overlay 필드가 없으면 지금 화면 그대로", () => {
  assert.equal(pickVideoPart({ video: done, final_video: done }), "final_video");
  assert.equal(pickVideoPart({ video: done }), "video");
  assert.equal(pickVideoPart({}), "video");
  assert.equal(pickVideoPart(null), "video");
  assert.equal(pickVideoPart({ final_video: done, overlay: null }), "final_video");
  assert.equal(playPartFor("final_video", { final_video: done }), "final_video");
  assert.equal(playPartFor("video", { video: done }), "video");
  assert.equal(overlayVisibleFor("final_video", { final_video: done }), false);
  assert.equal(overlayChip(undefined), null);
  assert.equal(overlayChip(null), null);
  assert.equal(canToggleOriginal({ final_video: done }), false);
  assert.equal(canReapply(undefined, [{ no: 1 }]), false);
});

test("칸별 재생 파일: 가장 나중 영상 칸만 바뀐다, 원본 보기면 그대로", () => {
  const r = { video: done, final_video: done, overlay: ov("applied", "final_video") };
  assert.equal(playPartFor("final_video", r), "overlay_video");
  assert.equal(playPartFor("video", r), "video");
  assert.equal(playPartFor("final_video", r, true), "final_video");
  assert.equal(overlayVisibleFor("final_video", r), true);
  assert.equal(overlayVisibleFor("video", r), false);
  assert.equal(canToggleOriginal(r), true);
});

test("칩 문구와 이유", () => {
  assert.deepEqual(overlayChip(ov("applied", "video")), { text: "자막·엔드카드 얹음", tone: "ok" });
  assert.deepEqual(overlayChip(ov("running", "video")), { text: "자막 얹는 중", tone: "busy" });
  assert.equal(overlayChip(ov("skipped", "video", "font_not_found"))?.text, "서버에 한글 글꼴이 없어 자막 없이 나왔어요");
  assert.equal(overlayChip(ov("failed", "video", "ffmpeg_not_found"))?.text, "서버에 영상 도구가 없어 자막 없이 나왔어요");
  assert.equal(overlayChip(ov("failed", "video", "boom"))?.text, "자막을 얹지 못했어요");
  assert.equal(overlayChip(ov("skipped", "video"))?.tone, "warn");
});

test("다시 얹기 본문과 60자 상한", () => {
  const drafts = captionDrafts([{ no: 1, caption_ko: "안녕" }, { no: 2 }, { no: 3, caption_ko: "가".repeat(80) }]);
  assert.equal(drafts[0].caption_ko, "안녕");
  assert.equal(drafts[1].caption_ko, "");
  assert.equal(drafts[2].caption_ko.length, CAPTION_MAX);
  const body = reapplyBody([{ no: 1, caption_ko: "  고침  " }, { no: 2, caption_ko: "나".repeat(100) }]);
  assert.deepEqual(body.captions[0], { no: 1, caption_ko: "고침" });
  assert.equal(body.captions[1].caption_ko.length, 60);
  assert.deepEqual(Object.keys(body), ["captions"]);
});

test("다시 얹기 버튼 조건", () => {
  assert.equal(canReapply(ov("applied", "video"), [{ no: 1 }]), true);
  assert.equal(canReapply(ov("failed", "video"), [{ no: 1 }]), true);
  assert.equal(canReapply(ov("running", "video"), [{ no: 1 }]), false);
  assert.equal(canReapply(ov("applied", "video"), []), false);
  assert.equal(canReapply(ov("applied", "video"), null), false);
});
