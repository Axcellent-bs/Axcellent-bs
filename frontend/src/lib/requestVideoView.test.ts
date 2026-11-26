// node --test로 돈다 (npm test). 의뢰로 만든 영상: 영상 열기 링크(완료일 때만) · 필터 · 칸 표시(라벨 없으면 재생 안 함) · 정렬.
import assert from "node:assert/strict";
import { test } from "node:test";
import { PUBLISH_CHANNELS, canUploadToYoutube, durationKo, isRequestId, publishStatusKo, requestFilterOf, requestVideoLink, sortForRequest, statusKo, videoPane, type RequestVideo } from "./requestVideoView.ts";
import type { StepStatus, WorkRequestStep } from "./workRequests.ts";

const RID = "wr_732efb95e9";
const plan = [
  { step_id: "s4", dept: null, skill: "human_check", label: "사람 확인", after: [] as string[] },
  { step_id: "s5", dept: "marketing", skill: "prepare_video_draft_from_plan", label: "영상 초안 준비", after: ["s4"] },
  { step_id: "s6", dept: "marketing", skill: "generate_media", label: "영상 제작", after: ["s5"] },
];
const req = (status: StepStatus | null, task: string | null = "promo_video", id = RID) => ({
  request_id: id, task, plan,
  steps: (status ? { s6: { status } as WorkRequestStep } : {}) as Record<string, WorkRequestStep>,
});

test("영상 열기 링크: 영상 제작 단계가 완료일 때만 제작 기록의 의뢰 영상 탭(?view=videos&request=)", () => {
  assert.equal(requestVideoLink(req("completed")), `/marketing/records?view=videos&request=${RID}`);
  for (const s of ["running", "waiting_async", "failed", "held", "pending"] as StepStatus[]) assert.equal(requestVideoLink(req(s)), null);
  assert.equal(requestVideoLink(req(null)), null);
  assert.equal(requestVideoLink(req("completed", "trend_report")), null);
  assert.equal(requestVideoLink(req("completed", null)), `/marketing/records?view=videos&request=${RID}`);
  assert.equal(requestVideoLink(req("completed", "promo_video", "wr_bad")), null);
});

test("의뢰 번호 형식과 ?request= 필터", () => {
  assert.equal(isRequestId(RID), true);
  assert.equal(isRequestId("wr_732EFB95E9"), false);
  assert.equal(requestFilterOf(RID), RID);
  assert.equal(requestFilterOf("wr_x&limit=1"), null);
  assert.equal(requestFilterOf(null), null);
});

const vid = (over: Partial<RequestVideo> = {}): RequestVideo => ({
  post_draft_id: "pd_1", request_id: RID, context_id: null, media_id: "m1", media_kind: "video", duration_s: 8,
  caption_ko: "캡션", created_at: "2026-11-20T01:00:00Z", labeled: true, preview_path: "/x", status: "draft", ...over,
});

test("칸 표시: 라벨 입힌 영상만 재생, 그 밖에는 안내", () => {
  assert.equal(videoPane(vid()).kind, "play");
  assert.equal(videoPane(vid({ labeled: false, preview_path: null })).kind, "waiting");
  assert.equal(videoPane(vid({ labeled: false, preview_path: "/raw" })).kind, "waiting"); // 라벨 없으면 경로가 있어도 재생 안 함
  assert.equal(videoPane(vid({ labeled: true, preview_path: null })).kind, "waiting");
  assert.equal(videoPane(vid({ status: "label_missing" })).kind, "waiting"); // label_missing이면 값이 어긋나도 재생 안 함
  assert.equal(videoPane(vid({ status: "label_missing", labeled: false, preview_path: null })).kind, "waiting");
  assert.equal(videoPane(vid({ status: "check_rejected" })).kind, "play"); // 라벨은 있으니 재생, 상태만 표시
  assert.equal(videoPane(vid({ status: "draft_ready" })).kind, "play");
});

test("길이·상태 문구, 의뢰 필터 정렬", () => {
  assert.equal(durationKo(7.6), "8초");
  assert.equal(durationKo(null), "길이 미정");
  assert.equal(durationKo(0), "길이 미정");
  assert.equal(statusKo("draft"), "초안");
  assert.equal(statusKo("draft_ready"), "초안 준비됨");
  assert.equal(statusKo("check_rejected"), "점검에서 거절됨");
  assert.equal(statusKo("label_missing"), "AI 표시 아직 없음");
  assert.equal(statusKo("zzz"), "zzz");
  assert.equal(statusKo(""), "상태 미정");
  const a = vid({ post_draft_id: "a", request_id: "wr_0000000001" });
  const b = vid({ post_draft_id: "b" });
  assert.deepEqual(sortForRequest([a, b], RID).map((v) => v.post_draft_id), ["b", "a"]);
  assert.deepEqual(sortForRequest([a, b], null).map((v) => v.post_draft_id), ["a", "b"]);
});

test("유튜브 칸: AI 표시를 입힌(재생되는) 영상만", () => {
  assert.equal(canUploadToYoutube(vid()), true);
  assert.equal(canUploadToYoutube(vid({ status: "check_rejected" })), true);
  assert.equal(canUploadToYoutube(vid({ labeled: false, preview_path: null })), false);
  assert.equal(canUploadToYoutube(vid({ status: "label_missing" })), false);
});

test("대화방 올리기 버튼: 채널마다 지금 상태 한 마디, 안 했으면 null", () => {
  assert.deepEqual(PUBLISH_CHANNELS.map((c) => c.key), ["youtube", "instagram"]);
  assert.equal(publishStatusKo({}, "youtube"), null);
  assert.equal(publishStatusKo({ youtube: null, instagram: null }, "instagram"), null);
  assert.equal(publishStatusKo({ youtube: { status: "done" } as never }, "youtube"), "올림");
  assert.equal(publishStatusKo({ instagram: { status: "awaiting_approval" } as never }, "instagram"), "사람 승인 대기");
  assert.equal(publishStatusKo({ instagram: { status: "posting" } as never }, "instagram"), "올리는 중");
  assert.equal(publishStatusKo({ youtube: { status: "weird" } as never }, "youtube"), "weird");
});
