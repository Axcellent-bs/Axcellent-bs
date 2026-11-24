// 참조 이미지 여러 장 올리기 (순수 계산만: `npm test`에서 그대로 돈다). 필드 타입은 characterSheet.ts에 있다.
// 가상 인물·일러스트는 4칸(정면=대표 · 전신 · 옆 · 표정), 실제 사람 사진은 한 장.

import type { CharacterReference, ExtraImage, ExtraLabel } from "./characterSheet.ts";

type Kind = "real_photo" | "virtual_human" | "illustration";

export interface RefSlot { key: "front" | ExtraLabel; label: string }
export const REF_SLOTS: readonly RefSlot[] = [
  { key: "front", label: "정면 (대표)" },
  { key: "full", label: "전신" },
  { key: "side", label: "옆" },
  { key: "face", label: "표정" },
];
export const MAX_EXTRA = 3;
export const TOO_BIG_LINE = "사진이 너무 커요. 장수를 줄여 주세요";
export const MULTI_HINT = "여러 장 올리면 더 비슷하게 만들어요 (정면만 있어도 돼요)";

/** 올리기 칸 수: 가상 인물·일러스트 4칸, 실제 사람 사진 1칸 */
export const slotsFor = (kind: Kind): readonly RefSlot[] => (kind === "real_photo" ? REF_SLOTS.slice(0, 1) : REF_SLOTS);

/** 칸 순서(전신·옆·표정)대로 채워진 것만 서버로 보낼 추가 이미지로. 최대 3장 */
export function buildExtras(filled: Partial<Record<ExtraLabel, { media_type: string; data_base64: string }>>): ExtraImage[] {
  const out: ExtraImage[] = [];
  for (const s of REF_SLOTS.slice(1)) {
    const img = filled[s.key as ExtraLabel];
    if (img) out.push({ media_type: img.media_type, data_base64: img.data_base64, label: s.key as ExtraLabel });
  }
  return out.slice(0, MAX_EXTRA);
}

/** 요청 본문: 추가 이미지가 없으면 지금과 똑같다(필드를 보내지 않는다) */
export function sheetBody(
  image: { media_type: string; data_base64: string }, note: string, thumb: string | undefined,
  opts: { kind?: Kind; virtualConfirmed?: boolean; extras?: readonly ExtraImage[] } = {},
): Record<string, unknown> {
  return {
    image, note: note.trim() || undefined, thumbnail_base64: thumb || undefined,
    ...(opts.kind && opts.kind !== "real_photo" ? { kind: opts.kind, virtual_confirmed: !!opts.virtualConfirmed } : {}),
    ...(opts.extras && opts.extras.length > 0 ? { extra_images: opts.extras.slice(0, MAX_EXTRA) } : {}),
  };
}

/** 413이면 장수 줄이기 안내, 그 밖에는 서버 문장(422 등)을 그대로 */
export const uploadErrorMessage = (status: number, serverMessage: string | undefined, fallback: string) =>
  status === 413 ? TOO_BIG_LINE : (serverMessage || fallback);

/** 저장된 캐릭터의 "참조 N장" 칩. 참조 정보가 없으면 칩을 두지 않는다 (N = references 개수) */
export const referenceChip = (c: { references?: readonly CharacterReference[] | null }): string | null =>
  c.references && c.references.length > 0 ? `참조 ${c.references.length}장` : null;
