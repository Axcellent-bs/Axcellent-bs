// node --test (npm test). 스토리보드 뒤에서 만들기 + 기록에 붙을 때까지 기다리기 (11/26). 가짜 io로 돈다(실제 호출 없음)
import assert from "node:assert/strict";
import { readFileSync } from "node:fs";
import { test } from "node:test";
import { BOARD_TIMEOUT_KO, POLL_LIMIT_MS, POLL_MS, startBoardAndWait } from "./storyboardWait.ts";

type Rec = { storyboard?: { script_used?: boolean; title_ko?: string } | null; storyboard_at?: string | null };
const T0 = Date.parse("2026-11-26T03:00:00Z");
const BODY = { story: "들판", shots: 12 as const, record_id: "CR-aaaaaaaaaaaa" };

/** 가짜 시계: sleep이 시간을 흘린다 */
function fakeIo(records: (Rec | Error)[], jobs: { status: string; code?: string; message?: string }[] = []) {
  let t = T0;
  const calls: string[] = [];
  let r = 0;
  let j = 0;
  return {
    calls,
    io: {
      start: async (b: typeof BODY) => { calls.push(`start:${b.record_id}:${b.shots}`); },
      getRecord: async (id: string) => { calls.push(`get:${id}`); const x = records[Math.min(r++, records.length - 1)]; if (x instanceof Error) throw x; return x; },
      job: async (id: string) => { calls.push(`job:${id}`); return jobs[Math.min(j++, jobs.length - 1)] ?? { status: "running" }; },
      sleep: async (ms: number) => { t += ms; },
      now: () => t,
    },
  };
}

test("background로 시작하고, 새 스토리보드가 붙은 기록을 돌려준다", async () => {
  const old: Rec = { storyboard: null, storyboard_at: null };
  const arrived: Rec = { storyboard: { script_used: true, title_ko: "새 콘티" }, storyboard_at: new Date(T0 + 2 * POLL_MS).toISOString() };
  const { io, calls } = fakeIo([old, arrived]);
  const rec = await startBoardAndWait(BODY, io);
  assert.equal(rec.storyboard?.title_ko, "새 콘티");
  assert.equal(calls[0], "start:CR-aaaaaaaaaaaa:12");
  assert.equal(calls.filter((c) => c.startsWith("start")).length, 1);
});

test("시작 전에 붙어 있던 옛 스토리보드는 도착으로 보지 않는다", async () => {
  const before: Rec = { storyboard: { script_used: true }, storyboard_at: new Date(T0 - 60_000).toISOString() };
  const after: Rec = { storyboard: { script_used: true }, storyboard_at: new Date(T0 + POLL_MS).toISOString() };
  const { io, calls } = fakeIo([before, before, after]);
  await startBoardAndWait(BODY, io);
  assert.equal(calls.filter((c) => c.startsWith("get")).length, 3);
});

test("job이 실패하면 그 이유 문장으로 멈춘다", async () => {
  const { io } = fakeIo([{ storyboard: null }], [{ status: "running" }, { status: "failed", code: "llm_timeout", message: "마케팅 AI가 늦어요" }]);
  await assert.rejects(startBoardAndWait(BODY, io), { message: "스토리보드를 만들지 못했어요 — 마케팅 AI가 늦어요 (llm_timeout)" });
});

test("기록 읽기가 잠깐 실패해도 계속 기다리고, 5분이 넘으면 새로고침 안내", async () => {
  const { io, calls } = fakeIo([new Error("502"), { storyboard: null }]);
  await assert.rejects(startBoardAndWait(BODY, io), { message: BOARD_TIMEOUT_KO });
  assert.ok(calls.filter((c) => c.startsWith("get")).length >= POLL_LIMIT_MS / POLL_MS - 1);
});

test("시작 호출이 실패하면 그 오류를 그대로 (기다리지 않음)", async () => {
  const { io, calls } = fakeIo([{ storyboard: null }]);
  io.start = async () => { throw new Error("스토리보드를 만들 수 없어요 (400)"); };
  await assert.rejects(startBoardAndWait(BODY, io), { message: "스토리보드를 만들 수 없어요 (400)" });
  assert.equal(calls.length, 0);
});

test("SNS·미디어 시트 화면: 기록이 있으면 background로 요청하고 기록의 콘티를 보인다", () => {
  const src = readFileSync(new URL("../components/marketing/CharacterSheetMaker.tsx", import.meta.url), "utf8");
  const draw = src.slice(src.indexOf("const drawBoard"), src.indexOf("const make = async"));
  assert.match(draw, /if \(sheet\.record_id\) \{\s*const rec = await startBoardAndWait\(\{ \.\.\.body, record_id: sheet\.record_id \}, RECORD_BOARD_IO\);\s*setBoard\(rec\.storyboard \?\? null\);/);
  // 기다려서 받기는 붙일 기록이 없을 때만
  assert.match(draw, /\} else setBoard\(await makeStoryboard\(body\)\);/);
  // 버튼 글자·떠나도 된다는 한 줄·실패 이유(role=alert)
  assert.match(src, /step === "board" \? BOARD_BUSY_KO/);
  assert.match(src, /\{BOARD_LEAVE_KO\}/);
  assert.match(src, /\{error && <p[^>]*role="alert">\{error\}<\/p>\}/);
});
