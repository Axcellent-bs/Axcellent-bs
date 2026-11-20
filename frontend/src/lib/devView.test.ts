// node --test로 돈다 (npm test). 개발 화면 교차검증·지표·작업 현황 필터의 순수 계산 (lib/devView.ts)
import assert from "node:assert/strict";
import { test } from "node:test";
import type { Activity, CrossReview, PR, State, Task } from "./planningData.ts";
import {
  ADMIN_ACTION_TYPES, activityDir, activityPeers, auditOf, deptLabel, isStoppedTask, paramLines, reviewStage, routeAction, routeWhyOf, tokenFor,
} from "./devView.ts";

const review = (over: Partial<CrossReview & { decideWith?: unknown }> = {}): CrossReview & { decideWith?: never } => ({
  id: "XR-PR #7", t: "11-20 10:00", ctx: "c1", target: "merge_pr", ref: "PR #7", title: "t", lane: "ai", routeWhy: [], summary: "", evidence: [],
  checks: [], verifier: "v", ...over,
} as CrossReview & { decideWith?: never });

// ---- 5. 보류 버튼·토스트
test("보류(humanDecide ok:false)는 서버가 받지 않아 보낼 액션이 없다", () => {
  const r = routeAction({ type: "humanDecide", review: "XR-PR #7", ok: false }, [review()]);
  assert.ok("error" in r);
});
test("확인하고 진행은 서버가 알려 준 decide_with로 보낸다", () => {
  const xr = { ...review(), decideWith: { type: "mergePr", pr: "PR #7" } };
  const r = routeAction({ type: "humanDecide", review: "XR-PR #7", ok: true }, [xr as never]);
  assert.deepEqual(r, { send: { type: "mergePr", pr: "PR #7" }, human: true });
  assert.ok("error" in routeAction({ type: "humanDecide", review: "XR-없음", ok: true }, []));
});
test("자동 범위 승급 승인·보류는 운영자(admin) 토큰으로 보낸다", () => {
  assert.ok(ADMIN_ACTION_TYPES.has("approvePromotion"));
  assert.equal(tokenFor({ type: "approvePromotion" }), "admin");
  assert.equal(tokenFor({ type: "rejectPromotion" }), "admin");
  assert.equal(tokenFor({ type: "mergePr", pr: "PR #1" }), "human");
});

// ---- 6. 교차검증 화면: 없는 판정을 기다리는 것처럼 그리지 않는다
test("근거가 모였으면 '근거 모음 완료 · 사람 판단'", () => {
  const st = reviewStage(review({ evidence: [{ source: "data", label: "최근 24시간 이상", value: "정상", ok: true }] }));
  assert.equal(st.label, "근거 모음 완료 · 사람 판단");
  assert.equal(st.timeout, false);
});
test("근거 없이 규칙에 걸린 건은 자동 검증자가 없어 사람 판단, 화면이 열려 있을 때만 시간이 지나면 사람 확인으로", () => {
  const st = reviewStage(review());
  assert.equal(st.label, "규칙에 걸려 멈춤 · 사람 판단");
  assert.equal(st.timeout, true);
  const human = reviewStage(review({ lane: "human" }));
  assert.equal(human.label, "사람 확인");
  assert.equal(human.timeout, false);
});
test("판정 값(params)은 사람 말로, 0인 개수·모르는 키·근거 줄과 겹치는 키는 숨긴다", () => {
  const lines = paramLines({
    changed_lines: 320, changed_files: 4, changed_paths: ["a/x.ts", "a/y.ts", "b/z.ts", "c/w.ts"], review_mismatch_count: 0,
    linked_incident_count: 1, irreversible_count: 0, cross_check: true, data_verdict: "normal", task: "T-1", mystery_key: 9,
  });
  assert.deepEqual(lines, ["320줄 · 4파일 바뀜", "경로 a/x.ts, a/y.ts, b/z.ts 외 1개", "장애 났던 문단 1곳"]);
  assert.deepEqual(paramLines({ open_questions: 1, warnings: 0, basis_count: 0, irreversible_count: 2 }), ["열린 질문 1개", "근거 0건", "되돌릴 수 없는 영역 2곳"]);
});
test("검토로 온 이유: request_paused도 사람 말로, 모르는 키는 일반 문구 하나로, 관문 보류는 그 사실로", () => {
  assert.deepEqual(routeWhyOf(["pr_changed_lines", "request_paused"], null), ["300줄 초과", "요청이 사람 중단 중"]);
  assert.deepEqual(routeWhyOf(["zzz", "yyy"], null), ["그 밖의 자동 처리 규칙"]);
  assert.deepEqual(routeWhyOf([], "held"), ["안전 관문이 보류"]);
});

// ---- 7. AI 단독 처리: 상태에 이미 있는 실제 기록으로 센다
const act = (point: string, text: string, t = "11-20 09:00", over: Partial<Activity> = {}): Activity =>
  ({ t, ctx: "c1", from: "planning", to: "safety", point, kind: "execute", text, ...over }) as Activity;
