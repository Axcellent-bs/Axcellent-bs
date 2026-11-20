// node --test로 돈다 (npm test). AI 오피스 자리 불을 고객 의뢰(wr_) 단계로도 켠다 (deriveOffice가 쓰는 lib/officeRequests.ts).
// deriveOffice(lib/office.ts)는 env를 읽는 모듈을 가져와 node로 바로 못 불러서, 자리 규칙(requestDesks·applyRequestDesks)을 여기서 고정한다
import assert from "node:assert/strict";
import { test } from "node:test";
import { applyRequestDesks, latestOpenRequest, progressOf, requestDesks } from "./officeRequests.ts";
import type { StepStatus, WorkRequestRow } from "./workRequests.ts";

type Desk = { state: "idle" | "working" | "waiting"; bubble: { text: string; kind: string } | null; skill: string | null };
const KEYS = ["data", "planning", "marketing", "orchestrator", "safety", "infra"] as const;
type K = (typeof KEYS)[number];
const blank = () => Object.fromEntries(KEYS.map((k) => [k, { state: "idle", bubble: null, skill: null }])) as Record<K, Desk>;
const deskOf = (n: string) => ((KEYS as readonly string[]).includes(n) ? (n as K) : null);

const step = (status: StepStatus, note = "") => ({
  status, attempts: 1, grade: null, note, reason_code: status === "waiting_human" ? "human_check" : null, result_ref: null, review_id: null,
  handoff_task_id: null, async_task_id: null, started_at: null, finished_at: null,
});
const req = (id: string, updated: string, s: Record<string, ReturnType<typeof step>>, status: WorkRequestRow["status"] = "running"): WorkRequestRow => ({
  request_id: id, account: "a", text: "x", created_at: updated, updated_at: updated, status, task: "promo_video", confidence: null, reason: null,
  alternatives: [], classification: null, steps: s, log: [],
  plan: [
    { step_id: "s1", dept: "data", skill: "trend_report_create", label: "트렌드 분석 보고서", after: [] },
    { step_id: "s2", dept: "planning", skill: "receive_insight", label: "영상 제작안", after: ["s1"] },
    { step_id: "s3", dept: "orchestrator", skill: "human_check", label: "사람 확인", after: ["s2"] },
    { step_id: "s4", dept: "marketing", skill: "video_make", label: "영상 제작", after: ["s3"] },
  ],
});

test("waiting_async 데이터 단계 → 데이터 자리 working + 말풍선(단계 이름, note에 %가 있으면 붙임)", () => {
  const r = req("wr_0000000001", "2026-11-20T05:36:20Z", { s1: step("waiting_async", "인스타 계정 10/41 · 45%"), s2: step("pending") });
  assert.deepEqual(requestDesks([r]), [{ desk: "data", state: "working", bubble: "트렌드 분석 보고서 45%", skill: "trend_report_create" }]);
  const d = blank();
  applyRequestDesks(d, requestDesks([r]), deskOf);
  assert.equal(d.data.state, "working");
  assert.deepEqual(d.data.bubble, { text: "트렌드 분석 보고서 45%", kind: "think" });
  assert.equal(d.planning.state, "idle");
  assert.equal(progressOf("보고서 만드는 중"), null);
});

test("단계가 바뀌면(데이터 completed → 기획 running) 불이 다음 부서 자리로 옮겨 간다", () => {
  const d = blank();
  applyRequestDesks(d, requestDesks([req("wr_0000000001", "2026-11-20T05:37:00Z", { s1: step("completed"), s2: step("running") })]), deskOf);
  assert.equal(d.data.state, "idle");
  assert.equal(d.planning.state, "working");
  assert.equal(d.planning.bubble?.text, "영상 제작안");
});

test("waiting_human(사람 확인) → 총괄 자리 waiting, held → 안전 처리 로그 자리 waiting", () => {
  const d = blank();
  applyRequestDesks(d, requestDesks([req("wr_0000000001", "2026-11-20T05:38:00Z", { s1: step("completed"), s2: step("completed"), s3: step("waiting_human") }, "waiting")]), deskOf);
  assert.equal(d.orchestrator.state, "waiting");
  assert.equal(d.orchestrator.bubble, null);
  const h = blank();
  applyRequestDesks(h, requestDesks([req("wr_0000000001", "2026-11-20T05:39:00Z", { s4: step("held") })]), deskOf);
  assert.equal(h.safety.state, "waiting");
});

test("사건 단계가 쓰는 자리는 사건이 먼저", () => {
  const d = blank();
  d.data = { state: "working", bubble: { text: "영향 분석", kind: "think" }, skill: "impact_analysis" }; // 장애 사건 단계
  applyRequestDesks(d, requestDesks([req("wr_0000000001", "2026-11-20T05:36:20Z", { s1: step("running") })]), deskOf);
  assert.deepEqual(d.data, { state: "working", bubble: { text: "영향 분석", kind: "think" }, skill: "impact_analysis" });
});

test("끝난 의뢰는 무시, 여러 개면 가장 최근 열린 의뢰 하나만", () => {
  for (const status of ["done", "failed", "rejected", "needs_human"] as const) {
    assert.deepEqual(requestDesks([req("wr_0000000001", "2026-11-20T05:36:20Z", { s1: step("running") }, status)]), [], status);
  }
  const old = req("wr_0000000001", "2026-11-20T05:00:00Z", { s1: step("running") });
  const fresh = req("wr_0000000002", "2026-11-20T05:40:00Z", { s1: step("completed"), s2: step("running") });
  const closed = req("wr_0000000003", "2026-11-20T06:00:00Z", { s1: step("running") }, "done");
  assert.equal(latestOpenRequest([old, closed, fresh])?.request_id, "wr_0000000002");
  assert.deepEqual(requestDesks([old, closed, fresh]).map((x) => x.desk), ["planning"]);
});

test("토큰이 없으면 의뢰 목록이 없다 → 자리는 지금 그대로", () => {
  for (const none of [null, undefined, []]) {
    const d = blank();
    applyRequestDesks(d, requestDesks(none), deskOf);
    assert.deepEqual(d, blank());
  }
});
