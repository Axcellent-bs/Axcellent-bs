// node --test로 돈다 (npm test). 서버 로그인 세션 저장·만료·사람 토큰 고르기·LOGIN_DISABLED 폴백.
import assert from "node:assert/strict";
import { test } from "node:test";
import {
  SESSION_KEY, accountInputProblem, canResetPassword, companyChoice, getSession, loginDisabled, parseSession, passwordChangeProblem,
  pickHumanToken, readCompanyPickResponse, readLoginResponse, readLoginStatus, readPasswordChangeResponse, readSessionCompanies,
  sessionFromLogin, setSession, setStoreForTests, withCompanies,
  type HumanSession, type KeyValueStore,
} from "./humanSession.ts";

const NOW = 1_800_000_000;
const account = { id: "acc-1", name: "예진", email: "yj@example.com", role: "admin" as const, created_at: null };
const session = (over: Partial<HumanSession> = {}): HumanSession => ({ token: "axs1.body.sig", expiresAt: NOW + 3600, account, ...over });

const memoryStore = (init: Record<string, string> = {}): KeyValueStore & { data: Record<string, string> } => {
  const data = { ...init };
  return {
    data,
    getItem: (k) => (k in data ? data[k] : null),
    setItem: (k, v) => { data[k] = v; },
    removeItem: (k) => { delete data[k]; },
  };
};

test("저장한 세션: 모양이 맞고 만료 전이면 읽고, 만료·깨진 값·세션 토큰이 아닌 값은 버린다", () => {
  assert.deepEqual(parseSession(JSON.stringify(session()), NOW), session());
  assert.equal(parseSession(JSON.stringify(session({ expiresAt: NOW })), NOW), null);
  assert.equal(parseSession("{not json", NOW), null);
  assert.equal(parseSession(null, NOW), null);
  assert.equal(parseSession(JSON.stringify(session({ token: "shared-human-token" })), NOW), null);
  assert.equal(parseSession(JSON.stringify({ ...session(), account: { ...account, role: "shared" } }), NOW), null);
});

test("로그인 응답 {token, expires_at, account}을 세션으로 바꾼다", () => {
  const s = sessionFromLogin({ token: "axs1.a.b", expires_at: NOW + 60, account }, NOW);
  assert.equal(s?.token, "axs1.a.b");
  assert.equal(s?.expiresAt, NOW + 60);
  assert.equal(s?.account.role, "admin");
  assert.equal(sessionFromLogin({ token: "axs1.a.b" }, NOW), null);
});

test("사람 토큰: 만료 전 세션이 있으면 세션 토큰, 없거나 만료되면 공용 토큰", () => {
  assert.equal(pickHumanToken(session(), "shared", NOW), "axs1.body.sig");
  assert.equal(pickHumanToken(null, "shared", NOW), "shared");
  assert.equal(pickHumanToken(session({ expiresAt: NOW - 1 }), "shared", NOW), "shared");
  assert.equal(pickHumanToken(null, "", NOW), "");
});

test("세션은 sessionStorage 한 칸에 두고, 지우면 칸도 지운다. 바뀔 때 듣는 쪽에 알린다", async () => {
  const store = memoryStore();
  setStoreForTests(store);
  assert.equal(getSession(), null);
  const { onSessionChange } = await import("./humanSession.ts");
  let calls = 0;
  const off = onSessionChange(() => { calls += 1; });
  const live = session({ expiresAt: Math.floor(Date.now() / 1000) + 3600 });
  setSession(live);
  assert.equal(getSession()?.token, live.token);
  assert.ok(store.data[SESSION_KEY]);
  assert.ok(!store.data[SESSION_KEY].includes("password"));
  setSession(null);
  assert.equal(getSession(), null);
  assert.equal(store.data[SESSION_KEY], undefined);
  assert.equal(calls, 2);
  off();
  setStoreForTests(null);
});

test("저장소에 남은 세션이 만료됐으면 다시 열 때 들어가지 않는다", () => {
  const old = session({ expiresAt: Math.floor(Date.now() / 1000) - 10 });
  setStoreForTests(memoryStore({ [SESSION_KEY]: JSON.stringify(old) }));
  assert.equal(getSession(), null);
  setStoreForTests(null);
});

