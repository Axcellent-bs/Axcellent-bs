import assert from "node:assert/strict";
import { test } from "node:test";
import { defaultPromoTitle, isHolding, promoChip, promoErrorKo, promoItem, promoPending, publishBlock, type PromoStatus } from "./promoSiteView.ts";

const status = (items: PromoStatus["items"], more: Partial<PromoStatus> = {}): PromoStatus => ({
  available: true, feed_ready: true, limit: 3, used: items.filter(isHolding).length, items, ...more,
});

test("올라가 있거나 올리는 중인 것만 자리를 차지한다", () => {
  for (const s of ["awaiting_approval", "approved", "published"]) assert.equal(isHolding({ item_id: "a", status: s }), true);
  for (const s of ["rejected", "failed", "removed", ""]) assert.equal(isHolding({ item_id: "a", status: s }), false);
  assert.equal(isHolding(null), false);
});

test("항목 찾기와 다시 읽어야 하는 때", () => {
  const s = status([{ item_id: "a", status: "published" }, { item_id: "b", status: "awaiting_approval" }]);
  assert.equal(promoItem(s, "a")?.status, "published");
  assert.equal(promoItem(s, "zz"), null);
  assert.equal(promoPending(s), true);
  assert.equal(promoPending(status([{ item_id: "a", status: "published" }])), false);
  assert.equal(promoPending(null), false);
});

test("폴더가 없거나 3개가 차면 올리기를 막는다", () => {
  assert.equal(publishBlock(status([])), null);
  assert.match(publishBlock(status([], { feed_ready: false })) ?? "", /공개 폴더/);
  assert.match(publishBlock(status([], { used: 3 })) ?? "", /3개/);
});

test("목록 칩: 올라간 것이 먼저, 없으면 대기, 둘 다 아니면 없음", () => {
  const s = status([{ item_id: "a", status: "awaiting_approval" }, { item_id: "b", status: "published" }, { item_id: "c", status: "rejected" }]);
  assert.equal(promoChip(s, ["a", "b"])?.text, "홍보 중");
  assert.equal(promoChip(s, ["a", null])?.text, "홍보 승인 대기");
  assert.equal(promoChip(s, ["c", "zz"]), null);
  assert.equal(promoChip(null, ["a"]), null);
});

test("제목 처음 값은 한 줄 40자", () => {
  assert.equal(defaultPromoTitle("  첫   출근\n영상 "), "첫 출근 영상");
  assert.equal(defaultPromoTitle("가".repeat(60)).length, 40);
});

test("오류 코드는 사람 말로, 모르는 값은 그대로", () => {
  assert.match(promoErrorKo("limit_reached") ?? "", /3개/);
  assert.equal(promoErrorKo("something_new"), "something_new");
  assert.equal(promoErrorKo(null), null);
});
