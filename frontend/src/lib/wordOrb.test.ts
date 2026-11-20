// node --test로 돈다 (npm test). 동그란 단어 구름 자리: 겹침 없음 · 원 안 · 가장 큰 말이 가운데 · 24개 상한 · 같은 입력 같은 결과.
import assert from "node:assert/strict";
import { test } from "node:test";
import { ORB_MAX, ORB_MAX_REM, ORB_MIN_REM, fontPxOf, orbLayout, textWidth, type OrbWord } from "./wordOrb.ts";

const SIZE = 440;
const words = (n: number): OrbWord[] =>
  Array.from({ length: n }, (_, i) => ({ text: i % 3 === 0 ? `키워드${i}` : i % 3 === 1 ? `word${i}` : `말${i}`, weight: n - i, level: i < 3 ? "rising" : i < 7 ? "warming" : "normal" }));

const box = (p: { x: number; y: number; w: number; h: number }) => ({ x0: p.x - p.w / 2, x1: p.x + p.w / 2, y0: p.y - p.h / 2, y1: p.y + p.h / 2 });

test("겹치는 말이 없고 모두 원(반지름 size/2 − 8) 안", () => {
  const out = orbLayout(words(30), SIZE);
  assert.ok(out.length > 5);
  const r = SIZE / 2 - 8;
  for (const p of out) {
    const b = box(p);
    for (const [x, y] of [[b.x0, b.y0], [b.x1, b.y0], [b.x0, b.y1], [b.x1, b.y1]]) {
      assert.ok((x - SIZE / 2) ** 2 + (y - SIZE / 2) ** 2 <= r * r + 1e-6, `${p.text} 원 밖`);
    }
  }
  for (let i = 0; i < out.length; i++) {
    for (let j = i + 1; j < out.length; j++) {
      const a = box(out[i]);
      const b = box(out[j]);
      const hit = a.x0 < b.x1 && b.x0 < a.x1 && a.y0 < b.y1 && b.y0 < a.y1;
      assert.equal(hit, false, `${out[i].text} · ${out[j].text} 겹침`);
    }
  }
});

test("가장 큰 말이 가운데, 가장 큰 글자 (2.9rem)", () => {
  const out = orbLayout([{ text: "작은말", weight: 1, level: "normal" }, { text: "수능일", weight: 3.4, level: "rising" }, { text: "번역", weight: 1.6, level: "warming" }], SIZE);
  assert.equal(out[0].text, "수능일");
  assert.equal(out[0].x, SIZE / 2);
  assert.equal(out[0].y, SIZE / 2);
  assert.equal(out[0].fontPx, ORB_MAX_REM * 16);
  assert.ok(out.every((p) => p.fontPx >= ORB_MIN_REM * 16 - 1e-9 && p.fontPx <= ORB_MAX_REM * 16 + 1e-9));
  assert.equal(out.find((p) => p.text === "번역")?.level, "warming");
});

test("최대 24개, 원에 못 들어가는 말은 뺀다, 같은 입력이면 같은 결과", () => {
  const many = orbLayout(words(60), 2000); // 넓은 원: 자리는 넉넉해도 24개까지만
  assert.equal(many.length, ORB_MAX);
  const tiny = orbLayout([{ text: "아주아주긴한글키워드입니다", weight: 5, level: "rising" }], 120);
  assert.equal(tiny.length, 0); // 원보다 넓은 말
  assert.deepEqual(orbLayout(words(20), SIZE), orbLayout(words(20), SIZE));
  assert.deepEqual(orbLayout([], SIZE), []);
});

test("글자 폭 추정: 한글 0.95 · 라틴·숫자 0.6, weight 0이면 가장 작은 글자", () => {
  assert.equal(textWidth("가나", 20), 38);
  assert.equal(textWidth("ab1", 10), 18);
  assert.equal(fontPxOf(0, 3), ORB_MIN_REM * 16);
  assert.equal(fontPxOf(3, 3), ORB_MAX_REM * 16);
});
