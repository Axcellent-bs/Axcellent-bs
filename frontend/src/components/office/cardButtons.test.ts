// node --test (npm test). 대화 카드 안 동작 버튼 크기 통일 (11/23): RequestProgressCard·ChatCards의 버튼(btn-primary·btn-ghost)은 모두 btn-card를 같이 쓴다.
// 화면을 띄우지 않고 소스의 className을 읽어 본다. 글자 링크 버튼(text-brand-700 hover:underline)은 동작 버튼이 아니라서 보지 않는다
import assert from "node:assert/strict";
import { readFileSync } from "node:fs";
import { test } from "node:test";

const FILES = ["RequestProgressCard.tsx", "ChatCards.tsx"];
const read = (f: string) => readFileSync(new URL(`./${f}`, import.meta.url), "utf8");

/** 파일 안 <button …> 태그들의 className 글자 */
function buttonClasses(src: string): string[] {
  const out: string[] = [];
  for (const m of src.matchAll(/<button\b[^>]*?className=(?:"([^"]*)"|\{`([^`]*)`\})/g)) out.push(m[1] ?? m[2]);
  return out;
}

test("카드 안 동작 버튼은 모두 btn-card (주 동작 btn-primary · 보조 btn-ghost)", () => {
  let actions = 0;
  for (const f of FILES) {
    const classes = buttonClasses(read(f)).filter((c) => /\bbtn-(primary|ghost)\b/.test(c));
    for (const c of classes) assert.match(c, /\bbtn-card\b/, `${f}: "${c}"`);
    actions += classes.length;
  }
  // 보고서 열기·PDF로 저장(완료 카드) · 분석 의뢰 넣기·고치기·아니에요 · 기획서 열기·PDF로 저장 · 영상 열기 · 기획 제안 열기 · 진행·멈추기
  assert.equal(actions, 11);
});

test("예전 작은 버튼 크기(px-2.5 py-1 text-xs)가 카드에 남지 않는다", () => {
  for (const f of FILES) {
    for (const c of buttonClasses(read(f))) assert.doesNotMatch(c, /\bpx-2\.5 py-1 text-xs\b/, `${f}: "${c}"`);
  }
});

test("열기·진행은 btn-primary, PDF·멈추기는 btn-ghost", () => {
  // 버튼 글자: className 뒤 가장 가까운 ">글자</button>" (onClick의 => 를 건너뛰도록 글자에 <>{}가 없을 때만)
  //  (다른 <button>으로 넘어가지 않게, 동작 버튼(btn-*)만)
  const labeled = (src: string) => [...src.matchAll(/<button\b(?:(?!<button)[^])*?className="([^"]*)"(?:(?!<button)[^])*?>([^<>{}]*)<\/button>/g)]
    .map((m) => ({ cls: m[1], label: m[2].trim() }));
  const all = FILES.flatMap((f) => labeled(read(f))).filter((b) => /\bbtn-/.test(b.cls));
  const of = (label: RegExp) => all.filter((b) => label.test(b.label));
  const opens = of(/^(보고서 열기|기획서 열기|영상 열기|기획 제안 열기)$/);
  const pdfs = of(/^PDF로 저장$/);
  assert.equal(opens.length, 4);
  assert.equal(pdfs.length, 2);
  for (const b of opens) assert.match(b.cls, /\bbtn-primary\b/, b.label);
  for (const b of pdfs) assert.match(b.cls, /\bbtn-ghost\b/, b.label);
  // 진행·멈추기: 글자가 서버 값({s.decide.approve})이라 줄로 본다
  const src = read("RequestProgressCard.tsx");
  assert.match(src, /className="btn-primary btn-card"[^\n]*s\.decide\.approve/);
  assert.match(src, /className="btn-ghost btn-card"[^\n]*s\.decide\.stop/);
});
