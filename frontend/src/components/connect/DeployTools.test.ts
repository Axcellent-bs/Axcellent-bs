// node --test로 돈다. 배포 탭(DeployTools.tsx)·새 연결 폼(ToolForm.tsx)이 쓰는 순수 함수 (lib/toolForms.ts).
// 화면 시험 도구(jsdom 등)가 없어서 화면이 그리는 값을 정하는 함수를 시험한다.
import assert from "node:assert/strict";
import { test } from "node:test";
import type { LatestDeploy, ToolConnection, ToolField, ToolKind } from "../../lib/toolConnections";
import { DEPLOY_CHOICES, bodyOf, deployRows, formActions, formReducer, initForm, latestDeployView, missingFields } from "../../lib/toolForms.ts";

const f = (key: string, label: string, extra: Partial<ToolField> = {}): ToolField => ({ key, label, secret: false, required: true, help: "", ...extra });
// 서버 FIELDS(dashboard-api tool_connections.py)와 같은 모양
const FIELDS: Record<ToolKind, ToolField[]> = {
  github_app: [f("app_id", "앱 ID"), f("installation_id", "설치 ID"), f("private_key", "개인 키(.pem)", { secret: true }), f("base_url", "GitHub Enterprise 주소", { required: false })],
  github_actions: [f("app", "GitHub 앱 연결"), f("repo", "레포"), f("workflow", "배포 워크플로 파일"), f("version_input", "버전 입력 이름", { required: false }), f("service", "대상 서비스")],
  jenkins: [f("url", "Jenkins 주소"), f("username", "사용자 이름"), f("api_token", "API 토큰", { secret: true }), f("job", "작업 이름"), f("version_param", "버전 매개변수 이름", { required: false }), f("service", "대상 서비스")],
  dejafix: [f("url", "DejaFix 백엔드 주소"), f("username", "viewer 계정 이름"), f("password", "비밀번호", { secret: true })],
};

const conn = (kind: ToolKind, config: Record<string, string>, extra: Partial<ToolConnection> = {}): ToolConnection => ({
  id: `${kind}-1`, kind, name: kind, config, secrets_set: {}, checked_at: null, check_ok: null, check_detail: null, updated_at: null, updated_by: null, ...extra,
});

test("연결 없음 → 빈 목록 (빈 상태 문구를 그린다)", () => {
  assert.deepEqual(deployRows([]), []);
  // 배포 도구가 아닌 연결은 배포 목록에 넣지 않는다
  assert.deepEqual(deployRows([conn("github_app", { app_id: "1" }), conn("dejafix", { url: "http://d" })]), []);
});

test("배포 목록은 서버 값만: 대상 서비스·도구·마지막 확인·상태", () => {
  const rows = deployRows([
    conn("jenkins", { service: "shop", job: "deploy-shop", url: "https://j" }, { id: "j1", check_ok: true, checked_at: "2026-11-17T01:00:00Z" }),
    conn("github_actions", { service: "api", repo: "o/n", workflow: "deploy.yml", app: "a1" }, { id: "g1", check_ok: false, check_detail: "워크플로에 version 입력이 없어요" }),
  ]);
  assert.deepEqual(rows.map((r) => [r.service, r.tool, r.target, r.status.label]), [
    ["api", "GitHub Actions", "o/n · deploy.yml", "확인 실패"],
    ["shop", "Jenkins", "deploy-shop", "연결됨"],
  ]);
  assert.equal(rows[0].detail, "워크플로에 version 입력이 없어요");
  assert.equal(rows[1].checkedAt, "2026-11-17T01:00:00Z");
  assert.equal(rows[1].detail, null);
});

test("확인 전 연결은 '확인 전' (지어낸 상태 없음)", () => {
  assert.equal(deployRows([conn("jenkins", { service: "x" })])[0].status.label, "확인 전");
});

test("도구 select를 바꾸면 칸이 그 도구 것으로 바뀐다", () => {
  assert.deepEqual(DEPLOY_CHOICES.map((c) => c.id), ["github_actions", "jenkins"]);
  let s = initForm("github_actions", FIELDS.github_actions);
  assert.deepEqual(Object.keys(s.config), ["app", "repo", "workflow", "version_input", "service"]);
  assert.deepEqual(Object.keys(s.secrets), []);
  s = formReducer(s, { type: "config", key: "repo", value: "o/n" });
  s = formReducer(s, { type: "kind", kind: "jenkins", fields: FIELDS.jenkins });
  assert.equal(s.kind, "jenkins");
  assert.deepEqual(Object.keys(s.config), ["url", "username", "job", "version_param", "service"]);
  assert.deepEqual(Object.keys(s.secrets), ["api_token"]);
  assert.equal(s.config.repo, undefined);
});

test("확인 실패 → 항목별 이유를 그대로 남기고 저장은 막힌다", () => {
  let s = initForm("jenkins", FIELDS.jenkins);
  s = formReducer(s, { type: "busy", busy: "test" });
  const checks = [
    { key: "job", label: "작업", ok: true, detail: "작업을 찾았어요" },
    { key: "param", label: "버전 매개변수", ok: false, detail: "매개변수 정의에 version이 없어요" },
  ];
  s = formReducer(s, { type: "tested", ok: false, checks });
  assert.equal(s.passed, false);
  assert.equal(s.checks?.[1].detail, "매개변수 정의에 version이 없어요");
  assert.equal(formActions(true, s).find((a) => a.id === "save")?.disabled, true);
  // 저장할 때 서버가 CHECK_FAILED로 막아도 항목을 보여 준다
  s = formReducer(s, { type: "failed", message: "연결 확인에 실패했어요", checks });
  assert.equal(s.passed, false);
  assert.equal(s.checks?.length, 2);
  // 칸을 고치면 지난 확인 결과는 지운다
  s = formReducer(s, { type: "config", key: "version_param", value: "VERSION" });
  assert.equal(s.checks, null);
  assert.equal(s.passed, null);
});

