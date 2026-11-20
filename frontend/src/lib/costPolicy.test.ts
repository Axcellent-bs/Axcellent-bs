// node --test로 돈다 (npm test). 사용량·안전 정책 창의 순수 계산(고칠 수 있는 정책은 영상 길이 하나).
import assert from "node:assert/strict";
import { test } from "node:test";
import {
  canApply, historyLineKo, openRequests, parseValue, policyHttpErrorKo, readCostPolicy, requestLineKo, requestProblem, requestResultKo,
  requestStatusKo, sourceKo, valueKo, type CostPolicyState,
} from "./costPolicy.ts";

const item = (over: Record<string, unknown> = {}) => ({
  key: "video_duration", label: "영상 길이", scope: "video_duration", unit: "s", op: ">", default: 90, value: 90, min: 1, absolute_max: 90,
  source: "default", updated_by: null, updated_at: null, ...over,
});
const raw = (over: Record<string, unknown> = {}) => ({
  editable: true,
  items: [item()],
  requests: [], history: [], viewer: { can_edit_policy: true }, ...over,
});
const state = (over: Record<string, unknown> = {}) => readCostPolicy(raw(over)) as CostPolicyState;

test("읽기: 목록이 없으면 null, 모양이 틀린 항목은 버린다, 권한은 서버 값만", () => {
  assert.equal(readCostPolicy(null), null);
  assert.equal(readCostPolicy({}), null);
  const s = state({ items: [item(), { key: "x" }, item({ key: "y", value: "a" })], viewer: {} });
  assert.equal(s.items.length, 1);
  assert.equal(s.viewer.can_edit_policy, false);
  assert.equal(state().editable, true);
  assert.equal(state({ editable: false }).editable, false);
});

test("값 표기와 입력 해석", () => {
  assert.equal(valueKo(2000, "krw"), "2,000원"); // 예전 서버가 원 단위 항목을 주는 경우 표기만 남아 있다
  assert.equal(valueKo(90, "s"), "90초");
  assert.equal(valueKo(null, "krw"), "-");
  assert.equal(parseValue("2,500"), 2500);
  assert.equal(parseValue(""), null);
  assert.ok(Number.isNaN(parseValue("1.5")));
  assert.equal(sourceKo({ source: "file" }), "변경 파일 값");
  assert.match(sourceKo({ source: "default" }), /기본값/);
});

test("요청 검사: 하한~절대 상한, 같은 값, 사유", () => {
  const rule = state().items[0];
  assert.match(requestProblem(undefined, 1, "r") ?? "", /항목/);
  assert.match(requestProblem(rule, null, "r") ?? "", /값을 적어/);
  assert.match(requestProblem(rule, Number.NaN, "r") ?? "", /숫자/);
  assert.match(requestProblem(rule, 0, "r") ?? "", /1초~90초/);
  assert.match(requestProblem(rule, 91, "r") ?? "", /코드 상수/);
  assert.match(requestProblem(rule, 90, "r") ?? "", /같습니다/);
  assert.match(requestProblem(rule, 45, "  ") ?? "", /사유/);
  assert.equal(requestProblem(rule, 45, "숏폼만 쓴다"), null);
  assert.equal(requestProblem(rule, 1, "r"), null);
});

test("요청 줄: 승인됐고 적용 전만 적용 가능, 적용된 것은 숨긴다", () => {
  const s = state({
    requests: [
      { review_id: "a", status: "approved", rule_key: "video_duration", requested_value: 30, requested_by: "재무", reason: "r" },
      { review_id: "b", status: "pending", rule_key: "video_duration", requested_value: 60, requested_by: "재무", reason: "r" },
      { review_id: "c", status: "approved", rule_key: "video_duration", requested_value: 45, requested_by: "재무", reason: "r" },
      { review_id: "d", status: "rejected", rule_key: "video_duration", requested_value: 40, requested_by: "재무", reason: "r" },
    ],
    history: [{ at: "2026-11-19T00:00:00Z", key: "video_duration", old: 90, new: 45, requested_by: "재무", approved_by: "총괄", review_id: "c", reason: "r" }],
  });
  assert.deepEqual(openRequests(s).map((r) => r.review_id), ["a", "b"]);
  assert.equal(canApply(s, s.requests[0]), true);
  assert.equal(canApply(s, s.requests[1]), false);
  assert.equal(canApply(s, s.requests[2]), false);
  assert.match(requestStatusKo("approved"), /적용 버튼/);
  assert.equal(requestLineKo(s.items, s.requests[0]), "영상 길이: 90초 → 30초");
  assert.match(historyLineKo(s.items, s.history[0]), /90초 → 45초 \(요청 재무, 승인 총괄, r\)/);
});

test("오류 문구: 서버 코드별, 코드 없으면 상태별", () => {
  assert.match(policyHttpErrorKo(409, "NOT_APPROVED"), /승인/);
  assert.match(policyHttpErrorKo(409, "ALREADY_APPLIED"), /이미 적용/);
  assert.match(policyHttpErrorKo(400, "VALUE_OUT_OF_RANGE"), /범위/);
  assert.match(policyHttpErrorKo(403, "POLICY_EDITOR_REQUIRED"), /권한/);
  assert.match(policyHttpErrorKo(400, "POLICY_FILE_NOT_CONFIGURED"), /설정/);
  assert.match(policyHttpErrorKo(502, "SAFETY_UNREACHABLE"), /안전 서버/);
  assert.match(policyHttpErrorKo(403), /운영자·관리자/);
  assert.match(policyHttpErrorKo(500), /닿지 못/);
});

test("요청 응답: held가 정상, 그 밖은 접수 안 됨", () => {
  const ok = requestResultKo({ status: "held", review_id: "rev_1" });
  assert.equal(ok.kind, "held");
  assert.equal(ok.reviewId, "rev_1");
  assert.match(ok.text, /다른 사람이 승인/);
  assert.equal(requestResultKo({ status: "rejected" }).kind, "failed");
  assert.equal(requestResultKo(null).kind, "failed");
});
