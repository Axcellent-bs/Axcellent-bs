// node --test로 돈다 (npm test). Higgsfield 줄의 실패 사유별 표시 (11/24, 화면 시안).
import assert from "node:assert/strict";
import { test } from "node:test";
import { checkedAt, reasonLine, reasonView } from "./higgsfieldReason.ts";

test("사유별 표시 문구 (시안 표)", () => {
  assert.deepEqual(reasonView("network"), { tone: "wait", label: "닿지 않음" });
  assert.deepEqual(reasonView("timeout"), { tone: "wait", label: "닿지 않음" });
  assert.deepEqual(reasonView("http_502"), { tone: "wait", label: "서버 오류" });
  assert.deepEqual(reasonView("refresh_failed"), { tone: "wait", label: "토큰 갱신 실패" });
  assert.deepEqual(reasonView("tool_error"), { tone: "bad", label: "도구 오류" });
  assert.deepEqual(reasonView("bad_response"), { tone: "bad", label: "응답 형식 오류" });
  assert.deepEqual(reasonView("rpc_error"), { tone: "bad", label: "응답 형식 오류" });
  assert.deepEqual(reasonView("client_error"), { tone: "bad", label: "요청 만들기 실패" });
  assert.deepEqual(reasonView("reconnect_needed"), { tone: "bad", label: "다시 연결 필요" });
  assert.deepEqual(reasonView("unauthorized"), { tone: "bad", label: "다시 연결 필요" });
});

test("사유가 없거나 모르는 코드면 지금 표시 그대로 (null)", () => {
  assert.equal(reasonView(null), null);
  assert.equal(reasonView(undefined), null);
  assert.equal(reasonView("client_id_missing"), null);
  assert.equal(reasonView("http_x"), null);
  assert.equal(reasonLine(null, "2026-11-24T06:17:00Z"), null);
});

test("아래 줄: 사유 코드 · 확인 시각(한국)", () => {
  assert.equal(checkedAt("2026-11-24T06:17:00Z"), "11/24 15:17");
  assert.equal(reasonLine("tool_error", "2026-11-24T06:17:00Z"), "사유 tool_error · 확인 11/24 15:17");
  assert.equal(reasonLine("network", "엉뚱한 값"), "사유 network");
});