const pr = (id: string, over: Partial<PR> = {}): PR =>
  ({ id, task: "T-1", title: `제목 ${id}`, branch: "b", reviews: ["통과"], gate: { result: "통과", lines: 10, files: 1, paths: "shop-web/" }, merged: true, ...over }) as PR;
test("자동 머지·교차검증 근거 뒤 자동 머지·자동 확정·자동 범위 제안·축소를 활동과 이력에서 센다", () => {
  const s = {
    prs: [pr("PR #1", { auto: true }), pr("PR #2", { auto: true, crossEvidence: [{ source: "data", label: "x", value: "정상", ok: true }] })],
    activity: [
      act("execute(merge_pr)", "PR #1 자동 머지 (L1 → merge_pr 규칙 통과, 사후 감사)", "11-20 09:00"),
      act("execute(merge_pr)", "PR #2 자동 머지 (L1 → merge_pr 규칙 통과, 사후 감사)", "11-20 09:05"),
      act("execute(merge_pr)", "사람이 PR #3 머지", "11-20 09:06"),
      act("audit.auto_confirmed", "SPEC-4 자동 확정 (질문 0·경고 0)", "11-20 09:07", { to: "planning" }),
      act("PR 머지", "사람이 PR #5 머지", "11-20 09:08", { from: "human", kind: "사람" }),
    ],
    autoPolicy: {
      rules: [], observing: [], history: [{ pr: "PR #1", req: "REQ-1", t: "11-20 09:00", lines: 10, files: 1, result: "장애 연결" }],
      log: [
        { t: "11-20 09:10", text: "L1 → L2 승급 제안 (연속 3건 정상)", by: "자동" },
        { t: "11-20 09:11", text: "PR #1(자동 머지)가 사건 c1에 연결 → L2 → L1 자동 축소", by: "자동" },
        { t: "11-20 09:12", text: "L1 → L2 승급 승인", by: "사람" },
      ],
    },
  } as unknown as Pick<State, "prs" | "activity" | "autoPolicy">;
  const items = auditOf(s);
  assert.deepEqual(items.map((x) => [x.kind, x.ref]), [
    ["자동 머지", "PR #1"], ["교차검증 승인", "PR #2"], ["자동 확정", "SPEC-4"], ["기준 제안", "자동 범위"], ["기준 해제", "자동 범위"],
  ]);
  assert.equal(items[0].result, "장애 연결");
  assert.equal(items[1].result, "머지됨");
  assert.deepEqual(auditOf({ prs: [], activity: [], autoPolicy: { rules: [], observing: [], history: [], log: [] } }), []);
});

// ---- 9. 에이전트 활동: 자기 부서는 화면(part)에 따라
test("개발 화면: dev가 자기, 개발 서버가 예전 이름 planning으로 남긴 자기 활동도 자기. dev↔planning 넘기기는 기획이 상대", () => {
  const a = (from: string, to: string) => ({ from, to }) as Activity;
  assert.equal(activityDir(a("dev", "safety"), "dev"), "보냄");
  assert.equal(activityDir(a("planning", "safety"), "dev"), "보냄");
  assert.equal(activityDir(a("human", "dev"), "dev"), "받음");
  assert.equal(activityDir(a("dev", "dev"), "dev"), "내부");
  assert.equal(activityDir(a("dev", "planning"), "dev"), "보냄");
  assert.equal(activityDir(a("planning", "dev"), "dev"), "받음");
  assert.equal(activityDir(a("planning", "safety"), "planning"), "보냄");
  assert.equal(activityDir(a("dev", "planning"), "planning"), "받음");
  assert.deepEqual(activityPeers([a("planning", "safety"), a("dev", "planning"), a("human", "dev"), a("dev", "dev")], "dev"), ["safety", "planning", "human"]);
  assert.deepEqual(activityPeers([a("planning", "hr"), a("dev", "planning")], "planning"), ["hr", "dev"]);
});
test("모르는 부서 이름은 '내부'", () => {
  assert.equal(deptLabel("hr"), "인사");
  assert.equal(deptLabel("auto_policy"), "내부");
});

// ---- 작업 현황: 코딩 기록 없이 사람이 맡은 작업이 PR 닫혀 '중단'이면 멈춘 작업
test("task.state '중단'은 코딩 기록이 없어도 멈춘 작업", () => {
  const t = (over: Partial<Task>): Task => ({ id: "T-1", spec: "SPEC-1", para: "R1", title: "x", owner: "사람", state: "진행 중", ...over });
  assert.equal(isStoppedTask(t({ state: "중단" })), true);
  assert.equal(isStoppedTask(t({ discarded: true })), true);
  assert.equal(isStoppedTask(t({ coding: { status: "canceled" } as Task["coding"] })), true);
  assert.equal(isStoppedTask(t({ coding: { status: "returned" } as Task["coding"] })), true);
  assert.equal(isStoppedTask(t({})), false);
});
