// node --test로 돈다 (npm test). 안전 처리 로그 › 대화방 의뢰 · 사람 확인 줄 뽑기.
import assert from "node:assert/strict";
import { test } from "node:test";
import { CHAT_ROOM_HREF, chatWaits } from "./safetyChatWaits.ts";
import type { StepStatus, WorkRequestRow, WorkRequestStep } from "./workRequests.ts";

const TASKS = [{ name: "promo_video", label: "홍보 영상" }];
const plan = [
  { step_id: "s1", dept: null, skill: "brand_character", label: "회사 캐릭터", after: [] as string[] },
  { step_id: "s4", dept: null, skill: "human_check", label: "사람 확인", after: ["s1"] },
  { step_id: "s6", dept: "marketing", skill: "generate_media", label: "영상 제작", after: ["s4"] },
];
const step = (status: StepStatus, reason_code: string | null = null, started_at: string | null = null): WorkRequestStep => ({
  status, attempts: 0, grade: null, note: "", reason_code, result_ref: null, review_id: null,
  handoff_task_id: null, async_task_id: null, started_at, finished_at: null,
});
const row = (id: string, steps: Record<string, WorkRequestStep>, extra: Partial<WorkRequestRow> = {}) => ({
  request_id: id, status: "waiting" as const, task: "promo_video", plan, steps,
  updated_at: "2026-11-26T01:00:00Z", hidden_at: null, stopped_at: null, ...extra,
});

test("사람 확인을 기다리는 단계마다 한 줄: 이름·이유·대화방 링크", () => {
  const got = chatWaits([row("wr_a", { s1: step("completed"), s4: step("waiting_human", "human_check", "2026-11-26T02:00:00Z") })], TASKS);
  assert.equal(got.length, 1);
  assert.deepEqual(got[0], {
    key: "req:wr_a:s4", requestId: "wr_a", what: "홍보 영상 · 사람 확인",
    why: "사람 확인을 눌러야 다음 단계로 가요", since: "2026-11-26T02:00:00Z", href: CHAT_ROOM_HREF,
  });
});

test("캐릭터 답 대기·승인 필요도 잡고, 모르는 이유는 기본 문장", () => {
  const got = chatWaits([
    row("wr_b", { s1: step("waiting_human", "character_wait") }),
    row("wr_c", { s4: step("waiting_human", "needs_approval") }),
    row("wr_d", { s4: step("waiting_human", "something_new") }),
  ], TASKS);
  assert.deepEqual(got.map((w) => w.why), [
    "회사 캐릭터를 골라야 시작해요", "상태를 바꾸는 일이라 사람 승인을 기다려요", "사람이 정해야 넘어가요",
  ]);
});

test("영상 승인 대기(held)는 C 보류 목록에 있으니 빼고, 끝난·숨긴·멈춘 의뢰도 뺀다", () => {
  const waiting = { s4: step("waiting_human", "human_check") };
  const got = chatWaits([
    row("wr_held", { s6: { ...step("held", "video_approval"), review_id: "rv_1" } }),
    row("wr_done", waiting, { status: "done" }),
    row("wr_hidden", waiting, { hidden_at: "2026-11-26T03:00:00Z" }),
    row("wr_stopped", waiting, { stopped_at: "2026-11-26T03:00:00Z" }),
  ], TASKS);
  assert.deepEqual(got, []);
});

test("오래 기다린 것부터, 일 이름을 모르면 '의뢰'", () => {
  const got = chatWaits([
    row("wr_new", { s4: step("waiting_human", "human_check", "2026-11-26T05:00:00Z") }),
    row("wr_old", { s4: step("waiting_human", "human_check", "2026-11-26T01:30:00Z") }, { task: "unknown" }),
  ], TASKS);
  assert.deepEqual(got.map((w) => w.requestId), ["wr_old", "wr_new"]);
  assert.equal(got[0].what, "의뢰 · 사람 확인");
});
