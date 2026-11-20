// node --test로 돈다 (npm test). 대화방 의뢰 진행 카드: 번호 찾기(총괄 문장만) · 단계 칩·버튼 · 같은 의뢰 알림 줄 숨김 · 보고서·제안 잇기 · 토큰 없으면 카드 없음.
import assert from "node:assert/strict";
import { test } from "node:test";
import { noticeIdOf, placeRequestCards, planCard, planLookupDue, proposalOfRequest, reportIdOfRequest, requestIdOf, showRequestCard, stepRows } from "./requestCards.ts";
import { progressKeepPolling } from "./chatCards.ts";
import type { StepStatus, WorkRequestRow } from "./workRequests.ts";

const RID = "wr_732efb95e9";

test("requestIdOf: 총괄 코드 문장 '(번호 wr_…)' · '[의뢰 wr_…]'만", () => {
  assert.equal(requestIdOf(`의뢰를 접수했어요. (번호 ${RID}) '홍보 영상 만들기'로 분류했어요`), RID);
  assert.equal(requestIdOf(`[의뢰 ${RID}] 데이터분석이 분석을 진행 중이에요`), RID);
  assert.equal(requestIdOf(`${RID} 어떻게 됐어요?`), null); // 형식 밖 (고객이 번호만 적은 글)
  assert.equal(requestIdOf("(번호 wr_732efb95e) 짧음"), null);
  assert.equal(requestIdOf("(번호 wr_732EFB95E9) 대문자"), null);
  assert.equal(requestIdOf("[의뢰 wr_732efb95e9x]"), null);
  assert.equal(requestIdOf(null), null);
  assert.equal(noticeIdOf(`[의뢰 ${RID}] 진행 중`), RID);
  assert.equal(noticeIdOf(`의뢰를 접수했어요 (번호 ${RID})`), null);
});

test("카드는 번호가 처음 나온 총괄 줄 아래 하나, 같은 의뢰의 다음 알림 줄은 그 방에서 숨김, 사람 말에서는 찾지 않음", () => {
  const { cardAt, hidden } = placeRequestCards([
    { id: "u1", room: "all", text: `[의뢰 ${RID}] 사람이 따라 적은 글`, fromOrchestrator: false },
    { id: "o1", room: "all", text: `의뢰를 접수했어요. (번호 ${RID})`, fromOrchestrator: true },
    { id: "o2", room: "all", text: `[의뢰 ${RID}] 데이터분석이 분석을 진행 중이에요`, fromOrchestrator: true },
    { id: "o3", room: "all", text: "다른 답", fromOrchestrator: true },
    { id: "o4", room: "all", text: `[의뢰 ${RID}] 기획이 받았어요`, fromOrchestrator: true },
    { id: "x1", room: "ctx_inc_1", text: `[의뢰 ${RID}] 다른 방에서 처음`, fromOrchestrator: true },
    { id: "o5", room: "all", text: "[의뢰 wr_0000000001] 다른 의뢰", fromOrchestrator: true },
  ]);
  assert.deepEqual([...cardAt], [["o1", RID], ["x1", RID], ["o5", "wr_0000000001"]]);
  assert.deepEqual([...hidden].sort(), ["o2", "o4"]);
});

const step = (status: StepStatus, over: Record<string, unknown> = {}) => ({
  status, attempts: 1, grade: null, note: "", reason_code: null as string | null, result_ref: null as string | null, review_id: null,
  handoff_task_id: null, async_task_id: null, started_at: null, finished_at: null, ...over,
});
const row = (steps: Record<string, ReturnType<typeof step>>): Pick<WorkRequestRow, "plan" | "steps"> => ({
  steps,
  plan: [
    { step_id: "s1", dept: "data", skill: "trend_report_create", label: "트렌드 분석 보고서", after: [] },
    { step_id: "s2", dept: "data", skill: "trend_report_insight", label: "기획에 넘길 인사이트", after: ["s1"] },
    { step_id: "s3", dept: "planning", skill: "receive_insight", label: "영상 제작안", after: ["s2"] },
    { step_id: "s4", dept: null, skill: "human_check", label: "사람 확인", after: ["s3"] },
    { step_id: "s5", dept: "marketing", skill: "video_make", label: "영상 제작", after: ["s4"] },
  ],
});

