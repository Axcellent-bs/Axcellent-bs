// node --test로 돈다 (npm test). 개발 화면 배포·장애 대응의 표시 규칙 (devOps.ts).
import assert from "node:assert/strict";
import { test } from "node:test";
import { deployExecuted, deployedPrCount, deployRunView, deployStatusText, incidentStageText, NO_TOOL_TEXT, needsToolLink, revertText, safeUrl } from "./devOps.ts";
import type { DeployRun, Deployment } from "./planning";

const dep = (over: Partial<Deployment>): Deployment => ({
  id: "DEP-1", pr_numbers: [28], version: null, env: "dev", at: "2026-10-21T05:02:00Z", status: "approved",
  approved_by: null, approval_reasons: [], protected_paths: [], check: null, ...over,
});

test("배포 도구가 실행하지 않은 배포는 '배포됨'이 아니다", () => {
  assert.equal(deployExecuted(dep({ status: "approved", executed: false })), false);
  assert.equal(deployExecuted(dep({ status: "succeeded", executed: true })), true);
  // 예전 응답(executed 없음): status로 본다
  assert.equal(deployExecuted(dep({ status: "succeeded" })), true);
  assert.equal(deployExecuted(dep({ status: "approved" })), false);
  assert.match(deployStatusText(dep({ status: "approved", executed: false }))[0], /실제 배포 안 함/);
  assert.equal(deployStatusText(dep({ status: "succeeded", executed: true }))[0], "배포됨");
});

test("'배포된 PR'은 실제로 나간 배포의 PR만 센다 (머지 수가 아니다)", () => {
  const rows = [
    dep({ id: "DEP-1", pr_numbers: [28], status: "succeeded", executed: true }),
    dep({ id: "DEP-2", pr_numbers: [28, 30], status: "approved", executed: false }),
    dep({ id: "DEP-3", pr_numbers: [31], status: "requested" }),
  ];
  assert.equal(deployedPrCount(rows), 1);
  assert.equal(deployedPrCount([]), 0);
});

test("신호원이 없거나 못 읽으면 도구 연결 링크를 띄운다", () => {
  assert.equal(needsToolLink(null), false);
  const base = { deployment: "DEP-1", at: "", window_minutes: 30, verdict: "watch" as const, evidence: [], candidate_prs: [], rollback: null };
  assert.equal(needsToolLink({ ...base, signals: [] }), false);
  assert.equal(needsToolLink({ ...base, signals: [{ kind: "unavailable", source: "dejafix", ref: null, title: "", at: null, reason: "not_connected" }] }), true);
  assert.equal(needsToolLink({ ...base, signals: [{ kind: "incident", source: "dejafix", ref: "1", title: "", at: null }] }), false);
});

test("사건 카드 상태는 'PR 연결됨'이고 '완료'라고 하지 않는다 (복구 확인과 무관)", () => {
  assert.equal(incidentStageText(true), "PR 연결됨");
  assert.equal(incidentStageText(false), "PR 연결 전");
});

test("되돌리기 PR 결과를 사람이 읽는 말로", () => {
  assert.equal(revertText({ connectionId: "c", sourcePr: 7, revertPr: 9, status: "merged", at: "" }), "레포 PR #7 되돌리기 PR #9 머지됨");
  assert.equal(revertText({ connectionId: "c", sourcePr: 7, revertPr: 9, status: "auto_merge_scheduled", at: "" }), "레포 PR #7 되돌리기 PR #9 자동 머지 예약 (CI 대기)");
  assert.equal(revertText({ connectionId: "c", sourcePr: 7, revertPr: null, status: "held", at: "" }), "레포 PR #7 되돌리기 안전 확인 대기");
});

const run = (over: Partial<DeployRun>): DeployRun => ({
  state: "dispatched", service: "shop-api", version: "v2.4.1", tool: "github_actions", run_id: "901",
  url: "https://github.com/acme/shop/actions/runs/901", run_status: "dispatched", code: null, reason: null,
  review_id: null, at: "2026-10-21T05:02:00Z", checked_at: null, ...over,
});

