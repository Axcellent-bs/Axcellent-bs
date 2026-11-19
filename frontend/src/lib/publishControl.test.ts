// node --test로 돈다 (npm test). 자동 게시 스위치 문구가 서버 값만 쓰는지.
import assert from "node:assert/strict";
import { test } from "node:test";
import { publishControlHttpErrorKo, publishControlLine, publishControlResultKo, type PublishControlState } from "./publishControl.ts";

const state = (over: Partial<PublishControlState> = {}): PublishControlState => ({
  channel: "instagram", paused: false, reason: "", updated_by: null, updated_at: null, source: "default", history: [], ...over,
});

test("상태 줄: 꺼져 있으면 사유·바꾼 사람·시각과 수동 게시 안내·초안·미디어는 계속된다는 말", () => {
  const line = publishControlLine(state({ paused: true, reason: "계정 정지 재고 대기", updated_by: "재헌", updated_at: "2026-11-19T01:00:00Z", source: "db" }), (s) => `at ${s}`);
  assert.equal(line.tone, "warn");
  assert.equal(line.title, "자동 게시가 꺼져 있습니다");
  assert.match(line.detail, /수동 게시로 이어집니다/);
  assert.match(line.detail, /사유: 계정 정지 재고 대기/);
  assert.match(line.detail, /재헌 · at 2026-11-19T01:00:00Z/);
  assert.match(line.detail, /초안과 미디어 생성은 계속/);
});

test("상태 줄: 켜짐은 여전히 사람 승인, 기록이 없으면 기본값이라고 적는다", () => {
  const line = publishControlLine(state());
  assert.equal(line.tone, "ok");
  assert.match(line.detail, /사람 승인/);
  assert.match(line.detail, /기본값/);
});

test("상태를 못 받으면 게시 가능이라고 짐작하지 않는다", () => {
  const line = publishControlLine(null);
  assert.equal(line.tone, "bad");
  assert.doesNotMatch(line.title, /켜짐/);
  assert.match(line.detail, /막습니다/);
});

test("켜고 끄기 결과: 끄기는 바로, 켜기 보류는 승인 안내, 실패는 코드별", () => {
  assert.match(publishControlResultKo({ status: "completed", outcome: "updated", output: { paused: true } }).text, /바로 적용/);
  assert.match(publishControlResultKo({ status: "completed", outcome: "updated", output: { paused: false } }).text, /자동 게시를 켰습니다/);
  assert.equal(publishControlResultKo({ status: "completed", outcome: "unchanged", output: null }).kind, "unchanged");
  const held = publishControlResultKo({ status: "input-required", outcome: "held", output: { review_id: "rev_1" } });
  assert.equal(held.kind, "held");
  assert.match(held.text, /사람 승인/);
  const failed = publishControlResultKo({ status: "failed", outcome: "publish_control_error", output: null });
  assert.equal(failed.kind, "failed");
  assert.ok(!failed.text.includes("publish_control_error"));
  assert.match(publishControlResultKo({ status: "failed", outcome: "new_code", output: null }).text, /new_code/);
});

test("연결 오류는 서버 문장 없이 상태별 문구", () => {
  assert.match(publishControlHttpErrorKo(403), /권한/);
  assert.match(publishControlHttpErrorKo(422), /200자/);
  assert.match(publishControlHttpErrorKo(undefined), /다시 시도/);
});
