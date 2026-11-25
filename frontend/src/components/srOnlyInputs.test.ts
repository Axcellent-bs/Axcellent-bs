// node --test (npm test). 숨긴 입력(sr-only)은 가까운 부모에 relative가 있어야 한다 (11/25 운영 재현).
// sr-only는 position:absolute라서 relative 부모가 없으면 <main class="relative overflow-y-auto">를 기준으로 놓인다.
// 긴 대화 목록 아래(6156px)에 놓인 입력이 main에 빈 스크롤을 만들고, 파일을 고르면 포커스로 main이 빈 곳까지 내려간다.
// 화면을 띄우지 않고 소스를 읽어, 입력을 감싼 <label>의 className에 relative가 있는지 본다
import assert from "node:assert/strict";
import { readFileSync, readdirSync } from "node:fs";
import { test } from "node:test";

const ROOT = new URL("./", import.meta.url);
const read = (f: string) => readFileSync(new URL(f, ROOT), "utf8");

/** 파일 안 sr-only <input …/> 마다, 그 입력을 감싼 가장 가까운 <label>의 className 글자(없으면 null) */
function srOnlyInputs(src: string): { tag: string; label: string | null }[] {
  const out: { tag: string; label: string | null }[] = [];
  for (const m of src.matchAll(/<input\b[\s\S]*?\/>/g)) {
    const tag = m[0];
    if (!/className="[^"]*\bsr-only\b/.test(tag)) continue;
    const before = src.slice(0, m.index);
    const open = before.lastIndexOf("<label");
    const closed = open >= 0 && before.indexOf("</label>", open) >= 0;
    if (open < 0 || closed) { out.push({ tag, label: null }); continue; }
    const cls = before.slice(open).match(/className=(?:"([^"]*)"|\{`([^`]*)`\})/);
    out.push({ tag, label: cls ? (cls[1] ?? cls[2]) : "" });
  }
  return out;
}

const hasRelative = (cls: string | null) => cls !== null && /(^|\s)relative(\s|$)/.test(cls);

test("BrandCharacterAsk 대표·참조 이미지 입력은 relative 라벨 안에 갇힌다", () => {
  const inputs = srOnlyInputs(read("office/BrandCharacterAsk.tsx")).filter((i) => i.tag.includes('type="file"'));
  assert.equal(inputs.length, 2); // 대표 이미지 · 참조 칸
  for (const i of inputs) assert.ok(hasRelative(i.label), `라벨 className: "${i.label}"`);
  // 대표 이미지(첫 칸)의 가장 가까운 positioned 조상이 라벨이다
  assert.match(inputs[0].label ?? "", /^relative block cursor-pointer/);
});

test("components 안 sr-only 입력은 모두 relative 라벨 안에 있다", () => {
  const files = readdirSync(ROOT, { recursive: true }).map(String).filter((f) => f.endsWith(".tsx"));
  let n = 0;
  for (const f of files) {
    for (const i of srOnlyInputs(read(f.replaceAll("\\", "/")))) {
      n += 1;
      assert.ok(hasRelative(i.label), `${f}: ${i.tag.slice(0, 80)} → 라벨 "${i.label}"`);
    }
  }
  // 영수증 · .pem · 은행 거래내역 · PG 정산 · 대표 이미지 · 참조 칸 · MCP 부서 체크
  assert.ok(n >= 7, `sr-only 입력 ${n}개`);
});

test("대화 스크롤 박스는 relative라서 안의 숨긴 입력이 main 기준으로 빠지지 않는다", () => {
  assert.match(read("office/OfficeChat.tsx"), /ref=\{box\} onScroll=\{onScroll\} className="[^"]*\brelative\b[^"]*overflow-y-auto/);
  assert.match(read("office/DirectChat.tsx"), /ref=\{box\} className="[^"]*\brelative\b[^"]*overflow-y-auto/);
});
