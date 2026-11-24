// node --test (npm test). 브라우저 확인·알림 창을 쓰지 않는다 (11/24): 확인은 화면 안 상자(components/InlineConfirm),
// 알림은 그 자리 아래 한 줄. 화면을 띄우지 않고 src 아래 소스(.ts·.tsx, 테스트 파일 제외)를 읽어 본다.
import assert from "node:assert/strict";
import { readdirSync, readFileSync } from "node:fs";
import { join } from "node:path";
import { fileURLToPath } from "node:url";
import { test } from "node:test";

const SRC = fileURLToPath(new URL("../", import.meta.url));
const DIALOG = /\bwindow\s*\.\s*(confirm|alert|prompt)\b/g;

function sources(dir: string): string[] {
  const out: string[] = [];
  for (const e of readdirSync(dir, { withFileTypes: true })) {
    const p = join(dir, e.name);
    if (e.isDirectory()) out.push(...sources(p));
    else if (/\.tsx?$/.test(e.name) && !/\.test\.tsx?$/.test(e.name)) out.push(p);
  }
  return out;
}

test("frontend 소스에 window.confirm·window.alert·window.prompt가 한 곳도 없다", () => {
  const files = sources(SRC);
  assert.ok(files.length > 100, `소스를 못 찾았다: ${files.length}개`); // 폴더를 잘못 읽어 0곳으로 통과하지 않게
  const hits: string[] = [];
  for (const f of files) {
    readFileSync(f, "utf8").split("\n").forEach((line, i) => {
      if (line.match(DIALOG)) hits.push(`${f.slice(SRC.length)}:${i + 1}`);
    });
  }
  assert.deepEqual(hits, []);
});