test("단계 → 칩·점·버튼: 사람 확인은 '<다음 단계> 진행' · '여기서 멈추기', 실패·건너뜀은 회색 + note", () => {
  const rows = stepRows(row({
    s1: step("completed", { result_ref: "tr_86f1aa" }), s2: step("completed"), s3: step("completed", { result_ref: "PROP-12" }),
    s4: step("waiting_human", { reason_code: "human_check" }),
  }));
  assert.deepEqual(rows.map((r) => [r.dot, r.chip.label]), [["ok", "완료"], ["ok", "완료"], ["ok", "완료"], ["warn", "사람 확인 대기"], ["gray", "대기"]]);
  assert.deepEqual(rows[3].decide, { approve: "영상 제작 진행", stop: "여기서 멈추기" });
  assert.equal(rows[0].decide, null);
  // 부서 대기(재시작 확인 등)는 사람이 정하는 단계가 아니다
  assert.equal(stepRows(row({ s4: step("waiting_human", { reason_code: "restart_check" }) }))[3].decide, null);
  const failed = stepRows(row({ s1: step("failed", { note: "인스타 한도에 닿아 멈춤" }), s2: step("skipped") }));
  assert.equal(failed[0].muted, "인스타 한도에 닿아 멈춤");
  assert.equal(failed[1].muted, "건너뜀");
  assert.equal(failed[0].dot, "gray");
  assert.equal(stepRows(row({ s1: step("waiting_async") }))[0].dot, "run");
});

test("보고서 id(tr_)와 기획 제안 번호: result_ref가 먼저, 없으면 insightId trend_report:<보고서>", () => {
  const done = row({ s1: step("completed", { result_ref: "tr_86f1aa" }), s3: step("completed", { result_ref: "PROP-12" }) });
  assert.equal(reportIdOfRequest(done), "tr_86f1aa");
  assert.equal(proposalOfRequest(done, []), "PROP-12");
  const noRef = row({ s1: step("completed", { result_ref: "tr_86f1aa" }), s3: step("completed") });
  assert.equal(proposalOfRequest(noRef, [{ id: "PROP-9", insightId: "trend_report:tr_other" }, { id: "PROP-13", insightId: "trend_report:tr_86f1aa" }]), "PROP-13");
  assert.equal(proposalOfRequest(row({ s1: step("completed", { result_ref: "tr_86f1aa" }), s3: step("running") }), [{ id: "PROP-13", insightId: "trend_report:tr_86f1aa" }]), null);
  assert.equal(reportIdOfRequest(row({ s1: step("running", { result_ref: "rep_1" }) })), null); // tr_가 아니면 보고서 칸을 붙이지 않는다
});

test("보고서 진행 폴링은 끝나면 멈추고, 토큰이 없으면 카드가 없다", () => {
  assert.equal(progressKeepPolling("running"), true);
  assert.equal(progressKeepPolling("partial"), false);
  assert.equal(showRequestCard(false, RID), false);
  assert.equal(showRequestCard(true, null), false);
  assert.equal(showRequestCard(true, RID), true);
});

test("기획 단계 칸: 영상 제작안이 있으면 '영상 제작안 · 15초 · 9:16 · 장면 4' + 콘셉트, 기획서가 있으면 칩 '기획서 23'·기획서로 열기", () => {
  const scenes = [0, 2, 6, 11].map((s, i, a) => ({ n: i + 1, startS: s, endS: a[i + 1] ?? 15, visual: "v", caption: "", prompt: "p" }));
  const c = planCard({ id: "PROP-15", spec: "SPEC-23", title: "트렌드 분석: 책", idea: "할 일", videoPlan: { concept: "첫 문장에 빠지는 순간", durationS: 15, aspect: "9:16", scenes } });
  assert.deepEqual(c, {
    chip: "기획서 23", head: "영상 제작안 · 15초 · 9:16 · 장면 4", line: "첫 문장에 빠지는 순간",
    openPath: "/planning/work/specs/SPEC-23", printPath: "/planning/work/specs/SPEC-23?print=1",
  });
});

test("기획 단계 칸: 영상 제작안·기획서가 없으면 '기획 제안' + 할 일 한 줄, 칩 '제안 15'·제안 화면으로", () => {
  assert.deepEqual(planCard({ id: "PROP-15", title: "트렌드 분석: 책", idea: "10대 소설 숏폼 3편" }), {
    chip: "제안 15", head: "기획 제안", line: "10대 소설 숏폼 3편",
    openPath: "/planning/proposals/PROP-15", printPath: "/planning/proposals/PROP-15?print=1",
  });
  assert.equal(planCard({ id: "PROP-15", title: "제목만", idea: "" }).line, "제목만");
});

test("기획 기록 다시 읽기는 1분에 한 번까지", () => {
  assert.equal(planLookupDue(null, 1_000), true);
  assert.equal(planLookupDue(1_000, 1_000 + 59_999), false);
  assert.equal(planLookupDue(1_000, 1_000 + 60_000), true);
});
