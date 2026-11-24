// node --test로 돈다 (npm test). 참조 여러 장: 칸 수·요청 본문·413 문구·"참조 N장" 칩.
import assert from "node:assert/strict";
import { test } from "node:test";
import { MAX_EXTRA, REF_SLOTS, TOO_BIG_LINE, buildExtras, referenceChip, sheetBody, slotsFor, uploadErrorMessage } from "./characterRefs.ts";

const img = (d: string) => ({ media_type: "image/jpeg", data_base64: d });

test("칸 수: 가상 인물·일러스트 4칸, 실제 사람 사진 1칸", () => {
  assert.equal(slotsFor("virtual_human").length, 4);
  assert.equal(slotsFor("illustration").length, 4);
  assert.equal(slotsFor("real_photo").length, 1);
  assert.deepEqual(REF_SLOTS.map((s) => s.key), ["front", "full", "side", "face"]);
});

test("추가 이미지는 최대 3장 (4칸 중 정면이 대표)", () => {
  assert.equal(MAX_EXTRA, 3);
  assert.equal(REF_SLOTS.length - 1, MAX_EXTRA);
  const all = buildExtras({ face: img("c"), side: img("b"), full: img("a") });
  assert.equal(all.length, 3);
  const body = sheetBody(img("m"), "", undefined, { kind: "virtual_human", extras: [...all, ...all] }) as { extra_images: unknown[] };
  assert.equal(body.extra_images.length, 3);
});

test("본문: extra_images는 칸 순서(전신·옆·표정)와 label대로, 채운 칸만", () => {
  const ex = buildExtras({ face: img("c"), full: img("a") });
  assert.deepEqual(ex, [
    { media_type: "image/jpeg", data_base64: "a", label: "full" },
    { media_type: "image/jpeg", data_base64: "c", label: "face" },
  ]);
  const body = sheetBody(img("m"), " ", "t", { kind: "illustration", extras: ex });
  assert.deepEqual(body.extra_images, ex);
  assert.equal(body.kind, "illustration");
});

test("추가 이미지가 없으면 지금 본문 그대로 (필드를 보내지 않는다)", () => {
  assert.deepEqual(buildExtras({}), []);
  const today = (kind?: "real_photo" | "virtual_human") => JSON.stringify({
    image: img("m"), note: undefined, thumbnail_base64: "t",
    ...(kind && kind !== "real_photo" ? { kind, virtual_confirmed: true } : {}),
  });
  assert.equal(JSON.stringify(sheetBody(img("m"), "", "t", { extras: [] })), today());
  assert.equal(JSON.stringify(sheetBody(img("m"), "", "t")), today());
  assert.equal(JSON.stringify(sheetBody(img("m"), "", "t", { kind: "real_photo", extras: [] })), today());
  assert.equal(JSON.stringify(sheetBody(img("m"), "", "t", { kind: "virtual_human", virtualConfirmed: true, extras: [] })), today("virtual_human"));
  assert.ok(!("extra_images" in sheetBody(img("m"), "", "t", { kind: "virtual_human", extras: [] })));
});

test("413은 장수 줄이기 안내, 422는 서버 문장", () => {
  assert.equal(uploadErrorMessage(413, undefined, "x"), "사진이 너무 커요. 장수를 줄여 주세요");
  assert.equal(TOO_BIG_LINE, "사진이 너무 커요. 장수를 줄여 주세요");
  assert.equal(uploadErrorMessage(422, "얼굴이 보이지 않아요", "x"), "얼굴이 보이지 않아요");
  assert.equal(uploadErrorMessage(500, undefined, "기본"), "기본");
});

test("칩: references 개수 그대로, 없으면 칩 없음", () => {
  const r = (i: number) => ({ index: i, label: "front", width: 1, height: 1 });
  assert.equal(referenceChip({ references: [r(0), r(1), r(2)] }), "참조 3장");
  assert.equal(referenceChip({ references: [r(0)] }), "참조 1장");
  assert.equal(referenceChip({ references: [] }), null);
  assert.equal(referenceChip({}), null);
  assert.equal(referenceChip({ references: null }), null);
});
