// node --test로 돈다 (npm test).
import assert from "node:assert/strict";
import { test } from "node:test";
import { paidOrderNote } from "./labels.ts";

test("취소 클레임 지급 기록은 판매 서버가 주문도 취소했는지 보여 준다 (#1591)", () => {
  assert.equal(paidOrderNote({ refund_cash: 15300, order_cancelled: true }), "주문 취소됨");
  assert.equal(paidOrderNote({ order_cancelled: false }), "주문은 취소되지 않음(발송 뒤·부분 환불)");
});

test("값이 없는 옛 기록·반품·교환 지급은 아무것도 적지 않는다", () => {
  assert.equal(paidOrderNote({ refund_cash: 15300, compensation: 0 }), null);
  assert.equal(paidOrderNote({ order_cancelled: "true" }), null);
});
