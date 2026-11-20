// node --test로 돈다 (npm test). 멈춘 이유마다 누가 어떻게 이어 가나.
import assert from "node:assert/strict";
import { test } from "node:test";
import { recoveryOf } from "./codingRecovery.ts";
import type { CodingRun } from "./planningData";

const run = (error: string, extra: Partial<CodingRun> = {}): CodingRun =>
  ({ status: "failed", model: null, files: [], summary: null, review: null, error, attempts: 1, ...extra }) as CodingRun;

test("파일을 못 고르면 사람이 파일을 고르거나 AI 대화로", () => {
  const r = recoveryOf(run("고칠 파일을 고르지 못함"));
  assert.equal(r?.who, "human");
  assert.deepEqual(r?.actions, ["pick", "chat", "return"]);
});

test("크기 한도는 작업을 나누게", () => {
  assert.deepEqual(recoveryOf(run("바뀐 줄 1600줄 > 1500줄 → 사람 확인 (PR 없음)"))?.actions, ["chat", "return", "pick"]);
  assert.deepEqual(recoveryOf(run("고칠 파일 7개 > 5개 → 사람 확인", { status: "waiting_human" }))?.actions, ["pick", "chat", "return"]);
});

test("AI 응답 오류는 에이전트가 한 번 다시, 다 쓰면 사람", () => {
  assert.equal(recoveryOf(run("LLM 리뷰 시간 초과", { transient: true, autoRetries: 0 }))?.who, "agent");
  assert.equal(recoveryOf(run("LLM 리뷰 시간 초과", { transient: true, autoRetries: 1 }))?.who, "human");
});

test("환경 오류에는 파일 고르기·대화를 보이지 않는다", () => {
  for (const e of ["서버 AI 키가 설정되지 않음", "형상관리 PR 열기 실패 (unauthorized) → 토큰 만료·권한 확인", "읽기 전용 연결이라 커밋하지 않음 → 사람 반영 필요"]) {
    const a = recoveryOf(run(e))?.actions ?? [];
    assert.ok(a.includes("connect") && !a.includes("pick") && !a.includes("chat"), e);
  }
});

test("PR이 있는 멈춤은 머지·추가 지시", () => {
  const w = { status: "waiting_human" } as const;
  assert.deepEqual(recoveryOf(run("위험도 높음 (서버 코드 backend/x.py) → 사람 확인 후 머지", w))?.actions, ["merge", "instruct"]);
  assert.equal(recoveryOf(run("PR #3 CI가 20분 넘게 안 끝남 → 사람 확인", w))?.who, "agent");
  assert.deepEqual(recoveryOf(run("PR #3 CI 실패 (build) → 사람 확인", w))?.actions, ["instruct", "merge"]);
});

test("진행 중·사람이 멈춘 것·PR 열기 보류는 따로", () => {
  assert.equal(recoveryOf(run("x", { status: "writing" })), null);
  assert.equal(recoveryOf(run("사람이 중단", { status: "canceled" })), null);
  assert.equal(recoveryOf(run("안전 점검에서 PR 열기 보류", { status: "waiting_human" })), null);
});

test("열린 PR이 있는 사람 확인은 다시 시도 대신 머지·추가 지시", () => {
  const w = { status: "waiting_human", pr: "PR #31" } as const;
  for (const e of ["LLM 쓰기 실패 (LLMResponseError)", "PR #31 형상관리 PR 쓰기 실패 (forbidden)", "코딩 에이전트 오류 (KeyError)"]) {
    const r = recoveryOf(run(e, w));
    assert.equal(r?.who, "human", e);
    assert.ok(r?.actions.includes("merge") && r?.actions.includes("instruct") && !r?.actions.includes("retry"), e);
  }
  // 에이전트가 다시 한다고 하지 않는다 (PR이 있으면 다시 시도는 서버가 거절)
  assert.equal(recoveryOf(run("LLM 리뷰 시간 초과", { ...w, transient: true, autoRetries: 0 }))?.who, "human");
});

test("열린 PR이어도 권한 문제면 도구 연결 안내를 남긴다", () => {
  for (const e of ["PR #31 형상관리 PR 쓰기 실패 (forbidden)", "형상관리 읽기 실패 (unauthorized) → 토큰 만료·권한 확인"]) {
    const a = recoveryOf(run(e, { status: "waiting_human", pr: "PR #31" }))?.actions ?? [];
    assert.ok(a.includes("connect") && a.includes("merge") && !a.includes("retry"), e);
  }
});

test("서버 재시작으로 끊긴 리뷰는 PR을 보고 머지·추가 지시", () => {
  const r = recoveryOf(run("서버 재시작으로 리뷰를 다시 확인해야 해요 → PR을 보고 머지하거나 추가 지시", { status: "waiting_human", pr: "PR #31" }));
  assert.deepEqual(r?.actions, ["merge", "instruct"]);
  assert.match(r?.say ?? "", /재시작/);
});

test("내 Claude 작업의 열린 PR은 추가 지시 대신 PR 코멘트", () => {
  const r = recoveryOf(run("서버 재시작으로 리뷰를 다시 확인해야 해요", { status: "waiting_human", pr: "PR #31", mode: "subscription" }));
  assert.ok(r && !r.actions.includes("instruct") && r.actions.includes("merge"));
  assert.match(r?.say ?? "", /코멘트/);
});

test("읽기 전용 연결 멈춤에는 다시 시도를 보이지 않는다", () => {
  for (const e of ["읽기 전용 연결이라 커밋하지 않음 → 사람 반영 필요", "형상관리 PR 쓰기 실패 (read_only)"]) {
    const a = recoveryOf(run(e))?.actions ?? [];
    assert.ok(a.includes("connect") && !a.includes("retry"), e);
  }
});

test("코드 쓰기·리뷰 관문 보류는 안전 확인 탭으로", () => {
  for (const e of ["안전 점검에서 코드 쓰기 보류", "안전 점검에서 PR 리뷰 보류"]) {
    const r = recoveryOf(run(e, { status: "waiting_human", pr: "PR #31" }));
    assert.ok(r?.actions.includes("holds"), e);
    assert.match(r?.say ?? "", /안전 확인/);
  }
});
