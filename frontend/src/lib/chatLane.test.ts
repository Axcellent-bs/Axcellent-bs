// node --test로 돈다 (npm test). 대화방에서 AI끼리 주고받은 호출 기록(작업 기록)을 기본으로 접는 규칙 (lib/commFilter.ts chatLane·chatLanes)
// 11/20 16:46~16:49 전체 대화방 줄을 설명(공통 로그 서버 기록)대로 다시 만든 픽스처. log_id는 이 테스트에서 붙인 값이다
import assert from "node:assert/strict";
import { test } from "node:test";
import { RETRY_WINDOW_MS, chatLane, chatLanes, commKey, type LaneContext } from "./commFilter.ts";
import { cardContextsIn, stepNoteText } from "./requestCards.ts";
import { QUIET_LOG_KEY, quietLogText, readShowLog, saveShowLog } from "./quietLog.ts";
import type { CommLine } from "./types";

let n = 0;
const line = (at: string, from: string, to: string | null, skill: string | null, outcome: CommLine["outcome"], context_id: string | null, grade: CommLine["grade"] = "B"): CommLine =>
  ({ log_id: `L${++n}`, occurred_at: `2026-11-20T${at}+09:00`, from_agent: from, to_agent: to, skill, outcome, context_id, execution_id: null, grade });

const WR = "ctx_wr_ada8f37811";
const REQS = ["ctx_req_REQ-21", "ctx_req_REQ-30", "ctx_req_REQ-34", "ctx_req_REQ-45", "ctx_req_DEV-1"];
// 16:44: 기획→개발 start_dev를 처음 보내 개발이 바로 끝냄 (대화로 남는 줄)
const BEFORE = REQS.flatMap((c) => [line("16:44:01", "planning", "dev", "start_dev", "in_progress", c), line("16:44:02", "planning", "dev", "start_dev", "completed", c)]);
// 16:46~16:49 (모두 작업 기록이어야 하는 줄)
const WINDOW = [
  line("16:46:10", "orchestrator", "marketing", "generate_media", "in_progress", WR),
  line("16:46:40", "orchestrator", "marketing", "generate_media", "rejected", WR), // 총괄이 30초에 실패 처리
  line("16:47:10", "orchestrator", "marketing", "generate_media", "rejected", WR),
  line("16:47:40", "orchestrator", "marketing", "generate_media", "rejected", WR),
  ...REQS.map((c) => line("16:48:00", "planning", "dev", "start_dev", "completed", c)), // 다시 보냄, 개발은 바로 "끝남"
  line("16:48:30", "dashboard-api", "marketing", "creative_video_request", "rejected", "ctx_ui_dashboard"),
  line("16:49:00", "planning", "memory", "memory.card_write", "completed", "ctx_req_data_result_tr_c3fd"),
  line("16:49:01", "safety", "planning", "memory.card_write", "completed", "ctx_req_data_result_tr_c3fd"),
];
const CARD = new Set([WR]);

test("11/20 16:46~16:49 전체 대화방 줄은 모두 작업 기록: chat 0, 실패 2 (generate_media ×3 · creative_video_request)", () => {
  const lanes = chatLanes([...BEFORE, ...WINDOW], { cardContexts: CARD });
  const win = WINDOW.map((l) => lanes.get(l.log_id));
  assert.equal(win.filter((x) => x === "chat").length, 0);
  assert.equal(win.filter((x) => x === "log").length, WINDOW.length);
  // 실패 말풍선 수: 같은 키의 실패는 한 말풍선(×3)으로 묶인다 → generate_media · creative_video_request
  const failBubbles = new Set(WINDOW.filter((l) => l.outcome === "rejected" && lanes.get(l.log_id) === "log").map(commKey));
  assert.equal(failBubbles.size, 2);
  // 처음 보낸 start_dev(16:44)는 대화
  assert.ok(BEFORE.every((l) => lanes.get(l.log_id) === "chat"));
});

const ctx = (o: Partial<LaneContext> = {}): LaneContext => ({ cardContexts: new Set(), completedBefore: new Set(), retriedOk: false, ...o });

