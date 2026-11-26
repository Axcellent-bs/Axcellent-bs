// node --test (npm test). 제작 기록 상세 스토리보드 칸 (11/26): 이미지 시트 → 대본까지만 만든 기록에 스토리보드를 처음 만들 버튼.
// 화면을 띄우지 않고 소스 구조를 읽어 본다 (RecordDetail.tsx BoardStep · RecordScriptStart.tsx RecordBoardFromScript)
import assert from "node:assert/strict";
import { readFileSync } from "node:fs";
import { test } from "node:test";

const read = (f: string) => readFileSync(new URL(`./${f}`, import.meta.url), "utf8");
const detail = read("RecordDetail.tsx");
const start = read("RecordScriptStart.tsx");
const boardStep = detail.slice(detail.indexOf("function BoardStep"), detail.indexOf("function VideoStep"));

test("RecordBoardFromScript: 모드는 prop 하나(first), 처음 만들기는 btn-primary + 새 문구", () => {
  assert.match(start, /first = false/);
  const firstBlock = start.slice(start.indexOf("if (first) return"), start.indexOf("지금 스토리보드는 대본을 따르지 않아요"));
  assert.match(firstBlock, /대본 \{scenes\}장면으로 스토리보드를 만들 수 있어요 · AI 콘티 · 그림 크레딧 0/);
  assert.match(firstBlock, /className="btn-primary[^"]*"/);
  assert.match(firstBlock, /"스토리보드 만드는 중… \(1~3분\)" : "대본으로 스토리보드 만들기"/);
  assert.match(firstBlock, /role="alert"/);
});

test("RecordBoardFromScript: 다시 만들기 모드 문구·버튼은 그대로", () => {
  assert.match(start, /지금 스토리보드는 대본을 따르지 않아요\. 영상은 스토리보드 장면으로 만들어져요\./);
  assert.match(start, /className="btn-ghost ml-auto text-xs"[^\n]*"대본대로 스토리보드 다시 만들기"/);
});

test("RecordBoardFromScript: 두 모드 다 같은 startStoryboard 호출(story·shots=nearestCount(장면 수)·record_id)", () => {
  // 11/26: 기다리는 로직을 lib/storyboardWait.ts로 뺐다. 부르는 곳은 한 곳, 실제 길은 RECORD_BOARD_IO(start = startStoryboard)
  assert.equal(start.match(/startBoardAndWait\(/g)?.length, 1);
  assert.match(start, /startBoardAndWait\(\{ story, character_en: characterEn, shots: nearestCount\(scenes\), record_id: recordId \}, RECORD_BOARD_IO\)/);
  assert.match(readFileSync(new URL("../../lib/storyboardIo.ts", import.meta.url), "utf8"), /start: startStoryboard,/);
});

test("BoardStep: 대본만 있는 기록(boardMissing)은 스토리보드 칸 맨 위에 처음 만들기, 옛 안내·빈 그림 칸은 숨김", () => {
  assert.match(boardStep, /const missing = boardMissing\(rec\)/);
  const head = boardStep.indexOf(">스토리보드</h3>");
  const first = boardStep.indexOf("<RecordBoardFromScript first");
  assert.ok(head > 0 && first > head, "처음 만들기는 스토리보드 제목 바로 아래");
  assert.ok(first < boardStep.indexOf("<BoardPicture"), "그림 칸보다 위");
  assert.match(boardStep, /rec\.script && missing && \(\s*<RecordBoardFromScript first[^>]*story=\{rec\.story \?\? rec\.script\.title_ko\}/);
  // 옛 문구는 missing이 아닐 때만
  assert.match(boardStep, /\{missing \? null : !board \? <p[^>]*>이 기록에는 스토리보드가 없어요\. SNS·미디어에서 줄거리를 넣고/);
  // Higgsfield 그림 칸(BoardPicture)은 hidePicture면 숨김
  assert.match(boardStep, /const hidePicture = missing && !render\.board_image\?\.status/);
  const pictures = [...boardStep.matchAll(/\{([^{}]*)&& (?:<div[^>]*>|<Fold[^>]*>)<(?:div[^>]*><)?BoardPicture/g)];
  assert.equal(pictures.length, 2, "그림 칸 두 곳(바로 보임 · 직접 올리기 접힘)");
  for (const m of pictures) assert.match(m[1], /!hidePicture/);
});

test("BoardStep: 대본보다 오래된 스토리보드는 지금처럼 대본 칸에서 다시 만들기", () => {
  assert.match(boardStep, /rec\.script && boardBehindScript\(rec\) && \(\s*<RecordBoardFromScript recordId/);
});
