// node --test로 돈다 (npm test). 방 목록 ⋯ 메뉴 위치·고객 의뢰 넘김 sidebar 모양 (11/23).
import assert from "node:assert/strict";
import { test } from "node:test";
import { EDGE, MENU_GAP, MENU_WIDTH, menuHeight, menuPlace } from "./menuPlace.ts";
import { HANDOFF_GRID, stepsOpenByDefault, stepsSummary } from "./requestHandoffs.ts";

const VIEW = { width: 1280, height: 800 };

test("아래 공간이 넉넉하면 ⋯ 버튼 바로 아래, 오른쪽 끝을 버튼에 맞춰 연다", () => {
  const p = menuPlace({ top: 100, bottom: 120, right: 300 }, VIEW, 150);
  assert.deepEqual(p, { top: 120 + MENU_GAP, left: 300 - MENU_WIDTH, up: false });
});

test("아래 공간이 메뉴 높이보다 작으면 위로 연다 (방 목록 맨 아래 줄)", () => {
  const p = menuPlace({ top: 700, bottom: 720, right: 300 }, VIEW, 150);
  assert.equal(p.up, true);
  assert.equal(p.top, 700 - MENU_GAP - 150);
  // 딱 맞으면 아래로: 아래 공간 = 800 - 620 - 4 - 8 = 168 ≥ 168
  assert.equal(menuPlace({ top: 600, bottom: 620, right: 300 }, VIEW, 168).up, false);
  assert.equal(menuPlace({ top: 600, bottom: 620, right: 300 }, VIEW, 169).up, true);
});

test("창 밖으로 나가지 않는다 (왼쪽 끝 버튼·위가 좁은 창)", () => {
  assert.equal(menuPlace({ top: 100, bottom: 120, right: 60 }, VIEW, 100).left, EDGE);
  assert.equal(menuPlace({ top: 100, bottom: 120, right: 2000 }, VIEW, 100).left, VIEW.width - MENU_WIDTH - EDGE);
  const tight = menuPlace({ top: 40, bottom: 60, right: 300 }, { width: 1280, height: 120 }, 200);
  assert.equal(tight.up, true);
  assert.equal(tight.top, EDGE);
});

test("메뉴 높이 어림값: 설명 줄이 있는 항목은 더 높다", () => {
  assert.equal(menuHeight([]), 8);
  assert.equal(menuHeight([{}, { sub: "내 브라우저에서만" }]), 8 + 30 + 44);
});

test("고객 의뢰 넘김: 대화방 왼쪽 칸(sidebar)은 단계 목록을 접고, AI 오피스는 그대로 펼친다", () => {
  assert.equal(stepsOpenByDefault("sidebar"), false);
  assert.equal(stepsOpenByDefault("office"), true);
  assert.equal(stepsSummary(4), "단계 4개 보기");
  // 열 수는 화면 너비(md:·lg:)가 아니라 담긴 칸 너비로 정한다
  assert.ok(HANDOFF_GRID.includes("auto-fill") && !/\b(md|lg):/.test(HANDOFF_GRID));
});
