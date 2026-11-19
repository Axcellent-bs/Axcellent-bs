// node --test로 돈다. 개발 서버 프록시가 진짜 토큰을 서버 쪽에서 붙이는 규칙(token-proxy.ts)과
// run_web.sh가 화면 번들에 자리표시자만 넘기는지 본다.
import assert from "node:assert/strict";
import { readFileSync } from "node:fs";
import { test } from "node:test";
import {
  ADMIN_PLACEHOLDER,
  DATA_PLACEHOLDER,
  HUMAN_PLACEHOLDER,
  TOKEN_ENV,
  findTokenLeaks,
  fromThisPage,
  proxyAuthorization,
  proxyTokens,
} from "../../token-proxy.ts";

const tokens = { human: "real-human-0001", admin: "real-admin-0002", data: "real-data-0003" };
const page = { host: "localhost:3090", "sec-fetch-site": "same-origin" };
const read = (rel: string) => readFileSync(new URL(rel, import.meta.url), "utf-8");

test("사람·관리자 자리표시자는 진짜 토큰으로 바꾼다", () => {
  assert.equal(proxyAuthorization("human", { ...page, authorization: `Bearer ${HUMAN_PLACEHOLDER}` }, tokens), "Bearer real-human-0001");
  assert.equal(proxyAuthorization("human", { ...page, authorization: `Bearer ${ADMIN_PLACEHOLDER}` }, tokens), "Bearer real-admin-0002");
});

test("세션 토큰(axs1.)은 덮어쓰지 않는다", () => {
  assert.equal(proxyAuthorization("human", { ...page, authorization: "Bearer axs1.abc.def" }, tokens), undefined);
  assert.equal(proxyAuthorization("data", { ...page, authorization: "Bearer axs1.abc.def" }, tokens), undefined);
});

test("자리표시자가 아닌 헤더와 헤더 없는 사람 경로는 그대로 둔다", () => {
  assert.equal(proxyAuthorization("human", { ...page, authorization: "Bearer work-token" }, tokens), undefined);
  assert.equal(proxyAuthorization("human", page, tokens), undefined);
});

test("데이터 경로는 헤더가 없거나 데이터 자리표시자면 데이터 토큰을 붙인다", () => {
  assert.equal(proxyAuthorization("data", page, tokens), "Bearer real-data-0003");
  assert.equal(proxyAuthorization("data", { ...page, authorization: `Bearer ${DATA_PLACEHOLDER}` }, tokens), "Bearer real-data-0003");
  assert.equal(proxyAuthorization("data", { ...page, authorization: `Bearer ${HUMAN_PLACEHOLDER}` }, tokens), undefined);
});

test("진짜 토큰이 없으면 바꾸지 않는다", () => {
  assert.equal(proxyAuthorization("human", { ...page, authorization: `Bearer ${HUMAN_PLACEHOLDER}` }, {}), undefined);
  assert.equal(proxyAuthorization("data", page, {}), undefined);
});

test("다른 사이트에서 온 요청에는 붙이지 않는다", () => {
  const evil = { host: "localhost:3090", origin: "https://evil.example", "sec-fetch-site": "cross-site", authorization: `Bearer ${HUMAN_PLACEHOLDER}` };
  assert.equal(proxyAuthorization("human", evil, tokens), undefined);
  assert.equal(proxyAuthorization("data", { host: "localhost:3090", "sec-fetch-site": "cross-site" }, tokens), undefined);
  assert.equal(fromThisPage({ host: "localhost:3090", origin: "http://localhost:3091" }), false);
  assert.equal(fromThisPage({ host: "localhost:3090", origin: "http://localhost:3090", "sec-fetch-site": "same-origin" }), true);
  assert.equal(fromThisPage({ host: "localhost:3090" }), true);
});

test("진짜 토큰은 VITE_ 접두사가 없는 이름으로만 읽는다", () => {
  for (const name of Object.values(TOKEN_ENV)) assert.ok(!name.startsWith("VITE_"), name);
  assert.deepEqual(proxyTokens({ VITE_SAFETY_HUMAN_TOKEN: "x", AX_WEB_HUMAN_TOKEN: " h ", AX_WEB_DATA_A2A_TOKEN: "" }), { human: "h", admin: undefined, data: undefined });
});

test("번들 검사: 진짜 토큰이 든 파일 이름과 환경변수 이름만 알려 준다", () => {
  const leaks = findTokenLeaks(
    [{ name: "assets/a.js", text: `const t="${HUMAN_PLACEHOLDER}"` }, { name: "assets/b.js", text: 'const t="real-admin-0002"' }],
    tokens,
  );
  assert.deepEqual(leaks, [{ file: "assets/b.js", env: "AX_WEB_ADMIN_TOKEN" }]);
});

test("run_web.sh는 VITE_*_TOKEN에 자리표시자만 넣고 web.env를 셸로 읽어 들이지 않는다", () => {
  const sh = read("../../../backend/scripts/run_web.sh");
  assert.match(sh, new RegExp(`VITE_SAFETY_HUMAN_TOKEN=\\$\\(placeholder "\\$AX_WEB_HUMAN_TOKEN" ${HUMAN_PLACEHOLDER}\\)`));
  assert.match(sh, new RegExp(`VITE_SHOP_ADMIN_TOKEN=\\$\\(placeholder "\\$AX_WEB_ADMIN_TOKEN" ${ADMIN_PLACEHOLDER}\\)`));
  assert.match(sh, new RegExp(`VITE_DATA_A2A_TOKEN=\\$\\(placeholder "\\$AX_WEB_DATA_A2A_TOKEN" ${DATA_PLACEHOLDER}\\)`));
  assert.doesNotMatch(sh, /^\s*(\.|source)\s/m, "web.env를 source하면 예전 VITE_ 이름이 그대로 환경에 실린다");
  assert.doesNotMatch(sh, /set -a/);
  // 토큰을 붙이는 서비스는 모두 프록시 주소로 부른다
  for (const v of ["DASHBOARD_API_URL=/dashboard-api", "SAFETY_API_URL=/safety-api", "FINANCE_AGENT_URL=/finance-api", "PLANNING_API_URL=/planning-api", "DEV_API_URL=/dev-api"])
    assert.ok(sh.includes(`VITE_${v}`), v);
});

test("run_all.sh는 web.env에 VITE_ 이름으로 토큰을 쓰지 않는다", () => {
  const sh = read("../../../backend/scripts/run_all.sh");
  assert.doesNotMatch(sh, /echo "VITE_[A-Z_]*TOKEN=/);
  for (const name of Object.values(TOKEN_ENV)) assert.ok(sh.includes(`echo "${name}=`), name);
});

test("데이터 자리표시자는 화면(env.ts)과 같은 값이다", () => {
  assert.ok(read("./env.ts").includes(`DATA_A2A_PLACEHOLDER = '${DATA_PLACEHOLDER}'`));
});
