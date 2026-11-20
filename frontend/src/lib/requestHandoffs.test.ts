import assert from "node:assert/strict";
import { test } from "node:test";
import { handoffRequests, requestHandoffs } from "./requestHandoffs.ts";
import type { WorkRequestRow, WorkRequestStep } from "./workRequests.ts";

const step = (status: WorkRequestStep["status"], note = ""): WorkRequestStep => ({
  status, attempts: 1, grade: null, note, reason_code: null, result_ref: null, review_id: null,
  handoff_task_id: null, async_task_id: null, started_at: status === "pending" ? null : "2026-11-20T01:00:00Z", finished_at: null,
});

const row = (over: Partial<WorkRequestRow> = {}): WorkRequestRow => ({
  request_id: "REQ-1", account: "a", text: "고객이 쓴 글", created_at: "2026-11-20T00:59:00Z", updated_at: "2026-11-20T01:00:00Z",
  status: "running", task: "t", confidence: null, reason: null, alternatives: [], classification: null,
  plan: [
    { step_id: "s1", dept: "data", skill: "a", label: "데이터 모으기", after: [] },
    { step_id: "s2", dept: null, skill: "b", label: "사람 확인", after: ["s1"] },
    { step_id: "s3", dept: "planning", skill: "c", label: "기획서 쓰기", after: ["s2"] },
  ],
  steps: { s1: step("completed", "부서가 쓴 문장"), s2: step("completed"), s3: step("pending") },
  log: [], ...over,
});

test("첫 부서는 총괄이 보내고, 다음 부서는 사람 확인을 건너뛰어 앞 부서가 보낸 것으로 본다", () => {
  const h = requestHandoffs(row());
  assert.equal(h.length, 2);
  assert.equal(h[0].from, "orchestrator");
  assert.equal(h[0].to, "data");
  assert.equal(h[1].from, "data");
  assert.equal(h[1].to, "planning");
  assert.equal(h[1].planned, true);
});

test("부서 문장·고객 글은 줄에 들어가지 않는다", () => {
  const dump = JSON.stringify(requestHandoffs(row()));
  assert.ok(!dump.includes("부서가 쓴 문장"));
  assert.ok(!dump.includes("고객이 쓴 글"));
});

test("넘김이 없는 의뢰는 빠지고 최근 것이 먼저 온다", () => {
  const empty = row({ request_id: "REQ-0", plan: [], steps: {} });
  const newer = row({ request_id: "REQ-2", created_at: "2026-11-20T02:00:00Z" });
  assert.deepEqual(handoffRequests([empty, row(), newer]).map((x) => x.row.request_id), ["REQ-2", "REQ-1"]);
});
