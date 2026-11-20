// node --test로 돈다 (npm test). 개발 화면 배포·장애 대응의 표시 규칙 (devOps.ts).
import assert from "node:assert/strict";
import { test } from "node:test";
import { deployExecuted, deployedPrCount, deployStatusText, incidentStageText, needsToolLink, revertText } from "./devOps.ts";
import type { Deployment } from "./planning";

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
