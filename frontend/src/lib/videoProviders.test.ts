// node --test로 돈다 (npm test). 공급자 선택 문구와 직접 추가 양식 검사.
import assert from "node:assert/strict";
import { test } from "node:test";
import { CUSTOM_SPEC_TEMPLATE, parseCustomSpec, providerOptionLabel, selectionProblem, videoProviderResultKo, type VideoProviderRow } from "./videoProviders.ts";

const row = (over: Partial<VideoProviderRow> = {}): VideoProviderRow => ({
  id: "kling", label: "Kling", kind: "builtin", key_env: "MARKETING_KLING_API_KEY", key_configured: true,
  resolutions: ["720p"], min_duration_s: 3, max_duration_s: 20, models: ["kling-4.0"], priced: true, selectable: true, ...over,
});

test("드롭다운 라벨에 막힌 사유가 붙는다", () => {
  assert.equal(providerOptionLabel(row()), "Kling");
  assert.equal(providerOptionLabel(row({ priced: false, models: [], key_configured: false })), "Kling (단가 미확인, 키 없음)");
  assert.equal(providerOptionLabel(row({ kind: "custom", selectable: false })), "Kling (직접 추가, 꺼짐)");
});

test("선택 문제: 단가 없는 내장은 막고, 목은 통과", () => {
  assert.equal(selectionProblem(row(), "kling-4.0"), null);
  assert.match(selectionProblem(row({ priced: false, models: [] }), "x") ?? "", /단가/);
  assert.match(selectionProblem(row(), "other") ?? "", /단가가 확인된 모델/);
  assert.match(selectionProblem(row({ key_configured: false }), "kling-4.0") ?? "", /MARKETING_KLING_API_KEY/);
  assert.equal(selectionProblem(row({ id: "mock", key_env: null, priced: true, models: [] }), ""), null);
  assert.match(selectionProblem(undefined, "") ?? "", /목록에 없는/);
});

test("응답 문구: 보류는 승인 안내, 실패는 서버 사유코드를 한국어로", () => {
  assert.equal(videoProviderResultKo({ status: "input-required", outcome: "held", output: null }).kind, "held");
  assert.equal(videoProviderResultKo({ status: "completed", outcome: "selected", output: null }).kind, "done");
  const bad = videoProviderResultKo({ status: "failed", outcome: "invalid_spec", output: { reason: "baseUrl: 허용 목록에 없다" } });
  assert.equal(bad.kind, "failed");
  assert.match(bad.text, /baseUrl/);
});

test("직접 추가 양식: JSON·키 값 검사, 예시는 통과", () => {
  assert.equal(parseCustomSpec(JSON.stringify(CUSTOM_SPEC_TEMPLATE)).ok, true);
  assert.equal(parseCustomSpec("{").ok, false);
  assert.equal(parseCustomSpec("[]").ok, false);
  const leaked = parseCustomSpec(JSON.stringify({ ...CUSTOM_SPEC_TEMPLATE, label: "sk-abcdefghijklmnop1234" }));
  assert.equal(leaked.ok, false);
});
