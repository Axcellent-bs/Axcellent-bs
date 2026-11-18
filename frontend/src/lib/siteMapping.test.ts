// node --test로 돈다. 다른 형식 API 연결 화면(SiteAdapterConnect.tsx)의 첫 줄 값 미리 보기와 빈 칸 찾기 (lib/siteMapping.ts).
import assert from "node:assert/strict";
import { test } from "node:test";
import { preview, unfilled, type SiteMapping } from "./siteMapping.ts";

const row = { code: "B-1", contact: { name: "홍길동" }, memo: null, long: "가".repeat(50) };

test("preview는 점 경로 값을 보여 주고, 없는 경로는 없는 칸이라고 한다", () => {
  assert.equal(preview(row, "code"), "B-1");
  assert.equal(preview(row, "contact.name"), "홍길동");
  assert.equal(preview(row, "memo"), "null");
  assert.equal(preview(row, "nope"), "없는 칸");
  assert.equal(preview(row, "code.x"), "없는 칸");
  assert.equal(preview(row, "sum:adults,children"), "sum:adults,children");
  assert.equal(preview(row, ""), "—");
  assert.equal(preview(null, "code"), "—");
  assert.equal(preview(row, "long"), `${"가".repeat(40)}…`);
});

test("unfilled는 빈 짝 식과 정하지 않은 상태를 모은다", () => {
  const m: SiteMapping = {
    version: 1,
    auth_header: "Authorization",
    auth_scheme: "Bearer",
    resources: {
      orders: { path: "/b", fields: { order_id: "code", paid_amount: " " }, values: { status: { paid_x: "paid", weird: "" } } },
      products: { path: "/t", fields: { name: "" } },
    },
  };
  assert.deepEqual(unfilled(m), ["orders.paid_amount", "products.name", "orders.status=weird"]);
});
