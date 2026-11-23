// node --test로 돈다 (npm test).
import assert from "node:assert/strict";
import { test } from "node:test";
import { campaignIdForProposal, missingVideoFields, startCampaignId, startedConflict, startFields, type StartForm } from "./campaignStart.ts";

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

test("받은 기획안으로 시작하면 번호는 cmp_<proposal_id>, 없으면 예전처럼 cmp_<시각>", () => {
  assert.equal(startCampaignId("prop_20261123_a", 1_763_856_000_000), "cmp_prop_20261123_a");
  assert.equal(startCampaignId(" prop_x ", 1), "cmp_prop_x");
  assert.equal(startCampaignId(null, 1_763_856_000_000), "cmp_1763856000000");
  assert.equal(startCampaignId("", 42), "cmp_42");
  assert.equal(campaignIdForProposal("p1"), "cmp_p1");
});

test("이미 시작한 번호(취소 포함)는 다시 시작하지 않게 안내한다", () => {
  const started = { cmp_p1: { status: "active" }, cmp_p2: { status: "canceled" } };
  assert.match(startedConflict("cmp_p1", started) ?? "", /이미 시작했어요\(진행 중\)/);
  assert.match(startedConflict("cmp_p2", started) ?? "", /취소됨/);
  assert.equal(startedConflict("cmp_p3", started), null);
});
