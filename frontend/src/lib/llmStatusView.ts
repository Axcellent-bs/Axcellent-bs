// data LLM 상태 줄(InsightBrief LlmStatusLine)의 순수 계산. node --test (llmStatusView.test.ts).
import type { LlmStatus } from "./data.ts";

/** 회사 AI 키를 쓰면 "회사 키" 칩 하나를 더한다 (여러 회사 6+). 서버 키·키 없음·옛 응답은 없음 */
export const llmKeyChip = (s: Pick<LlmStatus, "key_kind"> | null | undefined): string | null =>
  s?.key_kind === "company" ? "회사 키" : null;
