// node --test로 돈다 (npm test). 대화방 이미지 시트·스토리보드 카드의 탭 한 칸 (11/24 ②, CR-12cfb3f910d4)
import assert from "node:assert/strict";
import { test } from "node:test";
import { recordLink, renderPending, renderTab } from "./creativeCardView.ts";

test("그림 있음(done + asset) → 그림 표시, 완료 칩, 주소 버전은 sha256", () => {
  const tab = renderTab({ status: "done", asset: "sheet_image.png", sha256: "abc123def4567890" });
  assert.equal(tab.show, "image");
  assert.equal(tab.version, "abc123def4567890");
  assert.deepEqual(tab.chip, { label: "완료", tone: "ok" });
  // 사람이 올린 그림(sha256만)도 그림
  assert.equal(renderTab({ status: "done", sha256: "ff00", source: "upload" }).show, "image");
});

test("그림 없음 → 배치판 (상태 없음은 칩 없음, done인데 저장된 그림이 없으면 배치판)", () => {
  assert.deepEqual(renderTab(undefined), { show: "board", version: null, chip: null });
  assert.deepEqual(renderTab({}), { show: "board", version: null, chip: null });
  assert.equal(renderTab({ status: "done" }).show, "board");
});

test("만드는 중 → 배치판 + 만드는 중 칩, 기록을 다시 읽는다", () => {
  for (const status of ["running", "awaiting_approval", "uploading"]) {
    const tab = renderTab({ status });
    assert.equal(tab.show, "board", status);
    assert.deepEqual(tab.chip, { label: "만드는 중", tone: "run" }, status);
  }
  assert.equal(renderPending({ status: "done", asset: "a" }, { status: "running" }), true);
  assert.equal(renderPending({ status: "done", asset: "a" }, undefined), false);
});

test("실패 → 배치판 + 사유 칩 (아는 코드는 사람 말, 모르는 코드는 그대로, 코드 없으면 실패만)", () => {
  assert.deepEqual(renderTab({ status: "failed", error: "credits_insufficient" }).chip,
    { label: "실패 · Higgsfield 계정의 크레딧이 모자라요. Higgsfield에서 크레딧을 채운 뒤 다시 해 주세요", tone: "fail" });
  assert.deepEqual(renderTab({ status: "failed", error: "new_code" }).chip, { label: "실패 · new_code", tone: "fail" });
  assert.deepEqual(renderTab({ status: "blocked" }).chip, { label: "실패", tone: "fail" });
  assert.equal(renderTab({ status: "failed", error: "interrupted" }).show, "board");
});

test("[크게 보기]는 제작 기록 화면", () => {
  assert.equal(recordLink("CR-12cfb3f910d4"), "/marketing/records?id=CR-12cfb3f910d4");
});
