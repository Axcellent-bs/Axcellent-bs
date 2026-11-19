// node --test로 돈다 (npm test). 회원·서버 관리 회원 표의 서버 로그인 규칙: 목록 읽기·역할 바꾸기·프로필 저장 본문.
import assert from "node:assert/strict";
import { test } from "node:test";
import { parseSession, readServerAccount, type ServerAccount } from "./humanSession.ts";
import {
  approverOf, canEditProfile, customerRows, readAccountList, roleChangeProblem, toggleReader, updateBody,
} from "./memberAdmin.ts";

const acc = (id: string, role: ServerAccount["role"], over: Partial<ServerAccount> = {}): ServerAccount =>
  ({ id, name: id, email: `${id}@example.com`, role, created_at: null, ...over });

test("계정 한 줄은 공개 칸만 읽는다: 해시·세대 같은 내부 값이 와도 버리고, 모르는 열람 영역도 버린다", () => {
  const raw = {
    id: "acc-1", name: "예진", email: "yj@example.com", role: "admin", created_at: "2026-11-19T00:00:00+00:00",
    password_hash: "scrypt$...", gen: 3, dept: "개발팀", approver: false, readers: ["finance", "sales", "hr"],
  };
  const a = readServerAccount(raw);
  assert.deepEqual(a, {
    id: "acc-1", name: "예진", email: "yj@example.com", role: "admin", created_at: "2026-11-19T00:00:00+00:00",
    dept: "개발팀", approver: false, readers: ["hr", "finance"],
  });
  assert.equal(readServerAccount({ ...raw, role: "root" }), null);
  assert.equal(readServerAccount({ ...raw, id: 1 }), null);
  assert.equal(readServerAccount({ ...raw, dept: null })?.dept, null);
  assert.equal(readServerAccount({ ...raw, dept: "" })?.dept, null);
});

test("저장한 세션도 프로필 칸을 지키고 내부 값은 버린다", () => {
  const now = 1_800_000_000;
  const raw = JSON.stringify({
    token: "axs1.a.b", expiresAt: now + 60,
    account: { id: "acc-1", name: "회원", email: "m@example.com", role: "member", dept: "재무팀", approver: true, readers: ["finance"], password_hash: "x" },
  });
  const s = parseSession(raw, now);
  assert.equal(s?.account.dept, "재무팀");
  assert.equal(s?.account.approver, true);
  assert.deepEqual(s?.account.readers, ["finance"]);
  assert.equal("password_hash" in (s?.account ?? {}), false);
});

test("목록 응답: 성공이면 공개 칸만 남긴 줄, 실패면 서버 메시지", () => {
  const ok = readAccountList(200, { data: [acc("a", "admin"), { id: 1 }, { ...acc("m", "member"), password_hash: "x" }] });
  assert.equal(ok.ok, true);
  if (ok.ok) {
    assert.deepEqual(ok.rows.map((r) => r.id), ["a", "m"]);
    assert.equal("password_hash" in ok.rows[1], false);
  }
  assert.deepEqual(readAccountList(403, { error: { message: "운영자와 고객사 관리자만 볼 수 있어요" } }), {
    ok: false, message: "운영자와 고객사 관리자만 볼 수 있어요",
  });
  assert.equal(readAccountList(500, null).ok, false);
  assert.equal(readAccountList(200, { data: null }).ok, false);
});

test("회원 표에는 고객사 계정만 (운영자는 빠진다)", () => {
  const rows = [acc("op", "operator"), acc("a", "admin"), acc("m", "member")];
  assert.deepEqual(customerRows(rows).map((r) => r.id), ["a", "m"]);
});

test("확인 권한: 서버 값이 있으면 그 값, 없으면 역할로", () => {
  assert.equal(approverOf(acc("a", "admin")), true);
  assert.equal(approverOf(acc("m", "member")), false);
  assert.equal(approverOf(acc("a", "admin", { approver: false })), false);
  assert.equal(approverOf(acc("m", "member", { approver: true })), true);
});

test("역할 바꾸기: 자기 자신·마지막 관리자·운영자 역할·일반회원이 바꾸기는 막는다", () => {
  const admin = acc("a", "admin");
  const admin2 = acc("a2", "admin");
  const member = acc("m", "member");
  const op = acc("op", "operator");
  const one = [admin, member];
  const two = [admin, admin2, member];
  // 관리자가 일반회원을 관리자로
  assert.equal(roleChangeProblem(admin, member, "admin", one), null);
  // 같은 역할은 바꾸기가 아니다
  assert.equal(roleChangeProblem(admin, admin, "admin", one), null);
  // 자기 강등
  assert.match(roleChangeProblem(admin, admin, "member", two) ?? "", /자기 역할/);
  // 마지막 관리자: 운영자도 못 내린다
  assert.match(roleChangeProblem(op, admin, "member", one) ?? "", /마지막/);
  assert.equal(roleChangeProblem(admin2, admin, "member", two), null);
  // 고객사 관리자는 운영자 역할을 못 주고 운영자 계정을 못 바꾼다
  assert.notEqual(roleChangeProblem(admin, member, "operator", one), null);
  assert.notEqual(roleChangeProblem(admin, op, "admin", one), null);
  // 일반회원은 아무것도 못 바꾼다
  assert.notEqual(roleChangeProblem(member, admin2, "member", two), null);
});

test("프로필을 바꿀 수 있는 사람: 운영자는 모두, 고객사 관리자는 고객사 계정만, 일반회원은 없음", () => {
  assert.equal(canEditProfile({ role: "operator" }, acc("a", "admin")), true);
  assert.equal(canEditProfile({ role: "admin" }, acc("m", "member")), true);
  assert.equal(canEditProfile({ role: "admin" }, acc("op", "operator")), false);
  assert.equal(canEditProfile({ role: "member" }, acc("m", "member")), false);
});

test("저장 본문: 보낸 칸만, 빈 요청은 보내지 않는다", () => {
  assert.equal(updateBody({}), null);
  assert.deepEqual(updateBody({ dept: " 개발팀 " }), { dept: "개발팀" });
  assert.deepEqual(updateBody({ dept: "" }), { dept: "" });
  assert.deepEqual(updateBody({ approver: false }), { approver: false });
  assert.deepEqual(updateBody({ readers: ["finance", "hr", "finance"] }), { readers: ["hr", "finance"] });
  assert.deepEqual(updateBody({ role: "admin", name: " 예진 " }), { role: "admin", name: "예진" });
});

test("열람 담당 켜고 끄기: 순서는 인사·재무", () => {
  assert.deepEqual(toggleReader(undefined, "finance"), ["finance"]);
  assert.deepEqual(toggleReader(["finance"], "hr"), ["hr", "finance"]);
  assert.deepEqual(toggleReader(["hr", "finance"], "hr"), ["finance"]);
});
