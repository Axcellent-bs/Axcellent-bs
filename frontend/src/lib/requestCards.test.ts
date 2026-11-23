// node --test로 돈다 (npm test). 대화방 의뢰 진행 카드: 번호 찾기(총괄 문장만) · 단계 칩·버튼 · 같은 의뢰 알림 줄 숨김 · 보고서·제안 잇기 · 토큰 없으면 카드 없음.
import assert from "node:assert/strict";
import { test } from "node:test";
import { madeVideo, noticeIdOf, perfStep, placeRequestCards, planCard, planLookupDue, proposalOfRequest, requestActionError, reportIdOfRequest, requestIdOf, resultStep, showRequestCard, stepRows } from "./requestCards.ts";
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

test("버튼 실패 문구: 403 서버 차단 · 409 이미 처리(목록 다시 읽기) · 404 · 그 밖 상태 코드", () => {
  const err = (status: number) => Object.assign(new Error("x"), { status });
  assert.deepEqual(requestActionError(err(403)), { text: "서버가 막았어요 (403) · 관리자에게 이 경로 허용을 요청해 주세요", reload: false });
  assert.deepEqual(requestActionError(err(409)), { text: "이미 다른 사람이 처리했어요", reload: true });
  assert.deepEqual(requestActionError(err(404)), { text: "의뢰나 단계를 찾지 못했어요", reload: false });
  assert.deepEqual(requestActionError(err(502)), { text: "처리하지 못했어요 (502)", reload: false });
  assert.deepEqual(requestActionError(new TypeError("Failed to fetch")), { text: "처리하지 못했어요", reload: false });
  assert.deepEqual(requestActionError(null), { text: "처리하지 못했어요", reload: false });
});

// ---- 의뢰 영상 성과 · 기획 결과 받기 (11/23)

const PUB = "2026-11-16T03:00:00+00:00";
const cp = (checkpoint: string, over: Record<string, unknown> = {}) => ({ checkpoint, status: "unknown" as const, ...over });
const perf = (over: Record<string, unknown> = {}) => ({
  status: "known" as const, video_id: "TGT1", platform: "instagram", published_at: PUB,
  peer_basis: "@ourshop 최근 릴스 12개 조회 중앙값 (이 영상 빼고, 최대 20개)",
  checkpoints: [
    cp("1h", { status: "known", views: 420, likes: 31, peer_ratio: 0.42 }),
    cp("1d", { status: "known", views: 2100, likes: 140, peer_ratio: 1.6 }),
    cp("3d", { reason: "놓침: 그 시점에 재지 못함 (데이터 끊김)", due_at: "2026-11-19T03:00:00+00:00" }),
    cp("7d", { reason: "측정 중 (100.0시간 뒤 잼)", due_at: "2026-11-23T03:00:00+00:00" }),
  ],
  ...over,
});
const NOW = Date.parse("2026-11-18T04:00:00Z");

test("성과 측정: 영상 제작이 끝난 의뢰만 읽고, 등록 안 됐으면(영상 id 없음) 줄을 더하지 않는다", () => {
  assert.equal(madeVideo({ ...row({ s5: step("completed") }), plan: [{ step_id: "s5", dept: "marketing", skill: "generate_media", label: "영상 제작", after: [] }] }), true);
  assert.equal(madeVideo({ ...row({ s5: step("running") }), plan: [{ step_id: "s5", dept: "marketing", skill: "generate_media", label: "영상 제작", after: [] }] }), false);
  assert.equal(perfStep(null, NOW), null);
  assert.equal(perfStep({ status: "unknown", reason: "캠페인 wr_x에 등록된 영상이 없음", checkpoints: [] }, NOW), null);
});

