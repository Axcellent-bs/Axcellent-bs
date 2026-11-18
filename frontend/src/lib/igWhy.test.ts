import { test } from "node:test";
import assert from "node:assert/strict";
import { factLabel, factText, factTone } from "./igWhy.ts";
import type { InstagramWhyFact } from "./data.ts";

const fact = (f: Partial<InstagramWhyFact>): InstagramWhyFact => ({
  kind: "format", label: "형식: 캐러셀 비율", value: null, baseline: null, ratio: null, reason: null, ...f,
});

test("값 (평소 값, ×배수)", () => {
  assert.equal(factText(fact({ value: "100%", baseline: "10%", ratio: 10 })), "100% (평소 10%, ×10)");
  assert.equal(factText(fact({ value: "10.0%", baseline: "2.0%", ratio: 4.96 })), "10.0% (평소 2.0%, ×5)");
  assert.equal(factText(fact({ value: "3곳 · 첫~마지막 2시간" })), "3곳 · 첫~마지막 2시간");
  assert.equal(factText(fact({ value: "2개 채널", baseline: "0개 채널" })), "2개 채널 (평소 0개 채널)");
});

test("모르면 0이 아니라 모름 + 이유", () => {
  const f = fact({ kind: "cross", reason: "네이버 커넥터가 없음" });
  assert.equal(factText(f), "모름 · 네이버 커넥터가 없음");
  assert.equal(factTone(f), "muted");
});

test("unknown 줄은 주의색, 라벨은 앞부분만", () => {
  const f = fact({ kind: "unknown", label: "쓴 오디오: 공식 API가 게시물 오디오를 주지 않음", reason: "쓴 오디오: 공식 API가 게시물 오디오를 주지 않음" });
  assert.equal(factTone(f), "caution");
  assert.equal(factLabel(f), "쓴 오디오");
  assert.equal(factText(f), "쓴 오디오: 공식 API가 게시물 오디오를 주지 않음");
  assert.equal(factTone(fact({ value: "1위" })), "plain");
});
