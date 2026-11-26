import assert from "node:assert/strict";
import { test } from "node:test";
import type { CreativeRecordSummary } from "./creativeRecords";
import type { RequestVideo } from "./requestVideoView.ts";
import type { RecordFacts } from "./recordWorks.ts";
import {
  PAGE_SIZE, dayLabel, defaultStep, defaultSteps, groupByDay, hasPublishState, linkedRecordId, madePicture, mergeWorks, slotKind, pageCount, pageHref, pageItems, pageOf,
  renderStatuses, videoSlot, workHref, workStatus, workTitle,
} from "./recordWorks.ts";

const record = (id: string, at: string, more: Partial<CreativeRecordSummary> = {}): CreativeRecordSummary => ({
  record_id: id, created_at: at, summary_ko: `요약 ${id}`, shot_count: 0, ...more,
});
const video = (draft: string, at: string, more: Partial<RequestVideo> = {}): RequestVideo => ({
  post_draft_id: draft, request_id: "wr_0123456789", context_id: null, media_id: "med_plain", media_kind: "video",
  duration_s: 15, caption_ko: "첫 줄\n둘째 줄", created_at: at, labeled: true, preview_path: "p", status: "draft_ready", ...more,
});
const A = "CR-aaaaaaaaaaaa";
const B = "CR-bbbbbbbbbbbb";

test("의뢰 영상의 media_id에서 기록 번호를 읽는다", () => {
  assert.equal(linkedRecordId({ media_id: `med_${A}` }), A);
  assert.equal(linkedRecordId({ media_id: "med_123" }), null);
  assert.equal(linkedRecordId({ media_id: null }), null);
});

test("캐릭터 기록으로 만든 의뢰 영상은 기록 줄에 합치고, 나머지는 혼자 한 줄", () => {
  const works = mergeWorks(
    [record(A, "2026-11-20T01:00:00Z"), record(B, "2026-11-24T01:00:00Z")],
    [video("d1", "2026-11-25T01:00:00Z", { media_id: `med_${A}` }), video("d2", "2026-11-22T01:00:00Z")],
  );
  assert.deepEqual(works.map((w) => w.key), [A, B, "d2"]); // 영상이 새로 생긴 작업이 위로
  assert.equal(works[0].videos.length, 1);
  assert.equal(works[0].at, "2026-11-25T01:00:00Z");
});

test("목록에 없는 기록의 영상은 한 줄로 묶고 그 기록 상세로 잇는다", () => {
  const works = mergeWorks([], [
    video("d1", "2026-11-25T01:00:00Z", { media_id: `med_${A}` }),
    video("d2", "2026-11-24T01:00:00Z", { media_id: `med_${A}` }),
    video("d3", "2026-11-23T01:00:00Z"),
  ]);
  assert.deepEqual(works.map((w) => w.videos.length), [2, 1]);
  assert.equal(workHref(works[0]), `/marketing/records?id=${A}`);
  assert.equal(workHref(works[1]), "/marketing/records?request=wr_0123456789");
});

test("제목은 스토리보드 제목 → 시트 요약 → 게시 글 첫 줄", () => {
  const [w] = mergeWorks([record(A, "2026-11-20T01:00:00Z", { storyboard_title_ko: "첫 출근" })], []);
  assert.equal(workTitle(w), "첫 출근");
  assert.equal(workTitle({ record: record(A, "x"), videos: [], key: A }), `요약 ${A}`);
  assert.equal(workTitle({ record: null, videos: [video("d1", "x")], key: "d1" }), "첫 줄");
});

test("날짜별로 묶는다", () => {
  const works = mergeWorks(
    [record(A, "2026-11-25T03:00:00"), record(B, "2026-11-25T01:00:00"), record("CR-cccccccccccc", "2026-11-24T01:00:00")], [],
  );
  assert.deepEqual(groupByDay(works).map((g) => [g.day, g.works.length]), [["11월 25일", 2], ["11월 24일", 1]]);
  assert.equal(dayLabel("모름"), "날짜 모름");
});

test("상태 글은 가장 앞선 단계 하나", () => {
  assert.equal(workStatus({}, []).text, "다음: 대본 쓰기");
  assert.equal(workStatus({ script_scenes: 6 }, []).text, "다음: 스토리보드");
  assert.equal(workStatus({ shot_count: 6 }, []).text, "다음: 영상 만들기");
  assert.equal(workStatus({ render: { video: "awaiting_approval" } }, []).text, "영상 승인 대기");
  assert.equal(workStatus({ render: { video: "running" } }, []).text, "영상 만드는 중");
  assert.equal(workStatus({ render: { video: "rejected" } }, []).text, "영상 다시 필요");
  assert.equal(workStatus({ render: { video: "done" } }, []).text, "영상 완성");
  assert.equal(workStatus({ render: { video: "done", youtube: "awaiting_approval" } }, []).text, "올리기 승인 대기");
  assert.equal(workStatus(null, [video("d", "x", { youtube: { status: "done" } })]).text, "게시함");
  assert.equal(workStatus(null, [video("d", "x", { status: "check_rejected" })]).tone, "bad");
  assert.deepEqual(renderStatuses({ video: { status: "done" }, qc: {} }), { video: "done", qc: "" });
});

