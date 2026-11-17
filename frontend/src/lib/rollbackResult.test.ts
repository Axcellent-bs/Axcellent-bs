import assert from "node:assert/strict";
import { test } from "node:test";
import { isRedeployFailed, parseHandoffSteps, rollbackView, safeHttpUrl } from "./rollbackResult.ts";

const step = (note: string, output: Record<string, unknown>, skill = "rollback_deployment") => ({ skill, note, output });

test("다시 배포했으면 도구·실행 번호·링크를 읽는다", () => {
  const v = rollbackView(step("", { redeploy: { tool: "github_actions", run_id: "123", url: "https://github.com/o/r/actions/runs/123" } }));
  assert.deepEqual(v, { kind: "redeployed", redeploy: { tool: "github_actions", runId: "123", url: "https://github.com/o/r/actions/runs/123" } });
});

test("실행 번호가 숫자여도 글자로, 없으면 null", () => {
  assert.equal(rollbackView(step("", { redeploy: { tool: "jenkins", run_id: 7, url: null } }))?.kind, "redeployed");
  const v = rollbackView(step("", { redeploy: { tool: "jenkins", url: "https://ci/x" } }));
  assert.ok(v && v.kind === "redeployed" && v.redeploy.runId === null);
});

test("http·https가 아닌 주소는 링크로 쓰지 않는다", () => {
  assert.equal(safeHttpUrl("javascript:alert(1)"), null);
  assert.equal(safeHttpUrl("ftp://x/y"), null);
  assert.equal(safeHttpUrl("not a url"), null);
  assert.equal(safeHttpUrl(42), null);
  assert.equal(safeHttpUrl("http://ci.local/job/1"), "http://ci.local/job/1");
  const v = rollbackView(step("", { redeploy: { tool: "x", run_id: "1", url: "javascript:alert(1)" } }));
  assert.ok(v && v.kind === "redeployed" && v.redeploy.url === null);
});

test("다시 배포 실패면 사람이 할 처리 순서를 낸다", () => {
  const v = rollbackView(step("failed: redeploy_failed", { handoff_steps: ["먼저 확인", "다시 실행", 3, ""], retryable: false }));
  assert.deepEqual(v, { kind: "handoff", steps: ["먼저 확인", "다시 실행"] });
  assert.ok(isRedeployFailed("failed: redeploy_failed"));
  assert.ok(!isRedeployFailed("failed: upstream_error"));
});

test("되돌리기가 아니거나 보여 줄 게 없으면 null", () => {
  assert.equal(rollbackView(step("", { redeploy: { tool: "x" } }, "verify_recovery")), null);
  assert.equal(rollbackView(step("", { rolled_back_to: "v1" })), null);
  assert.equal(rollbackView(step("", { redeploy: "oops" })), null);
  assert.equal(rollbackView({ skill: "rollback_deployment", note: "", output: null }), null);
  assert.deepEqual(parseHandoffSteps({ handoff_steps: "한 줄" }), []);
});
