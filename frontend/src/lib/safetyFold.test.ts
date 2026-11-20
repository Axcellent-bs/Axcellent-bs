// node --test로 돈다 (npm test). 안전 처리 로그 합치기: 통과는 요청 말풍선의 칩 하나로, 보류·거절은 따로 (lib/safetyFold.ts)
import assert from "node:assert/strict";
import { test } from "node:test";
import { foldSafety } from "./safetyFold.ts";

type M = { kind: "msg"; id: string; at: string; fromKey: string; from: string; text: string; tone: string; toKey?: string; execId?: string; safetyRole?: "request" | "check"; human?: boolean; safety?: { text: string; at: string; from: string }[] };
const at = (s: number) => new Date(Date.parse("2026-11-20T07:28:00Z") + s * 1000).toISOString();
const msg = (id: string, s: number, o: Partial<M>): M => ({ kind: "msg", id, at: at(s), fromKey: "marketing", from: "마케팅 AI", text: id, tone: "request", ...o });

test("같은 execution_id의 실행 요청·검증 통과·실행함은 실행 요청 말풍선 하나 + 칩으로", () => {
  const items = [
    msg("exe:e1:req", 0, { text: "쿠폰 지급 실행 요청", execId: "e1", safetyRole: "request" }),
    msg("exe:e1:verify", 0, { fromKey: "finance", from: "재무 AI", text: "검증 통과", tone: "ok", execId: "e1", safetyRole: "check" }),
    msg("exe:e1:res", 0, { fromKey: "safety", from: "안전 처리 로그", text: "실행됨 (B등급)", tone: "ok", execId: "e1", safetyRole: "check" }),
    msg("other", 5, { text: "다른 말" }),
  ];
  const out = foldSafety(items) as M[];
  assert.deepEqual(out.map((x) => x.id), ["exe:e1:req", "other"]);
  assert.deepEqual(out[0].safety?.map((l) => l.text), ["검증 통과", "실행됨 (B등급)"]);
});

test("부서가 보낸 같은 건 요청 줄이 있으면 거기에 붙인다 (safety의 확인 중·실행해도 돼요 줄 포함)", () => {
  const items = [
    msg("log:1", 0, { fromKey: "support", from: "고객지원 AI", toKey: "safety", text: "환불 부탁해요", execId: "e2" }),
    msg("log:2", 1, { fromKey: "safety", from: "안전 처리 로그", toKey: "support", text: "환불 — 실행해도 되는지 확인하고 있어요", tone: "request", execId: "e2" }),
    msg("log:3", 2, { fromKey: "safety", from: "안전 처리 로그", toKey: "support", text: "환불 — 실행해도 돼요", tone: "ok", execId: "e2" }),
  ];
  const out = foldSafety(items) as M[];
  assert.deepEqual(out.map((x) => x.id), ["log:1"]);
  assert.equal(out[0].safety?.length, 2);
});

test("execution_id 없는 safety 통과 줄은 2분 안에 받는 부서가 보낸 바로 앞 줄에, 2분 넘으면 그대로", () => {
  const items = [
    msg("log:a", 0, { fromKey: "planning", from: "기획 AI", toKey: "memory", text: "회사 기억에 남기기 부탁해요" }),
    msg("log:b", 30, { fromKey: "safety", from: "안전 처리 로그", toKey: "planning", text: "회사 기억에 남기기 — 실행해도 돼요", tone: "ok" }),
    msg("log:c", 400, { fromKey: "safety", from: "안전 처리 로그", toKey: "planning", text: "늦은 확인", tone: "ok" }),
  ];
  const out = foldSafety(items) as M[];
  assert.deepEqual(out.map((x) => x.id), ["log:a", "log:c"]);
  assert.equal(out[0].safety?.[0].text, "회사 기억에 남기기 — 실행해도 돼요");
});

test("보류·거절이 섞인 묶음은 합치지 않는다 (보류는 지금처럼 따로 크게)", () => {
  const held = [
    msg("exe:e3:req", 0, { text: "환불 실행 요청", execId: "e3", safetyRole: "request" }),
    msg("exe:e3:verify", 0, { text: "검증 통과", tone: "ok", execId: "e3", safetyRole: "check" }),
    msg("exe:e3:res", 0, { text: "보류 — 사람 결정 대기 (C등급)", tone: "warn", execId: "e3", safetyRole: "check" }),
    { kind: "hold", id: "exe:e3:hold", at: at(0) },
  ];
  assert.deepEqual(foldSafety(held).map((x) => x.id), ["exe:e3:req", "exe:e3:verify", "exe:e3:res", "exe:e3:hold"]);
  const rejected = [
    msg("log:r1", 0, { fromKey: "support", toKey: "safety", execId: "e4" }),
    msg("log:r2", 1, { fromKey: "safety", toKey: "support", text: "거절했어요", tone: "fail", execId: "e4" }),
  ];
  assert.deepEqual(foldSafety(rejected).map((x) => x.id), ["log:r1", "log:r2"]);
  // execution_id 없는 safety 보류 줄도 그대로
  const lone = [msg("log:x", 0, { fromKey: "planning", toKey: "memory" }), msg("log:y", 1, { fromKey: "safety", toKey: "planning", text: "보류했어요", tone: "warn" })];
  assert.deepEqual(foldSafety(lone).map((x) => x.id), ["log:x", "log:y"]);
});
