// node --test로 돈다 (npm test). 의뢰 진행 카드 기획서가 늦게·안 뜨는 문제 (11/23): 기획 목록 다시 읽기 판정.
import assert from "node:assert/strict";
import { test } from "node:test";
import { PLAN_LOOKUP_MIN_MS, PLAN_REFETCH_MIN_MS, PLAN_RETRY_MAX, PLAN_RETRY_MS, needPlanRefetch, planFound } from "./requestCards.ts";
import type { StepStatus, WorkRequestRow } from "./workRequests.ts";

const step = (status: StepStatus, result_ref: string | null = null) => ({
  status, attempts: 1, grade: null, note: "", reason_code: null, result_ref, review_id: null,
  handoff_task_id: null, async_task_id: null, started_at: null, finished_at: null,
});
const row = (plan: StepStatus, ref: string | null, trend: string | null = "tr_1"): Pick<WorkRequestRow, "plan" | "steps"> => ({
  plan: [
    { step_id: "trend", dept: "data", skill: "trend_report_create", label: "트렌드 분석 보고서", after: [] },
    { step_id: "plan", dept: "planning", skill: "receive_insight", label: "영상 제작안", after: ["trend"] },
  ],
  steps: { trend: step("completed", trend), plan: step(plan, ref) },
});
const NOW = 1_800_000_000_000;

test("결과 번호(PROP-)가 기억해 둔 목록에 있으면 다시 읽지 않고, 없으면 1분 제한 없이 바로 읽는다", () => {
  const r = row("completed", "PROP-20");
  assert.equal(needPlanRefetch(r, [{ id: "PROP-19" }, { id: "PROP-20" }], null, NOW), false); // 목록에 있음
  assert.equal(needPlanRefetch(r, [{ id: "PROP-19" }], null, NOW), true); // 없음: 바로
  assert.equal(needPlanRefetch(r, null, null, NOW), true); // 아직 못 읽음
  assert.ok(PLAN_REFETCH_MIN_MS < PLAN_LOOKUP_MIN_MS);
});

test("같은 번호로는 10초에 한 번까지만 다시 읽는다", () => {
  const r = row("completed", "PROP-20");
  const list = [{ id: "PROP-19" }];
  assert.equal(needPlanRefetch(r, list, NOW - 3_000, NOW), false); // 3초 전에 읽었다
  assert.equal(needPlanRefetch(r, list, NOW - PLAN_REFETCH_MIN_MS + 1, NOW), false);
  assert.equal(needPlanRefetch(r, list, NOW - PLAN_REFETCH_MIN_MS, NOW), true); // 10초 지남
});

test("기획 단계가 안 끝났거나 결과 번호가 PROP-가 아니면 즉시 다시 읽기 대상이 아니다", () => {
  assert.equal(needPlanRefetch(row("running", null), [], null, NOW), false);
  assert.equal(needPlanRefetch(row("completed", null), [], null, NOW), false); // 번호 없음: 15초 간격 다시 읽기가 맡는다
  assert.equal(needPlanRefetch(row("completed", "spd_123"), [], null, NOW), false);
});

test("제안을 찾았나: 끝났는데 못 찾으면 15초 간격으로 최대 8번 더 읽는다", () => {
  assert.equal(planFound(row("completed", "PROP-20"), [{ id: "PROP-20" }]), true);
  assert.equal(planFound(row("completed", "PROP-20"), [{ id: "PROP-19" }]), false);
  // 번호가 없으면 보고서로 이어진 제안(insightId)으로 찾는다
  assert.equal(planFound(row("completed", null), [{ id: "PROP-7", insightId: "trend_report:tr_1" }]), true);
  assert.equal(planFound(row("completed", null), []), false);
  assert.equal(planFound(row("completed", "PROP-20"), null), false);
  assert.deepEqual([PLAN_RETRY_MS, PLAN_RETRY_MAX], [15_000, 8]);
});
