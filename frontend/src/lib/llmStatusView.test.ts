// node --test로 돈다 (npm test). data LLM 상태 줄: 회사 키를 쓰면 "회사 키" 칩 (lib/llmStatusView.ts)
import assert from "node:assert/strict";
import { test } from "node:test";
import { llmKeyChip } from "./llmStatusView.ts";

test("key_kind가 company일 때만 '회사 키' 칩, 서버 키·키 없음·옛 응답(칸 없음)은 그대로", () => {
  assert.equal(llmKeyChip({ key_kind: "company" }), "회사 키");
  assert.equal(llmKeyChip({ key_kind: "server" }), null);
  assert.equal(llmKeyChip({ key_kind: "none" }), null);
  assert.equal(llmKeyChip({}), null);
  assert.equal(llmKeyChip(null), null);
});