test("LOGIN_DISABLED 폴백: 503 LOGIN_DISABLED·nginx가 막은 경로·옛 서버(경로 없음)는 데모 로그인으로", () => {
  assert.deepEqual(readLoginResponse(503, { error: { code: "LOGIN_DISABLED", message: "AX_SESSION_SECRET가 설정되지 않았다" } }, NOW), { kind: "disabled" });
  assert.deepEqual(readLoginResponse(403, "<html>403 Forbidden</html>", NOW), { kind: "disabled" });
  assert.deepEqual(readLoginResponse(404, { detail: "Not Found" }, NOW), { kind: "disabled" });
  // 다른 503·인증 실패·잠금은 폴백하지 않는다 (서버 로그인이 켜져 있는데 데모로 들어가면 안 된다)
  assert.equal(loginDisabled(503, { error: { code: "UPSTREAM_DOWN" } }), false);
  assert.deepEqual(readLoginResponse(401, { error: { code: "LOGIN_FAILED", message: "이메일이나 비밀번호가 맞지 않아요" } }, NOW), { kind: "error", message: "이메일이나 비밀번호가 맞지 않아요" });
  assert.equal(readLoginResponse(429, { error: { code: "LOGIN_LOCKED", message: "로그인을 여러 번 틀려 잠시 막았어요. 15분 뒤 다시 해 주세요" } }, NOW).kind, "error");
  assert.equal(readLoginResponse(403, { error: { code: "SOMETHING" } }, NOW).kind, "error");
});

test("로그인 성공 응답은 세션을 준다. 모양이 다르면 오류", () => {
  const ok = readLoginResponse(200, { data: { token: "axs1.x.y", expires_at: NOW + 100, account }, meta: {} }, NOW);
  assert.equal(ok.kind, "ok");
  assert.equal(ok.kind === "ok" ? ok.session.account.email : "", "yj@example.com");
  assert.equal(readLoginResponse(200, { data: {} }, NOW).kind, "error");
});

test("로그인 상태: 서버가 켜져 있다고 분명히 말할 때만 서버 로그인", () => {
  assert.equal(readLoginStatus(200, { data: { login_enabled: true }, meta: {} }), true);
  assert.equal(readLoginStatus(200, { data: { login_enabled: false }, meta: {} }), false);
  assert.equal(readLoginStatus(404, undefined), false);
  assert.equal(readLoginStatus(200, { data: { login_enabled: "yes" } }), false);
});

test("계정 만들기 입력: 이름·이메일·비밀번호 10자 이상", () => {
  const ok = { name: "회원", email: "m@example.com", password: "0123456789" };
  assert.equal(accountInputProblem(ok), null);
  assert.match(accountInputProblem({ ...ok, password: "012345678" }) ?? "", /10자 이상/);
  assert.match(accountInputProblem({ ...ok, email: "not-an-email" }) ?? "", /이메일/);
  assert.match(accountInputProblem({ ...ok, name: " " }) ?? "", /이름/);
  assert.match(accountInputProblem({ ...ok, confirm: "다른값다른값다른값" }) ?? "", /확인/);
});

test("비밀번호 바꾸기 입력: 지금 비밀번호 필요, 새 비밀번호 10자 이상·지금과 다름·확인 일치", () => {
  const ok = { current: "temp-pass-1234", next: "my-own-pass-99", confirm: "my-own-pass-99" };
  assert.equal(passwordChangeProblem(ok), null);
  assert.match(passwordChangeProblem({ ...ok, current: "" }) ?? "", /지금 비밀번호/);
  assert.match(passwordChangeProblem({ ...ok, next: "short", confirm: "short" }) ?? "", /10자 이상/);
  assert.match(passwordChangeProblem({ ...ok, next: ok.current, confirm: ok.current }) ?? "", /달라야/);
  assert.match(passwordChangeProblem({ ...ok, confirm: "my-own-pass-98" }) ?? "", /확인/);
});

test("비밀번호 바꾸기 응답: 성공이면 새 세션, 실패면 서버 문구(현재 비밀번호 틀림·잠금·짧음)", () => {
  const ok = readPasswordChangeResponse(200, { data: { token: "axs1.new.sig", expires_at: NOW + 100, account } }, NOW);
  assert.equal(ok.kind === "ok" ? ok.session.token : "", "axs1.new.sig");
  assert.equal(readPasswordChangeResponse(200, { data: { token: "shared" } }, NOW).kind, "error");
  assert.deepEqual(readPasswordChangeResponse(401, { error: { code: "PASSWORD_WRONG", message: "현재 비밀번호가 맞지 않아요" } }, NOW),
    { kind: "error", message: "현재 비밀번호가 맞지 않아요" });
  assert.deepEqual(readPasswordChangeResponse(401, undefined, NOW), { kind: "error", message: "지금 비밀번호가 맞지 않아요." });
  const locked = readPasswordChangeResponse(429, { error: { code: "LOGIN_LOCKED", message: "15분 뒤 다시 해 주세요" } }, NOW);
  assert.equal(locked.kind === "error" ? locked.message : "", "15분 뒤 다시 해 주세요");
  assert.deepEqual(readPasswordChangeResponse(500, {}, NOW), { kind: "error", message: "비밀번호를 바꾸지 못했어요 (500)" });
});

