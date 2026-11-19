import assert from "node:assert/strict";
import { test } from "node:test";
import { isRedeployFailed, parseHandoffSteps, pathLabel, rollbackDoneLine, rollbackView, safeHttpUrl } from "./rollbackResult.ts";

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

test("배포 도구 되돌리기 한 줄: 버전·도구·실행 번호·승인 경로", () => {
  const out = { from_version: "v2", to_version: "v1", rolled_back_to: "v1", path: "auto", redeploy: { tool: "github_actions", run_id: "123", url: null }, fault_released: false };
  assert.equal(rollbackDoneLine(out), "되돌림 완료 · 전 v2 → 후 v1 · github_actions로 다시 배포 (실행 123) · 자동 실행");
});

test("결제 설정 되돌리기는 전후 버전과 확인 결과", () => {
  const out = { from_version: "v2", to_version: "v1", path: "human_approved", payment_version: { before: "p2", after: "p1" }, post_check_ok: true };
  assert.equal(rollbackDoneLine(out), "되돌림 완료 · 전 p2 → 후 p1 · 결제 설정 되돌림 · 사람 승인 뒤 실행 · 되돌린 뒤 확인 통과");
});

test("rolled_back_to가 to_version보다 먼저, 모르는 경로는 그대로, 빈 값은 지어내지 않는다", () => {
  assert.equal(rollbackDoneLine({ to_version: "v0", rolled_back_to: "v1", path: "weird" }), "되돌림 완료 · 전 모름 → 후 v1 · weird");
  assert.equal(rollbackDoneLine(null, "?"), "되돌림 완료 · 전 ? → 후 ?");
  assert.equal(rollbackDoneLine({ fault_released: true, post_check_ok: false }), "되돌림 완료 · 전 모름 → 후 모름 · 장애 주입 해제 · 되돌린 뒤 확인 실패");
  assert.equal(pathLabel(3), null);
});