test("확인이 통과해야 저장 버튼이 켜진다", () => {
  let s = initForm("jenkins", FIELDS.jenkins);
  assert.equal(formActions(true, s).find((a) => a.id === "save")?.disabled, true);
  s = formReducer(s, { type: "tested", ok: true, checks: [] });
  assert.equal(formActions(true, s).find((a) => a.id === "save")?.disabled, false);
});

test("저장 뒤 비밀 칸은 비워진다 (다시 보여 주지 않는다)", () => {
  let s = initForm("jenkins", FIELDS.jenkins);
  s = formReducer(s, { type: "config", key: "url", value: "https://jenkins.example.com" });
  s = formReducer(s, { type: "secret", key: "api_token", value: "11aa22bb33cc" });
  s = formReducer(s, { type: "tested", ok: true, checks: [] });
  s = formReducer(s, { type: "saved" });
  assert.equal(s.secrets.api_token, "");
  assert.equal(s.config.url, "");
  assert.equal(s.passed, null);
});

test("관리자가 아니면 확인·저장 버튼이 없다", () => {
  const s = formReducer(initForm("jenkins", FIELDS.jenkins), { type: "tested", ok: true, checks: [] });
  assert.deepEqual(formActions(false, s), []);
  assert.deepEqual(formActions(true, s).map((a) => a.id), ["test", "save"]);
});

test("보낼 본문: 빈 칸·빈 비밀은 빼고 공백을 지운다", () => {
  let s = initForm("jenkins", FIELDS.jenkins);
  for (const [k, v] of Object.entries({ url: " https://j ", username: "bot", job: "deploy", service: "shop" })) s = formReducer(s, { type: "config", key: k, value: v });
  const body = bodyOf(s);
  assert.deepEqual(body.config, { url: "https://j", username: "bot", job: "deploy", service: "shop" });
  assert.deepEqual(body.secrets, {});
  assert.equal(body.name, "Jenkins deploy");
  assert.equal("id" in body, false);
});

test("고치기: 저장된 값으로 채우고 비밀 칸은 비운다. 저장된 비밀은 비워도 필수 칸이 빈 게 아니다", () => {
  const saved = conn("jenkins", { url: "https://j", username: "bot", job: "deploy", service: "shop" }, { id: "j9", secrets_set: { api_token: "••••" } });
  const s = initForm("jenkins", FIELDS.jenkins, saved);
  assert.equal(s.id, "j9");
  assert.equal(s.config.job, "deploy");
  assert.equal(s.secrets.api_token, "");
  assert.deepEqual(missingFields(FIELDS.jenkins, s, saved), []);
  assert.equal(bodyOf(s).id, "j9");
  // 새 연결은 비밀 칸이 필수
  assert.deepEqual(missingFields(FIELDS.jenkins, initForm("jenkins", FIELDS.jenkins)), ["Jenkins 주소", "사용자 이름", "API 토큰", "작업 이름", "대상 서비스"]);
});

// 서비스별 최근 배포 (dashboard-api GET /api/dashboard/deployments/latest → 인프라 받아 둔 이력)
const dep = (extra: Partial<LatestDeploy> = {}): LatestDeploy => ({
  service: "shop-api", version: "v3.0.1", deployed_at: "2026-11-18T05:05:00Z", source: "github_actions", is_rollback: false, version_from_sha: false, ...extra,
});
const fmt = (iso: string) => `T(${iso})`;

test("최근 배포: 그 서비스 행의 버전 · 시각", () => {
  const v = latestDeployView("shop-api", { state: "ok", rows: [dep({ service: "other", version: "v9" }), dep()] }, fmt);
  assert.deepEqual(v, { tone: "ok", text: "최근 배포 v3.0.1 · T(2026-11-18T05:05:00Z)", note: null });
});

test("최근 배포: 기록이 없으면 '배포 기록 없음' (지어내지 않는다)", () => {
  assert.deepEqual(latestDeployView("shop-api", { state: "ok", rows: [] }, fmt), { tone: "off", text: "배포 기록 없음", note: null });
  assert.equal(latestDeployView("", { state: "ok", rows: [dep()] }, fmt).text, "배포 기록 없음");
});

test("최근 배포: 못 읽으면 '확인할 수 없음' + 이유, 읽는 중은 따로", () => {
  assert.deepEqual(latestDeployView("shop-api", { state: "error", reason: "incident에 연결할 수 없다" }, fmt), { tone: "off", text: "최근 배포 확인할 수 없음", note: "incident에 연결할 수 없다" });
  assert.equal(latestDeployView("shop-api", { state: "loading" }, fmt).text, "최근 배포 불러오는 중…");
});

test("최근 배포: 커밋 앞자리 버전·롤백 다시 배포는 그대로 밝힌다", () => {
  assert.equal(latestDeployView("shop-api", { state: "ok", rows: [dep({ version: "abc1234", version_from_sha: true })] }, fmt).text, "최근 배포 커밋 abc1234 · T(2026-11-18T05:05:00Z)");
  assert.equal(latestDeployView("shop-api", { state: "ok", rows: [dep({ is_rollback: true })] }, fmt).note, "롤백으로 다시 배포한 버전");
  assert.equal(latestDeployView("shop-api", { state: "ok", rows: [dep({ source: "jenkins" })] }, fmt).note, null);
});