test("성과 측정 칸: 시점 4칸 (조회·좋아요, 우리 릴스 배수 · 놓침 · 잴 날짜), 출처 한 줄", () => {
  const v = perfStep(perf(), NOW)!;
  assert.deepEqual(v.chip.label, "3일째");
  assert.deepEqual(v.cells.map((c) => [c.name, c.main, c.sub]), [
    ["1시간", "조회 420 · 좋아요 31", "우리 릴스 중앙값의 0.4배"],
    ["1일", "조회 2,100 · 좋아요 140", "우리 릴스 중앙값의 1.6배"],
    ["3일", "놓침", null],
    ["7일", "11/23 잼", null],
  ]);
  assert.equal(v.source, "인스타 @ourshop 릴스 · 공식 API · 매출은 프로모션 링크가 없어 측정 불가");
  assert.equal(v.note, null);
  assert.equal(v.done, false);
});

test("성과 측정 칸: 표본 적음 · 7일 끝 · 게시 계정 없음 같은 서버 이유", () => {
  const low = perfStep(perf({ peer_basis: "표본 적음(low_sample): @ourshop 최근 릴스 2개 (3개 미만이라 배수 없음)",
    checkpoints: [cp("1h", { status: "known", views: 420, likes: 31 })] }), NOW)!;
  assert.equal(low.cells[0].sub, "비교 표본 적음");
  const done = perfStep(perf({ checkpoints: [cp("7d", { status: "known", views: 4500, likes: 300, peer_ratio: 1.5 })] }), NOW)!;
  assert.equal(done.chip.label, "측정 끝");
  assert.equal(done.done, true);
  const missed = perfStep(perf({ checkpoints: [cp("7d", { reason: "놓침: 등록 전에 그 시점이 지남" })] }), NOW)!;
  assert.equal(missed.done, true);
  const why = "회사 설정에 게시 계정 아이디가 없음 ([instagram] publish_username)";
  const none = perfStep(perf({ status: "unknown", reason: why, published_at: null, peer_basis: "우리 게시 계정 최근 릴스 조회 중앙값 (아직 재지 않음)",
    checkpoints: ["1h", "1d", "3d", "7d"].map((k) => cp(k, { reason: why })) }), NOW)!;
  assert.equal(none.chip.label, "확인 필요");
  assert.equal(none.note, why);
  assert.equal(none.source, "인스타 릴스 · 공식 API · 매출은 프로모션 링크가 없어 측정 불가");
  assert.deepEqual(none.cells.map((c) => c.main), ["측정 중", "측정 중", "측정 중", "측정 중"]);
});

test("기획 결과 받기: 이 의뢰로 결과를 받은 제안이면 '제안 N · 늘었음', 아니면 7일 뒤 · 보내는 중", () => {
  const measuring = perfStep(perf(), NOW)!;
  const done = perfStep(perf({ checkpoints: [cp("7d", { status: "known", views: 4500 })] }), NOW)!;
  const got = resultStep(RID, { id: "PROP-15", campaign: RID, result: { direction: "늘었음", summary: "의뢰 영상 7일 조회 4,500회 · 우리 릴스 중앙값의 1.50배" } }, done);
  assert.equal(got.chip.label, "결과 받음");
  assert.equal(got.line, "제안 15 · 늘었음 · 의뢰 영상 7일 조회 4,500회 · 우리 릴스 중앙값의 1.50배");
  // 다른 캠페인의 결과는 이 의뢰 것이 아니다
  assert.equal(resultStep(RID, { id: "PROP-15", campaign: "cmp_1", result: { direction: "줄었음" } }, measuring).chip.label, "7일 뒤");
  assert.equal(resultStep(RID, { id: "PROP-15" }, done).chip.label, "보내는 중");
  assert.equal(resultStep(RID, null, measuring).line, "7일을 재면 기획 제안에 결과를 보내요 · 다음 분석 의뢰의 \"지난번 결과\"에 들어가요");
});

test("기획 칸 열기 주소: 의뢰 번호를 주면 ?request= 로 붙는다 (PDF 주소에는 안 붙음)", () => {
  const c = planCard({ id: "PROP-18", title: "제목", idea: "할 일" }, "wr_d8cf3a9691");
  assert.equal(c.openPath, "/planning/proposals/PROP-18?request=wr_d8cf3a9691");
  assert.equal(c.printPath, "/planning/proposals/PROP-18?print=1");
});