test("목록의 영상 칸: 의뢰 영상 썸네일이 먼저, 없으면 기록 썸네일", () => {
  const base = record(A, "2026-11-20T01:00:00Z", { render: { video: "done" }, cover_media_id: "thm_aaaaaaaaaaaa", video_seconds: 12 });
  const [alone] = mergeWorks([base], []);
  assert.deepEqual(videoSlot(alone), { state: "done", cover: { from: "record", recordId: A, mediaId: "thm_aaaaaaaaaaaa" }, seconds: 12 });
  const [linked] = mergeWorks([base], [video("d1", "2026-11-21T01:00:00Z", { media_id: `med_${A}`, cover_media_id: "cov_aaaaaaaaaaaa" })]);
  assert.deepEqual(videoSlot(linked).cover, { from: "request", draftId: "d1", mediaId: "cov_aaaaaaaaaaaa" });
  assert.equal(videoSlot(linked).seconds, 15);
  const [making] = mergeWorks([record(B, "x", { render: { video: "running" } })], []);
  assert.equal(videoSlot(making).state, "making");
  const [none] = mergeWorks([record(B, "x")], []);
  assert.equal(videoSlot(none).state, "none");
});

test("처음 펼칠 단계는 하나", () => {
  assert.equal(defaultStep({}, []), 2);
  assert.equal(defaultStep({ script_scenes: 6 }, []), 2);
  assert.equal(defaultStep({ shot_count: 6 }, []), 3);
  assert.equal(defaultStep({ render: { video: "done" } }, []), 3);
  assert.equal(defaultStep({ render: { video: "done", instagram: "awaiting_approval" } }, []), 4);
  assert.equal(defaultStep(null, [video("d", "x")]), 3);
});

test("목록 칸: 만든 그림이 done이고 받았으면 그림, 실패·없음·받는 중이면 원래 칸 (11/26)", () => {
  const got = { src: "blob:x", failed: false };
  const done = { render: { sheet_image: "done", board_image: "done" } };
  assert.equal(madePicture(done, "sheet_image"), "sheet_image");
  assert.equal(madePicture(done, "board_image"), "board_image");
  assert.equal(slotKind(done, "sheet_image", got), "picture");
  assert.equal(slotKind(done, "board_image", got), "picture");
  // 읽기 실패 → 원래 칸
  assert.equal(slotKind(done, "board_image", { src: null, failed: true }), "original");
  // 받는 중 → 원래 칸
  assert.equal(slotKind(done, "sheet_image", { src: null, failed: false }), "original");
  // 그림이 없거나 done이 아니면 읽지도 않는다
  const none: (RecordFacts | null)[] = [null, {}, { render: {} }, { render: { board_image: "running" } }, { render: { board_image: "failed" } }];
  for (const r of none) {
    assert.equal(madePicture(r, "board_image"), null);
    assert.equal(slotKind(r, "board_image", got), "original");
  }
  // SVG 콘티만 있는 기록(board_image 없음)은 글자 칸
  assert.equal(slotKind({ render: { sheet_image: "done" } }, "board_image", got), "original");
});

test("처음 펼칠 단계들 (11/26): 영상 전이면 1·2를 같이", () => {
  assert.deepEqual(defaultSteps({}, []), [1, 2]);
  assert.deepEqual(defaultSteps({ script_scenes: 6 }, []), [1, 2]);
  // 스토리보드만 있고 영상이 없으면 1·2 (전에는 3만 펼쳐 시트·대본이 접혔다)
  assert.deepEqual(defaultSteps({ shot_count: 6, script_scenes: 6 }, []), [1, 2]);
  assert.deepEqual(defaultSteps({ shot_count: 6, render: { sheet_image: "done", board_image: "done" } }, []), [1, 2]);
  // 영상이 있으면 3
  assert.deepEqual(defaultSteps({ shot_count: 6, render: { video: "done" } }, []), [3]);
  assert.deepEqual(defaultSteps({ render: { final_video: "done" } }, []), [3]);
  assert.deepEqual(defaultSteps(null, [video("d", "x")]), [3]);
  // 올리기 중이면 4
  assert.deepEqual(defaultSteps({ render: { video: "done", instagram: "awaiting_approval" } }, []), [4]);
});

test("올리기 초안이나 상태가 남아 있나", () => {
  assert.equal(hasPublishState({}), false);
  assert.equal(hasPublishState({ youtube: { title: "제목" } }), true);
  assert.equal(hasPublishState({ instagram: { status: "draft" } }), true);
});

test("10건씩 나누고, 10건 이하면 한 쪽", () => {
  assert.equal(PAGE_SIZE, 10);
  assert.deepEqual([0, 1, 10, 11, 24].map(pageCount), [1, 1, 1, 2, 3]);
  const items = Array.from({ length: 24 }, (_, i) => i);
  assert.deepEqual(pageItems(items, 1), items.slice(0, 10));
  assert.deepEqual(pageItems(items, 3), [20, 21, 22, 23]);
});

test("?page=는 있는 쪽만 받고, 1쪽 주소에는 붙이지 않는다", () => {
  assert.equal(pageOf(null, 24), 1);
  assert.equal(pageOf("2", 24), 2);
  assert.equal(pageOf("9", 24), 3);
  for (const bad of ["0", "-1", "1.5", "abc", ""]) assert.equal(pageOf(bad, 24), 1);
  assert.equal(pageHref(1), "/marketing/records");
  assert.equal(pageHref(3), "/marketing/records?page=3");
});