test("사건(ctx_inc_) 흐름은 다시 보낸 줄·화면 호출·회사 기억 쓰기라도 언제나 대화", () => {
  const inc = "ctx_inc_3fc735633641";
  const again = line("10:00:00", "orchestrator", "data", "impact_analysis", "completed", inc);
  assert.equal(chatLane(again, ctx({ completedBefore: new Set([commKey(again)]) })), "chat");
  assert.equal(chatLane(line("10:00:00", "dashboard-api", "support", "notice_publish", "rejected", inc), ctx()), "chat");
  assert.equal(chatLane(line("10:00:00", "orchestrator", "memory", "memory.card_write", "completed", inc), ctx()), "chat");
  assert.equal(chatLane(line("10:00:00", "safety", "support", "refund_request", "completed", inc), ctx()), "chat");
});

test("보류(held)·사람 확인 필요(needs_human)는 어느 규칙에 걸려도 대화", () => {
  for (const outcome of ["held", "needs_human"] as const) {
    assert.equal(chatLane(line("10:00:00", "orchestrator", "marketing", "generate_media", outcome, WR), ctx({ cardContexts: CARD })), "chat", outcome);
    assert.equal(chatLane(line("10:00:00", "safety", "support", "refund_request", outcome, null), ctx()), "chat", outcome);
    assert.equal(chatLane(line("10:00:00", "dashboard-api", "marketing", "creative_video_request", outcome, "ctx_ui_x"), ctx()), "chat", outcome);
  }
});

test("다시 시도한 실패: 같은 키가 5분 안에 뒤에서 completed면 실패도 작업 기록, 5분 넘으면 대화", () => {
  const c = "ctx_close_2026_11";
  const fail = line("09:00:00", "finance", "planning", "receive_close_notice", "rejected", c);
  const okSoon = line("09:04:00", "finance", "planning", "receive_close_notice", "completed", c);
  assert.equal(chatLanes([fail, okSoon]).get(fail.log_id), "log");
  const fail2 = line("09:00:00", "finance", "hr", "receive_close_notice", "rejected", c);
  const okLate = { ...line("09:00:00", "finance", "hr", "receive_close_notice", "completed", c), occurred_at: new Date(Date.parse(fail2.occurred_at) + RETRY_WINDOW_MS + 1000).toISOString() };
  assert.equal(chatLanes([fail2, okLate]).get(fail2.log_id), "chat");
  // 다시 시도하지 않은 실패도 대화
  assert.equal(chatLanes([line("09:00:00", "marketing", "planning", "receive_campaign_update", "rejected", "ctx_x")]).values().next().value, "chat");
});

test("의뢰 카드가 없는 방(마케팅 1:1 등)·토큰 없음이면 ctx_wr_ 줄은 대화로 남긴다", () => {
  const l = line("16:46:40", "orchestrator", "marketing", "generate_media", "rejected", WR);
  assert.equal(chatLane(l, ctx({ cardContexts: new Set() })), "chat");
  assert.equal(chatLane(l, ctx({ cardContexts: CARD })), "log");
  // 카드가 보이는지: 그 방 총괄 말풍선에 번호 + 의뢰 목록에 있음 + 토큰
  const talk = [{ text: "의뢰를 접수했어요. (번호 wr_ada8f37811) '홍보 영상 만들기'로 분류했어요", fromOrchestrator: true }];
  const reqs = [{ request_id: "wr_ada8f37811", context_id: null }];
  assert.deepEqual([...cardContextsIn(talk, reqs, true)], [WR]);
  assert.deepEqual([...cardContextsIn(talk, reqs, false)], []); // 토큰 없음
  assert.deepEqual([...cardContextsIn(talk, [], true)], []); // 의뢰 목록에 없음
  assert.deepEqual([...cardContextsIn([{ ...talk[0], fromOrchestrator: false }], reqs, true)], []); // 사람이 따라 적은 글
});

test("다른 부서 흐름의 처음 보낸 쓰기 줄은 모두 대화", () => {
  const firsts = [
    line("11:00:00", "planning", "marketing", "receive_campaign_brief", "completed", "ctx_req_PROP-15"),
    line("11:00:00", "data", "planning", "receive_campaign_update", "completed", "ctx_req_data_result_tr_c3fd"),
    line("11:00:00", "planning", "dev", "start_dev", "in_progress", "ctx_req_REQ-50"),
    line("11:00:00", "marketing", "support", "outage_notice_video", "completed", "ctx_outage_payment_1120"),
    line("11:00:00", "hr", "orchestrator", "shift_handover", "completed", "ctx_hnd_shift_1120"),
    line("11:00:00", "finance", "planning", "receive_close_notice", "completed", "ctx_close_2026_11"),
    line("11:00:00", "finance", "marketing", "team_budget", "completed", "ctx_team_budget_mkt"),
  ];
  const lanes = chatLanes(firsts);
  for (const l of firsts) assert.equal(lanes.get(l.log_id), "chat", `${l.skill} ${l.context_id}`);
});

