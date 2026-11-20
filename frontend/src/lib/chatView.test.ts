// node --test로 돈다 (npm test). 대화 칸: 지난 일 다시 재생 막기(markSeen) · 스크롤 밀림 막기 · 한 칸 최대 폭 (lib/chatView.ts)
import assert from "node:assert/strict";
import { test } from "node:test";
import { markSeen, messageWidth, restoreScroll, scrollAnchor, type RowBox } from "./chatView.ts";

const OPEN = Date.parse("2026-11-20T07:21:00Z"); // 16:21 KST에 화면을 엶
const iso = (ms: number) => new Date(ms).toISOString();

test("markSeen: 늦게 온 예전 줄 → 0, 연 뒤 생긴 줄 → now, 시각 없는 줄 → 0", () => {
  const m = new Map<string, number>();
  markSeen(m, [{ id: "a", at: iso(OPEN - 60_000) }], OPEN, OPEN + 1_000);
  const now = OPEN + 30_000;
  // 의뢰 목록이 늦게 와 거르기가 바뀌어 들어온 예전 줄 b, 공통 로그 창이 움직여 들어온 예전 줄 c, 연 뒤 생긴 줄 d
  markSeen(m, [{ id: "a", at: iso(OPEN - 60_000) }, { id: "b", at: iso(OPEN - 40 * 60_000) }, { id: "c", at: "", lastAt: iso(OPEN - 3 * 3_600_000) }, { id: "d", at: iso(OPEN + 20_000) }, { id: "e", at: "" }], OPEN, now);
  assert.equal(m.get("a"), 0);
  assert.equal(m.get("b"), 0);
  assert.equal(m.get("c"), 0);
  assert.equal(m.get("d"), now);
  assert.equal(m.get("e"), 0);
  // 연 시각 5초 안쪽은 지금 일로 본다 (첫 응답이 막 생긴 줄)
  markSeen(m, [{ id: "f", at: iso(OPEN - 4_000) }], OPEN, now);
  assert.equal(m.get("f"), now);
});

test("markSeen: 방을 바꾸면 그 방의 예전 줄은 0 (바꾼 시각이 기준)", () => {
  const m = new Map<string, number>();
  const switched = OPEN + 10 * 60_000;
  markSeen(m, [{ id: "inc:1", at: iso(OPEN + 2 * 60_000) }, { id: "inc:2", at: iso(switched + 3_000) }], switched, switched + 3_000);
  assert.equal(m.get("inc:1"), 0); // 화면을 연 뒤 생겼지만 그 방을 연 시각보다 예전
  assert.equal(m.get("inc:2"), switched + 3_000);
});

test("markSeen: 보류 승인 — 보던 중 승인되면 now, 처음 볼 때 이미 승인된 예전 보류는 0", () => {
  const m = new Map<string, number>();
  const hold = { id: "h1", kind: "hold", at: iso(OPEN - 600_000), state: "pending" };
  markSeen(m, [hold], OPEN, OPEN);
  markSeen(m, [{ ...hold, state: "approved" }], OPEN, OPEN + 5_000);
  assert.equal(m.get("h1"), 0);
  assert.equal(m.get("h1:approved"), OPEN + 5_000);
  const late = { id: "h2", kind: "hold", at: iso(OPEN - 600_000), state: "approved" };
  markSeen(m, [late], OPEN, OPEN + 6_000);
  assert.equal(m.get("h2:approved"), 0);
});

// 높이 배열 → 칸 안 위치
const layout = (rows: [string, number][]): RowBox[] => {
  let top = 16;
  return rows.map(([id, height]) => { const r = { id, top, height }; top += height + 10; return r; });
};

test("스크롤 기준: 위에 줄 3개가 끼어도 기준 줄의 화면 위치가 같다", () => {
  const before = layout([["m1", 40], ["m2", 60], ["m3", 40], ["m4", 80], ["m5", 40]]);
  const scrollTop = 130; // m3(top 136) 근처를 보고 있음 — m2(top 66, 끝 126)는 위로 지나감
  const a = scrollAnchor(before, scrollTop);
  assert.equal(a?.ids[0], "m3");
  const screenY = before[2].top - scrollTop;
  const after = layout([["n1", 50], ["n2", 30], ["n3", 70], ["m1", 40], ["m2", 60], ["m3", 40], ["m4", 80], ["m5", 40], ["m6", 40]]);
  const next = restoreScroll(a, after);
  assert.ok(next !== null);
  const m3 = after.find((r) => r.id === "m3") as RowBox;
  assert.equal(m3.top - next, screenY);
});

test("스크롤 기준: 기준 말풍선이 사라지면(앞 80줄만 보이는 창이 밀림) 다음 말풍선을 쓴다, 아무것도 없으면 null", () => {
  const before = layout([["m1", 40], ["m2", 40], ["m3", 40]]);
  const a = scrollAnchor(before, 60); // m2 기준
  assert.equal(a?.ids[0], "m2");
  const after = layout([["m3", 40], ["m4", 40]]);
  // m2(top 66, 화면 위치 6)가 사라짐 → 다음 m3(새 top 16)를 m2가 있던 화면 위치에: scrollTop 10
  assert.equal(a?.offset, 6);
  assert.equal(restoreScroll(a, after), 10);
  assert.equal(restoreScroll(a, layout([["x", 40]])), null);
  assert.equal(scrollAnchor([], 0), null);
  assert.equal(restoreScroll(null, after), null);
});

test("한 칸이면 대화 줄을 가운데 최대 880px로, 여러 칸이면 칸 폭 그대로", () => {
  assert.equal(messageWidth(1), "mx-auto w-full max-w-[880px]");
  assert.equal(messageWidth(2), "w-full");
  assert.equal(messageWidth(3), "w-full");
});
