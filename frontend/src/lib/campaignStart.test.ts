// node --test로 돈다 (npm test).
import assert from "node:assert/strict";
import { test } from "node:test";
import { missingVideoFields, startFields, type StartForm } from "./campaignStart.ts";

const FULL: StartForm = { isVideo: true, category: " 아재개그 ", format: "shorts", production: "0", endsOn: "2026-11-25" };

test("영상 캠페인은 카테고리·형식·제작비·종료일이 모두 있어야 한다(제작비 0은 입력으로 본다)", () => {
  assert.deepEqual(missingVideoFields(FULL), []);
  assert.deepEqual(missingVideoFields({ ...FULL, category: " ", format: "", production: "", endsOn: "" }),
    ["콘텐츠 카테고리", "형식", "영상 제작비(없으면 0)", "종료일"]);
});

test("영상이 아니면 필수 칸 검사를 하지 않는다", () => {
  assert.deepEqual(missingVideoFields({ ...FULL, isVideo: false, category: "", format: "", production: "" }), []);
});

test("영상이면 campaign_kind·제작비 0을 보낸다", () => {
  assert.deepEqual(startFields(FULL), {
    campaign_kind: "video", content_category: "아재개그", format: "shorts", production_cost_krw: 0, ends_on: "2026-11-25",
  });
});

test("영상이 아니면 campaign_kind와 제작비를 보내지 않는다", () => {
  assert.deepEqual(startFields({ ...FULL, isVideo: false, production: "5" }),
    { content_category: "아재개그", format: "shorts", ends_on: "2026-11-25" });
});
