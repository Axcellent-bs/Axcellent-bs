// node --test로 돈다 (npm test). 11/24 회사 캐릭터 올리기 종류 (시안 ①): 실제 사람 사진 · AI 가상 인물 · 그림·일러스트
import assert from "node:assert/strict";
import { test } from "node:test";
import {
  KIND_OPTIONS, VIRTUAL_CONFIRM_LABEL, VIRTUAL_NOTE, canUploadKind, isReferenceKind,
} from "./requestCharacter.ts";

test("세 종류가 시안 문구 그대로 있다", () => {
  assert.deepEqual(KIND_OPTIONS.map((o) => o.label), ["실제 사람 사진", "AI로 만든 가상 인물", "그림·일러스트 캐릭터"]);
  assert.equal(KIND_OPTIONS.find((o) => o.kind === "virtual_human")?.isNew, true);
  assert.equal(VIRTUAL_CONFIRM_LABEL, "실존 인물이 아닌 AI 가상 인물이고, 우리 회사가 쓸 권리가 있어요");
  assert.equal(VIRTUAL_NOTE, "영상·게시물에 \"AI 가상 인물\" 표시가 붙어요.");
});

test("가상 인물은 가상 인물 확인을 체크해야 올린다", () => {
  assert.equal(canUploadKind(true, "virtual_human", true, false), false); // 권리 체크만으로는 안 된다
  assert.equal(canUploadKind(true, "virtual_human", false, true), true);
  assert.equal(canUploadKind(true, "real_photo", true, false), true);
  assert.equal(canUploadKind(true, "illustration", false, true), false); // 일러스트는 권리 확인
  assert.equal(canUploadKind(false, "virtual_human", false, true), false);
  assert.equal(canUploadKind(true, "virtual_human", false, true, true), false); // 올리는 중
});

test("가상 인물·일러스트만 원본을 참조로 쓴다", () => {
  assert.equal(isReferenceKind("virtual_human"), true);
  assert.equal(isReferenceKind("illustration"), true);
  assert.equal(isReferenceKind("real_photo"), false);
});
