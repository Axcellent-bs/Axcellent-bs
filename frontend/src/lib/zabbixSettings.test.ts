// node --test로 돈다. 회원·서버 관리 › DejaFix 연결 › "Zabbix 연결"이 그리는 값(lib/dejafix.ts).
// 응답 모양이 다르면 지어내지 않고(null), 토큰 값은 어디에도 없다.
import assert from "node:assert/strict";
import { test } from "node:test";
import { parseZabbixSettings, zabbixSummary } from "./zabbixSettings.ts";

const ok = {
  data: {
    source: "file", url: "https://zbx.example/api_jsonrpc.php", token_set: true, verify_tls: false,
    updated_at: "2026-11-25T09:00:00Z", updated_by: "admin", mock: false,
  },
};

test("화면에서 정한 연결: 주소·토큰 있음·TLS 끔 그대로", () => {
  const z = parseZabbixSettings(ok);
  assert.deepEqual(z, { ...ok.data, verify_tls: false });
  assert.equal(z && zabbixSummary(z), "화면에서 정함 · https://zbx.example/api_jsonrpc.php · 토큰 있음");
});

test("서버 설치 설정·연결 없음 한 줄", () => {
  const env = parseZabbixSettings({ data: { source: "env", url: "http://z", token_set: false } });
  assert.equal(env && zabbixSummary(env), "서버 설치 설정 · http://z · 토큰 없음");
  assert.equal(env?.verify_tls, true);
  const none = parseZabbixSettings({ data: { source: "none", url: null, token_set: false } });
  assert.equal(none && zabbixSummary(none), "Zabbix가 아직 연결되지 않았어요");
});

test("모르는 모양은 null", () => {
  assert.equal(parseZabbixSettings(null), null);
  assert.equal(parseZabbixSettings({ data: { source: "vault" } }), null);
  assert.equal(parseZabbixSettings({ error: { code: "X" } }), null);
});

test("토큰이 보호되지 않는 연결이면 경고", async () => {
  const { zabbixInsecure } = await import("./zabbixSettings.ts");
  assert.match(zabbixInsecure("http://zbx.example/api_jsonrpc.php", true) ?? "", /http:\/\//);
  assert.match(zabbixInsecure("https://zbx.example/api_jsonrpc.php", false) ?? "", /인증서/);
  assert.equal(zabbixInsecure("https://zbx.example/api_jsonrpc.php", true), null);
  assert.equal(zabbixInsecure("", false), null);
});

test("옛 DejaFix(501 · DEJAFIX_TOO_OLD)일 때만 저장 폼을 숨긴다", async () => {
  const { ZabbixCallError, zabbixUnsupported } = await import("./zabbixSettings.ts");
  assert.equal(zabbixUnsupported(new ZabbixCallError("이 DejaFix는 아직 화면에서 Zabbix를 바꾸지 못해요", 501, "DEJAFIX_TOO_OLD")), true);
  assert.equal(zabbixUnsupported(new ZabbixCallError("x", 501, null)), true);
  assert.equal(zabbixUnsupported(new ZabbixCallError("x", 200, "DEJAFIX_TOO_OLD")), true);
  for (const status of [401, 403, 409, 502]) assert.equal(zabbixUnsupported(new ZabbixCallError("x", status, null)), false);
  assert.equal(zabbixUnsupported(new Error("DejaFix 서버에 연결할 수 없습니다.")), false);
  assert.equal(zabbixUnsupported(undefined), false);
});
