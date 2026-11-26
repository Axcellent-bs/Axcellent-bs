// 의뢰 지시·제작 검사 기록 기본 접힘 (11/26). node --test src/lib/planChecksFold.test.ts
// 부품(components/planning/PlanChecks.tsx)은 TSX라 node --test가 못 읽어, 접힌 줄 글·열림 값은 lib 순수 함수로,
// 접힌 줄(summary) 모양은 소스에서 본다
import assert from "node:assert/strict";
import { readFileSync } from "node:fs";
import { test } from "node:test";
import { checkChip, planChecksCount, planChecksOpen, requestRuleLine, requestRulesChip, type CheckAttemptView, type RequestRuleView } from "./videoPlan.ts";

const rule = (textKo: string, kept: boolean, where = ""): RequestRuleView => ({ kind: "setting", textKo, kept, where }) as RequestRuleView;
const RULES = [rule("저녁 거실", true, "장면 1"), rule("탁자", true, "장면 2"), rule("현관", false), rule("눈", true, "장면 4"),
  rule("서점 앞", true, "장면 4"), rule("채널 이름", true, "엔드카드(마지막 2초, 코드)")];
const LOG = [
  { attempt: 1, by: "planning", passed: false, problems: [{ kind: "actions", scene: 1, detailKo: "장면 1 동작 2개", code: "" }] },
  { attempt: 2, by: "planning", passed: true, problems: [] },
] as unknown as CheckAttemptView[];

test("접힌 줄 개수 칩: 규칙 6개 → '6개', 검사 2판 → '2번 검사', 없으면 ''", () => {
  assert.equal(planChecksCount({ requestRules: RULES }, "rules"), "6개");
  assert.equal(planChecksCount({ checkLog: LOG }, "checks"), "2번 검사");
  assert.equal(planChecksCount({}, "rules"), "");
  assert.equal(planChecksCount({ checkLog: [] }, "checks"), "");
  for (const s of ["6개", "2번 검사"]) assert.doesNotMatch(s, /[✓✕]|지킴|통과|못/);
});

test("기본은 접힘, PDF·인쇄용 open 인자나 인쇄 중이면 펼침", () => {
  assert.equal(planChecksOpen(), false);
  assert.equal(planChecksOpen(false, false), false);
  assert.equal(planChecksOpen(true), true);
  assert.equal(planChecksOpen(false, true), true);
});

test("접힌 줄(summary)에는 ✓/✕·빨강/초록 칩·통과 낱말이 없다", () => {
  const src = readFileSync(new URL("../components/planning/PlanChecks.tsx", import.meta.url), "utf8");
  const summaries = [...src.matchAll(/<summary[\s\S]*?<\/summary>/g)].map((m) => m[0]);
  assert.equal(summaries.length, 1); // 두 칸이 같은 접기 부품을 쓴다
  for (const s of summaries) {
    assert.doesNotMatch(s, /[✓✕]|못 지킨|red-|emerald-|amber-|checkChip|requestRulesChip/);
    assert.match(s, /▸/);
  }
  // 두 블록 모두 기본 접힘 인자(open = false)이고 개수 칩을 쓴다
  assert.match(src, /export function CheckLog\(\{ v, open = false \}/);
  assert.match(src, /export function RequestRules\(\{ v, open = false \}/);
  assert.match(src, /planChecksCount\(v, 'checks'\)/);
  assert.match(src, /planChecksCount\(v, 'rules'\)/);
  // 펼친 안: 지금 내용 그대로 (경고·칩·지시 줄)
  assert.match(src, /AI가 한 번 다시 썼지만 못 지킨 지시가 있어요/);
  assert.match(src, /의뢰와 달라진 점/);
});

test("펼친 안의 글은 지금과 같다 (lib 함수 그대로)", () => {
  assert.equal(requestRulesChip({ requestRules: RULES }), "의뢰 지시 5/6 지킴");
  assert.equal(checkChip({ checkLog: LOG }), "다시 써서 통과");
  assert.match(requestRuleLine(RULES[2]), /못 지킴/);
});

test("기록이 없으면 블록을 그리지 않는다 (칩이 비면 null)", () => {
  assert.equal(requestRulesChip({}), "");
  assert.equal(checkChip({}), "");
  const src = readFileSync(new URL("../components/planning/PlanChecks.tsx", import.meta.url), "utf8");
  assert.match(src, /if \(!chip\) return null[\s\S]*ChecksFold className="plan-check-log"/);
  assert.match(src, /if \(!chip\) return null[\s\S]*ChecksFold className="plan-rules"/);
});
