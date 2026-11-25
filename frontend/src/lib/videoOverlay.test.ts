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

test("배속 칩 4가지 (#1760): 배속만·자막 없음 이유 2가지, 자막+배속, 필드 없음은 지금 그대로", () => {
  const fontless = { ...ov("applied", "video"), captions: 0, captions_skipped: "font_not_found", speed_applied: true };
  assert.deepEqual(overlayChip(fontless, 1.2), { text: "1.2배속만 · 자막 없음", tone: "warn", note: "서버에 한글 글꼴이 없어요" });
  const empty = { ...ov("applied", "video"), captions: 0, captions_skipped: "nothing_to_overlay", speed_applied: true };
  assert.deepEqual(overlayChip(empty, 1.5), { text: "1.5배속만 · 자막 없음", tone: "warn", note: "얹을 자막이 없어요" });
  const both = { ...ov("applied", "video"), speed_applied: true, captions_skipped: null };
  assert.deepEqual(overlayChip(both, 1.2), { text: "자막·엔드카드 얹음 · 1.2배속", tone: "ok" });
  // 배속 값은 영상 요약(summary.speed)을 먼저, 없으면 얹기 기록의 speed
  assert.equal(overlayChip({ ...both, speed: 1.333 })?.text, "자막·엔드카드 얹음 · 1.3배속");
  // 새 필드가 없으면(옛 기록·배속 끔) 지금 그대로
  assert.deepEqual(overlayChip(ov("applied", "video")), { text: "자막·엔드카드 얹음", tone: "ok" });
  assert.deepEqual(overlayChip(ov("applied", "video"), 1), { text: "자막·엔드카드 얹음", tone: "ok" });
  // 고르는 규칙은 그대로: 배속만 해도 applied면 overlay_video
  assert.equal(pickVideoPart({ video: done, overlay: fontless }), "overlay_video");
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