test("배포 실행 기록: 도구·실행 링크·상태를 서버 값 그대로", () => {
  assert.equal(deployRunView(null), null);
  assert.equal(deployRunView(undefined), null);
  const sent = deployRunView(run({}))!;
  assert.equal(sent.text, "GitHub Actions 실행 901 · 실행 요청됨");
  assert.equal(sent.url, "https://github.com/acme/shop/actions/runs/901");
  assert.equal(sent.canRefresh, true);
  assert.deepEqual(deployRunView(run({ run_status: "succeeded" }))!.text, "GitHub Actions 실행 901 · 성공");
  assert.equal(deployRunView(run({ run_status: "succeeded" }))!.tone, "green");
  assert.match(deployRunView(run({ tool: "jenkins", run_status: "running" }))!.text, /^Jenkins 실행 901 · 실행 중$/);
  // 실행 번호를 모르면 지어내지 않고, 다시 읽기도 못 한다
  const noId = deployRunView(run({ run_id: null, url: null }))!;
  assert.match(noId.text, /실행 번호 모름/);
  assert.equal(noId.canRefresh, false);
  assert.equal(noId.url, null);
});

test("배포 도구가 없으면 도구 연결로 보낸다 · 보류면 안전 확인", () => {
  const none = deployRunView(run({ state: "no_tool", tool: null, run_id: null, url: null, run_status: null }))!;
  assert.equal(none.text, NO_TOOL_TEXT);
  assert.equal(none.needsTool, true);
  assert.equal(none.url, null);
  const held = deployRunView(run({ state: "held", run_id: null, url: null, run_status: null, reason: "보류 → 안전 확인에서 승인하면 이어서 배포해요" }))!;
  assert.equal(held.held, true);
  assert.equal(held.needsTool, false);
  const failed = deployRunView(run({ state: "failed", run_id: null, url: null, run_status: null, reason: "배포 실패: 403" }))!;
  assert.equal(failed.text, "배포 실패: 403");
  assert.equal(failed.canRefresh, false);
  assert.match(deployRunView(run({ state: "unknown", run_id: null, url: null, run_status: null }))!.text, /먼저 확인/);
});

test("도구에서 아직 도는 중·실패한 배포는 '배포된 PR'에 세지 않는다", () => {
  const rows = [
    dep({ id: "DEP-4", pr_numbers: [40], status: "running", executed: true }),
    dep({ id: "DEP-5", pr_numbers: [41], status: "failed", executed: true }),
    dep({ id: "DEP-6", pr_numbers: [42], status: "succeeded", executed: true }),
  ];
  assert.equal(deployedPrCount(rows), 1);
});

test("머리 표시는 실행 기록을 따른다 (승인 이유 없음도 사람이 누를 때까지 대기)", () => {
  assert.match(deployStatusText(dep({ status: "ready" }))[0], /사람이 누름/);
  assert.equal(deployStatusText(dep({ status: "approved", run: run({ state: "unknown", run_id: null, url: null, run_status: null }) }))[0], "결과 모름 · 도구에서 확인");
  assert.equal(deployStatusText(dep({ status: "approved", run: run({ state: "handed", run_id: null, url: null, run_status: null }) }))[0], "사람 할 일함으로 넘어감");
  assert.equal(deployStatusText(dep({ status: "approved", run: run({ state: "not_sent", run_id: null, url: null, run_status: null }) }))[0], "승인됨 · 보내지 않음");
  assert.equal(deployStatusText(dep({ status: "running", executed: true, run: run({}) }))[0], "배포 중 (도구 실행)");
  assert.equal(deployStatusText(dep({ status: "succeeded", executed: true, run: run({ run_status: "succeeded" }) }))[0], "배포됨");
  assert.equal(deployStatusText(dep({ status: "failed", executed: true, run: run({ run_status: "failed" }) }))[0], "실패");
});

test("보내지 않은 승인 배포는 '배포 실행'을, 넘어간 배포는 사람 실행을 보인다", () => {
  const notSent = deployRunView(run({ state: "not_sent", run_id: null, url: null, run_status: null }))!;
  assert.equal(notSent.canSend, true);
  assert.equal(deployRunView(run({}))!.canSend, false);
  assert.match(deployRunView(run({ state: "handed", run_id: null, url: null, run_status: null }))!.text, /사람이 실행/);
  // Jenkins 대기열이면 도구에서 확인 중
  assert.match(deployRunView(run({ tool: "jenkins", run_id: "queue-12", resolving: true }))!.text, /도구에서 확인 중$/);
});

test("실행 주소는 http(s)만 링크로", () => {
  assert.equal(safeUrl("https://github.com/a/b/actions/runs/1"), "https://github.com/a/b/actions/runs/1");
  assert.equal(safeUrl("http://ci.local/job/x/1/"), "http://ci.local/job/x/1/");
  assert.equal(safeUrl("javascript:alert(1)"), null);
  assert.equal(safeUrl(null), null);
  assert.equal(deployRunView(run({ url: "javascript:alert(1)" }))!.url, null);
});