test("비밀번호 초기화 권한: 운영자는 다른 모든 계정, 고객사 관리자는 일반회원만, 자기 계정은 안 된다", () => {
  const op = { id: "op", role: "operator" as const };
  const admin = { id: "ad", role: "admin" as const };
  const member = { id: "m", role: "member" as const };
  assert.equal(canResetPassword(op, admin), true);
  assert.equal(canResetPassword(op, { id: "op2", role: "operator" }), true);
  assert.equal(canResetPassword(op, op), false);
  assert.equal(canResetPassword(admin, member), true);
  assert.equal(canResetPassword(admin, { id: "ad2", role: "admin" }), false);
  assert.equal(canResetPassword(admin, op), false);
  assert.equal(canResetPassword(member, { id: "m2", role: "member" }), false);
});

// ---- 회사 고르기 (여러 회사 설계 1-8): 소속이 둘 이상일 때만 머리글에 고르기 ----

const NUBI = { company_id: "nubi", role: "admin", name: "누비투어", site_url: "" };
const CATBOOKS = { company_id: "catbooks", role: "member", name: "캣북스", site_url: "https://catbooks.example.com" };

test("로그인 응답의 company_list·company_id를 세션에 담는다. 없으면(옛 서버) 칸을 만들지 않는다", () => {
  const s = sessionFromLogin({ token: "axs1.a.b", expires_at: NOW + 60, account, company_list: [NUBI, CATBOOKS], company_id: "nubi" }, NOW);
  assert.deepEqual(s?.companies, [
    { id: "nubi", role: "admin", name: "누비투어" },
    { id: "catbooks", role: "member", name: "캣북스", siteUrl: "https://catbooks.example.com" },
  ]);
  assert.equal(s?.companyId, "nubi");
  const old = sessionFromLogin({ token: "axs1.a.b", expires_at: NOW + 60, account }, NOW);
  assert.ok(old && !("companies" in old) && !("companyId" in old));
  // 저장했다가 다시 읽어도 같다
  assert.deepEqual(parseSession(JSON.stringify(s), NOW), s);
});

test("회사 목록 읽기: 모양이 틀린 줄·같은 회사 두 번째 줄은 버리고 지어내지 않는다", () => {
  assert.deepEqual(readSessionCompanies([NUBI, { company_id: "x" }, "junk", { ...NUBI, name: "다른 이름" }, { ...CATBOOKS, role: "owner" }]), [
    { id: "nubi", role: "admin", name: "누비투어" },
  ]);
  assert.deepEqual(readSessionCompanies(undefined), []);
});

test("회사 고르기는 소속이 둘 이상이고 지금 회사가 그 안에 있을 때만", () => {
  const two = sessionFromLogin({ token: "axs1.a.b", expires_at: NOW + 60, account, company_list: [NUBI, CATBOOKS], company_id: "catbooks" }, NOW);
  const choice = companyChoice(two);
  assert.equal(choice?.current.id, "catbooks");
  assert.deepEqual(choice?.options.map((c) => c.id), ["nubi", "catbooks"]);
  const one = sessionFromLogin({ token: "axs1.a.b", expires_at: NOW + 60, account, company_list: [CATBOOKS], company_id: "catbooks" }, NOW);
  assert.equal(companyChoice(one), null);
  assert.equal(companyChoice(session()), null);
  assert.equal(companyChoice(null), null);
  const stray = sessionFromLogin({ token: "axs1.a.b", expires_at: NOW + 60, account, company_list: [NUBI, CATBOOKS], company_id: "studyon" }, NOW);
  assert.equal(companyChoice(stray), null);
});

test("회사 고르기 응답: 성공이면 새 세션, 403이면 서버 문구", () => {
  const okRes = readCompanyPickResponse(200, { data: { token: "axs1.n.s", expires_at: NOW + 60, account, company_list: [NUBI, CATBOOKS], company_id: "nubi" } }, NOW);
  assert.equal(okRes.kind, "ok");
  if (okRes.kind === "ok") assert.equal(okRes.session.companyId, "nubi");
  assert.deepEqual(readCompanyPickResponse(403, { error: { code: "TENANT_MISMATCH", message: "이 계정이 속한 회사가 아니에요" } }, NOW), { kind: "error", message: "이 계정이 속한 회사가 아니에요" });
  assert.deepEqual(readCompanyPickResponse(500, null, NOW), { kind: "error", message: "회사를 바꾸지 못했어요 (500)" });
  assert.equal(readCompanyPickResponse(200, { data: { token: "nope" } }, NOW).kind, "error");
});

test("내 계정 응답으로 세션의 회사 칸을 고친다. 칸이 없는 응답(옛 서버)은 그대로 둔다", () => {
  const s = session();
  const next = withCompanies(s, { company_list: [NUBI, CATBOOKS], company_id: "catbooks" });
  assert.equal(next.companyId, "catbooks");
  assert.equal(next.companies?.length, 2);
  assert.equal(withCompanies(s, { account }), s);
});