test("화면 버튼 줄(dashboard-api→부서)은 작업 기록이어도 그 뒤 부서끼리 이어진 줄(기획→마케팅 brief)은 대화", () => {
  const screen = line("12:00:00", "dashboard-api", "planning", "approve_proposal", "completed", "ctx_ui_planning");
  const brief = line("12:00:02", "planning", "marketing", "receive_campaign_brief", "completed", "ctx_ui_planning");
  const lanes = chatLanes([screen, brief]);
  assert.equal(lanes.get(screen.log_id), "log");
  assert.equal(lanes.get(brief.log_id), "chat");
});

test("안전 처리 로그 방에서는 ⑤(회사 기억 쓰기·안전 처리 통과)를 접지 않는다", () => {
  const mem = line("13:00:00", "planning", "memory", "memory.card_write", "completed", "ctx_req_x");
  const safe = line("13:00:01", "safety", "planning", "memory.card_write", "completed", "ctx_req_x");
  assert.equal(chatLane(mem, ctx()), "log");
  assert.equal(chatLane(safe, ctx()), "log");
  assert.equal(chatLane(mem, ctx({ safetyRoom: true })), "chat");
  assert.equal(chatLane(safe, ctx({ safetyRoom: true })), "chat");
});

test("의뢰 카드 단계 실패 문구: failed: in_progress·timeout 계열은 '응답 대기 시간 초과', 그 밖은 그대로", () => {
  assert.equal(stepNoteText("failed: in_progress"), "응답 대기 시간 초과");
  assert.equal(stepNoteText("failed: in_progress (30s)"), "응답 대기 시간 초과");
  assert.equal(stepNoteText("timeout"), "응답 대기 시간 초과");
  assert.equal(stepNoteText("deadline_exceeded"), "응답 대기 시간 초과");
  assert.equal(stepNoteText("부서 응답 시간 초과"), "응답 대기 시간 초과");
  assert.equal(stepNoteText("failed: rejected"), "failed: rejected");
  assert.equal(stepNoteText("no_dept"), "no_dept");
  assert.equal(stepNoteText(null), "");
});

test("작업 기록 보기 토글: 저장소가 없거나 던져도 동작한다 (읽기 실패 = 접힘)", () => {
  const mem = new Map<string, string>();
  const ok = { getItem: (k: string) => mem.get(k) ?? null, setItem: (k: string, v: string) => { mem.set(k, v); } };
  assert.equal(readShowLog(ok), false);
  assert.equal(saveShowLog(ok, true), true);
  assert.equal(mem.get(QUIET_LOG_KEY), "1");
  assert.equal(readShowLog(ok), true);
  const broken = { getItem: () => { throw new Error("SecurityError"); }, setItem: () => { throw new Error("QuotaExceeded"); } };
  assert.equal(readShowLog(broken), false);
  assert.equal(saveShowLog(broken, true), false);
  assert.equal(readShowLog(null), false);
  assert.equal(saveShowLog(undefined, true), false);
  assert.equal(quietLogText(9, false), "작업 기록 9줄 접힘");
  assert.equal(quietLogText(9, true), "작업 기록 9줄 보는 중");
});

test("사람 글·AI 대답(대화 창구 말)은 대화, 안 읽은 수는 대화 줄만 센다", async () => {
  const { chatMessageCount, timelineLanes } = await import("./quietLog.ts");
  const items = [
    { kind: "msg", id: "direct:1", human: true, tone: "request" }, // 사람 글
    { kind: "msg", id: "direct:2", tone: "ok" }, // AI 대답
    { kind: "msg", id: "log:1", tone: "fail", lane: "log" as const }, // generate_media 실패 ×3
    { kind: "msg", id: "log:2", tone: "ok", lane: "log" as const }, // start_dev 다시 보냄
    { kind: "msg", id: "log:3", tone: "fail", lane: "log" as const }, // creative_video_request 실패
    { kind: "system", id: "sys:1" },
  ];
  const l = timelineLanes(items);
  assert.deepEqual(l.chat.map((x) => x.id), ["direct:1", "direct:2", "sys:1"]);
  assert.equal(l.logCount, 3);
  assert.equal(l.failCount, 2);
  assert.equal(chatMessageCount(items), 2);
});
