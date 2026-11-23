// node --test로 돈다. 회사 연결 › 사이트 쓰기 화면(SiteWrites.tsx)의 결과 칩·짝 검사 (lib/siteWrites.ts).
import assert from "node:assert/strict";
import { test } from "node:test";
import {
  appliedLabel, claimBlanks, cleanInbox, parseCodes, pathParams, startWrite, syncParams, writeChip, writeMapProblems, type WriteMap,
} from "./siteWrites.ts";

test("결과 칩: applied=site는 사이트에 반영됨, pending_human은 사이트 반영 대기", () => {
  assert.equal(appliedLabel("site"), "사이트에 반영됨");
  assert.equal(appliedLabel("pending_human"), "사이트 반영 대기");
  assert.equal(appliedLabel(null), null);
  assert.deepEqual(writeChip({ status: "done", reason: null, applied: "site" }), { label: "사이트에 반영됨", tone: "ok" });
  assert.deepEqual(writeChip({ status: "handoff", reason: "direct_off", applied: "pending_human" }), { label: "사이트 반영 대기", tone: "wait" });
  assert.equal(writeChip({ status: "partial", reason: "site_refund_failed", applied: "pending_human" }).tone, "wait");
  assert.equal(writeChip({ status: "unknown", reason: "site_unknown", applied: "pending_human" }).tone, "bad");
  assert.equal(writeChip({ status: "rejected", reason: "site_rejected", applied: null }).label, "사이트가 거절");
  assert.equal(writeChip({ status: "rejected", reason: "no_write_permission", applied: "pending_human" }).label, "키 권한 없음 · 사이트 반영 대기");
  assert.equal(writeChip({ status: "sending", reason: null, applied: null }).label, "보내는 중");
  assert.equal(writeChip({ status: null, reason: null, applied: "site" }).label, "사이트에 반영됨");
});

test("경로 칸: {이름}을 순서대로 찾고, 경로를 바꾸면 짝을 맞춘다", () => {
  assert.deepEqual(pathParams("/api/v1/cancellations/{site_id}/decision"), ["site_id"]);
  assert.deepEqual(pathParams("/a/{x}/b/{y}/{x}"), ["x", "y"]);
  assert.deepEqual(syncParams("/a/{x}/{z}", { x: "link:inquiries.seq", y: "old" }), { x: "link:inquiries.seq", z: "" });
  assert.deepEqual(startWrite("POST", "/api/v1/inquiries/{id}/answer"), {
    method: "POST", path: "/api/v1/inquiries/{id}/answer", path_params: { id: "" }, body: {}, ok: [200, 201],
  });
  assert.equal(startWrite("GET", "/x").method, "POST");
});

test("짝 검사: 누비투어 환불 짝은 통과하고, 서버가 막는 모양은 미리 알려 준다", () => {
  const refund: WriteMap = {
    method: "POST",
    path: "/api/v1/cancellations/{site_id}/decision",
    path_params: { site_id: "link:payments.payment_id" },
    body: { decision: "const:approve", refund_amount: "refund_amount" },
    ok: [200],
    partial_codes: ["REFUND_FAILED"],
  };
  assert.deepEqual(writeMapProblems(refund), []);
  assert.equal(writeMapProblems({ ...refund, path: "//evil.test/x" }).length > 0, true);
  assert.equal(writeMapProblems({ ...refund, path: "/x?y=1" }).length > 0, true);
  assert.ok(writeMapProblems({ ...refund, path_params: {} }).some((p) => p.includes("{이름}")));
  assert.ok(writeMapProblems({ ...refund, path_params: { site_id: " " } }).some((p) => p.includes("site_id")));
  assert.ok(writeMapProblems({ ...refund, body: { "": "x" } }).some((p) => p.includes("이름")));
  assert.ok(writeMapProblems({ ...refund, ok: [500] }).some((p) => p.includes("2xx")));
  assert.ok(writeMapProblems({ ...refund, ok: [] }).some((p) => p.includes("2xx")));
});

test("성공 코드 읽기", () => {
  assert.deepEqual(parseCodes("200, 201"), [200, 201]);
  assert.deepEqual(parseCodes("200"), [200]);
  assert.equal(parseCodes(""), null);
  assert.equal(parseCodes("200, ok"), null);
});

test("신청 받은함: 꼭 있어야 하는 칸과 빈 선택 칸 빼기", () => {
  const m = { path: "/api/v1/cancellations", fields: { claim_id: "id", order_id: " ", fee: "", site_fee: "" } };
  assert.deepEqual(claimBlanks(m), ["주문(예약) 번호", "신청 상태"]);
  assert.deepEqual(cleanInbox({ ...m, fields: { claim_id: "id", site_fee: " " } }).fields, { claim_id: "id" });
});
