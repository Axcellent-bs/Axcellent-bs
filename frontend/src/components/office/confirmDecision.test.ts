// node --test (npm test). 롤백 설계 3-b: 서비스 이름 확인(confirm)을 결정 요청에 싣는 길.
// 화면을 띄우지 않고 소스를 읽어 본다: finance.decide 본문, ChatReviewDecision 호출, 기존 호출자(confirm 미전달).
import assert from "node:assert/strict";
import { readFileSync } from "node:fs";
import { test } from "node:test";

const read = (rel: string) => readFileSync(new URL(rel, import.meta.url), "utf8");

test("finance.decide는 confirm이 있을 때만 본문에 넣고, 기존 본문은 그대로다", () => {
  const src = read("../../lib/finance.ts");
  const line = src.split("\n").find((l) => l.includes("/decision`"));
  assert.ok(line, "decide 호출 줄");
  assert.match(line, /decision, note: note \|\| null, \.\.\.\(decidedAs \? \{ decided_as: decidedAs \} : \{\}\), \.\.\.\(confirm !== undefined \? \{ confirm \} : \{\}\)/);
  assert.match(src, /decidedAs\?: string \| null, confirm\?: string\)/);
});

test("ChatReviewDecision은 confirmName이 있는 승인에서만 입력한 이름을 confirm으로 넘긴다", () => {
  const src = read("./ChatReviewDecision.tsx");
  assert.match(src, /finance\.decide\(reviewId, decision, undefined, true, decidedAs \?\? undefined, confirmName !== undefined && decision === "approved" \? typedName : undefined\)/);
});

test("기존 호출자는 confirm을 보내지 않는다(결정 호출 인자 5개 이하, confirmName 없음)", () => {
  for (const f of ["../finance/SafetyQueue.tsx", "../marketing/ApprovalPanel.tsx"]) {
    const m = /finance\.decide\(([^;]*)\);/.exec(read(f));
    assert.ok(m, `${f} decide 호출`);
    assert.ok(m[1].split(",").length <= 5, `${f} 인자 수`);
  }
  for (const f of ["./RequestCreative.tsx", "./RequestPublish.tsx"]) {
    const src = read(f);
    for (const m of src.matchAll(/<ChatReviewDecision\b[^>]*>/g)) assert.doesNotMatch(m[0], /confirmName/, f);
  }
  assert.match(read("./RequestProgressCard.tsx"), /<ChatReviewDecision[^>]*confirmName=\{decision\.service\}/);
});
