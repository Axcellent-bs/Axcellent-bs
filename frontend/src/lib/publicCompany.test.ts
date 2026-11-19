// node --test로 돈다 (npm test). 로그인 전 회사 공개 정보 읽기·거절 구분·회사 고르기.
import assert from "node:assert/strict";
import { test } from "node:test";
import { pickCompany, readPublicCompany, refusedForThisPerson } from "./publicCompany.ts";

test("공개 회사 정보는 id·이름·사이트 주소만 읽는다", () => {
  const body = { data: { company: { id: "c-k1", name: " 고양이책방 ", site_url: "https://cat.example", key: "AXC-SECRET" } } };
  assert.deepEqual(readPublicCompany(body), { id: "c-k1", name: "고양이책방", siteUrl: "https://cat.example" });
});

test("연결 전(null)·모양이 틀린 응답은 회사 없음", () => {
  assert.equal(readPublicCompany({ data: { company: null } }), null);
  assert.equal(readPublicCompany({ data: { company: { id: "x", name: "  " } } }), null);
  assert.equal(readPublicCompany(undefined), null);
  assert.equal(readPublicCompany({ error: { code: "AUTH_REQUIRED" } }), null);
});

test("사이트 주소는 http(s)만 받는다", () => {
  const c = readPublicCompany({ data: { company: { id: "a", name: "회사", site_url: "javascript:alert(1)" } } });
  assert.deepEqual(c, { id: "a", name: "회사" });
});

test("401·403만 이 사람에게 거절된 것으로 본다 (연결 실패·서버 오류는 지난 값 유지)", () => {
  assert.equal(refusedForThisPerson({ status: 401 }), true);
  assert.equal(refusedForThisPerson({ status: 403 }), true);
  assert.equal(refusedForThisPerson({ status: 502 }), false);
  assert.equal(refusedForThisPerson(new TypeError("Failed to fetch")), false);
  assert.equal(refusedForThisPerson(null), false);
});

test("로그인 전에는 공개 정보로, 로그인 뒤에는 서버 목록으로 회사를 정한다", () => {
  const keyed = { id: "k" };
  const site = { id: "s" };
  const pub = { id: "p" };
  assert.equal(pickCompany(null, null, pub), pub); // 로그인 전: 토큰이 없어 목록은 비어도 회사는 있다
  assert.equal(pickCompany(keyed, site, pub), keyed);
  assert.equal(pickCompany(null, site, pub), site);
  assert.equal(pickCompany(null, null, null), null);
});
