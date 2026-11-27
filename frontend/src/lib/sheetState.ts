// 캐릭터 시트 칸 상태 (11/26 A, 마케팅 creative_render.sheet_ready·original_part와 같은 규칙).
// 가상 인물·일러스트는 올린 원본(render.original_image, 예전 기록은 시트 칸 자리의 원본 복사본)과
// 만든 캐릭터 시트(render.sheet_image, Higgsfield)를 따로 보여 준다. node --test (sheetState.test.ts)

export const SHEET_CREDITS = "약 1.5 크레딧";
export const MAKE_SHEET = `캐릭터 시트 만들기 · ${SHEET_CREDITS}`;
export const REMAKE_SHEET = `시트 다시 만들기 · ${SHEET_CREDITS}`;
export const SHEET_MISSING_NOTE = "회사 캐릭터에 시트를 먼저 만들면 스토리보드가 정확해져요";
const ORIGINAL_SOURCE = "upload_original";
const REFERENCE_KINDS = new Set(["virtual_human", "illustration"]);

export interface SheetPartLike {
  status?: string;
  source?: string;
  original?: boolean;
  sheet_missing?: boolean;
  copied_from?: string | null;
  /** 승인 관문을 지나 우리가 그린 그림이면 있다 (제작 기록 상세 render 칸에 그대로 온다) */
  execution_id?: string | null;
}
export interface SheetRenderLike {
  sheet_image?: SheetPartLike | null;
  original_image?: SheetPartLike | null;
}
export interface SheetState {
  /** 시트 칸 제목: 만든 시트가 있으면 "캐릭터 시트", 원본만 있으면 "원본 사진 (시트 아직 없음)" */
  title: string;
  /** 만든 캐릭터 시트 그림이 있다 */
  hasSheet: boolean;
  /** 올린 원본이 있는 칸 (옆에 작게 "원본"), 없으면 null */
  originalPart: "original_image" | "sheet_image" | null;
  /** [캐릭터 시트 만들기]·[시트 다시 만들기] (가상 인물·일러스트 캐릭터 기록만), 아니면 null */
  button: string | null;
}

const done = (p?: SheetPartLike | null) => p?.status === "done";

/** 예전 기록: 올린 원본이 시트 칸 자리에 있다 (시트가 아니다).
 *  11/27: 우리가 그린 그림(execution_id가 있음)은 원본 표시가 남아 있어도 시트 (마케팅 legacy_original과 같은 규칙) */
export const legacyOriginal = (render: SheetRenderLike) =>
  done(render.sheet_image) && !render.sheet_image?.execution_id
  && (render.sheet_image?.source === ORIGINAL_SOURCE || render.sheet_image?.original === true);

/** 기록 render로 시트 칸 상태. withButton: 캐릭터 기록(스토리보드 없는 회사 캐릭터·시트 단계)에서만 버튼 */
export function sheetState(render: SheetRenderLike, kind: string | null | undefined, withButton = true): SheetState {
  const legacy = legacyOriginal(render);
  const hasSheet = done(render.sheet_image) && !legacy;
  const originalPart = done(render.original_image) ? "original_image" : legacy ? "sheet_image" : null;
  const reference = REFERENCE_KINDS.has(kind ?? "");
  const title = hasSheet ? "캐릭터 시트" : originalPart ? "원본 사진 (시트 아직 없음)" : "캐릭터 시트";
  const button = withButton && reference && (originalPart || hasSheet) ? (hasSheet ? REMAKE_SHEET : MAKE_SHEET) : null;
  return { title, hasSheet, originalPart, button };
}

/** 회사 캐릭터 카드(대표 캐릭터 목록 preview_part)로 시트 상태 */
export function brandSheetState(previewPart: string | null | undefined, kind: string | null | undefined): SheetState {
  const hasSheet = previewPart === "sheet_image";
  const originalPart = previewPart === "original_image" ? "original_image" : null;
  const reference = REFERENCE_KINDS.has(kind ?? "");
  return {
    title: hasSheet ? "캐릭터 시트" : originalPart ? "원본 사진 (시트 아직 없음)" : "캐릭터 시트",
    hasSheet,
    originalPart,
    button: reference && (hasSheet || originalPart) ? (hasSheet ? REMAKE_SHEET : MAKE_SHEET) : null,
  };
}

const KIND_SHORT: Record<string, string> = {
  virtual_human: "AI 가상 인물",
  illustration: "일러스트 캐릭터",
  real_photo: "사진으로 만든 캐릭터",
};

export interface BrandCardView {
  /** 썸네일: 만든 캐릭터 시트가 있으면 시트 그림(sheet_image, 16:9 contain), 없으면 원본 작은 그림(thumbnail), 둘 다 없으면 null */
  thumb: "sheet_image" | "thumbnail" | null;
  /** 이름 밑 작은 글 "AI 가상 인물 · 참조 3장 · 캐릭터 시트 ✓ · CR-…" */
  info: string;
  /** 아래 버튼 줄의 시트 버튼 글 ([시트 만들기 / 다시 만들기 · 약 1.5 크레딧]), 없으면 null */
  button: string | null;
}

/** 11/27 의뢰 1단계 회사 캐릭터 카드 (운영 CR-05c62f5944f0: 시트 버튼이 줄 안에서 폭을 차지해 글이 세로로 쪼개지고,
 *  썸네일은 원본 사진이었다). 시트 판정은 brandSheetState(preview_part, #1968과 같은 규칙) */
export function brandCardView(c: {
  record_id: string;
  kind?: string | null;
  preview_part?: string | null;
  references?: readonly unknown[] | null;
  thumbnail_base64?: string | null;
}): BrandCardView {
  const s = brandSheetState(c.preview_part, c.kind);
  const thumb = s.hasSheet ? "sheet_image" : c.thumbnail_base64 ? "thumbnail" : null;
  const refs = c.references?.length ? `참조 ${c.references.length}장` : "";
  const sheet = s.hasSheet ? "캐릭터 시트 ✓" : s.originalPart ? "시트 아직 없음" : "";
  const info = [KIND_SHORT[c.kind ?? ""] ?? "", refs, sheet, c.record_id].filter(Boolean).join(" · ");
  return { thumb, info, button: s.button };
}

// ---- 11/27 등장 캐릭터 여럿: 제작 기록 1단계 캐릭터별 칸 (마케팅 render.cast · cast_1 · cast_2)
export interface CastSlot {
  part: "cast_1" | "cast_2";
  role: string;
  /** 그 회사 캐릭터 기록 번호 ([시트 만들기]는 그 기록에) */
  characterId: string | null;
  picture: "sheet" | "original" | null;
  title: string;
  /** 시트가 없으면 [캐릭터 시트 만들기 · 약 1.5 크레딧] */
  button: string | null;
  done: boolean;
}
type CastRender = Record<string, unknown> & { cast?: { members?: { record_id?: string; role_ko?: string }[] } | null };

/** 주인공 역할 (여럿일 때만, 아니면 null) */
export function castLeadRole(render: CastRender): string | null {
  const members = render.cast?.members ?? [];
  return members.length >= 2 ? members[0]?.role_ko || "주인공" : null;
}

export function castSlots(render: CastRender): CastSlot[] {
  if ((render.cast?.members ?? []).length < 2) return [];
  return (["cast_1", "cast_2"] as const).flatMap((part) => {
    const p = render[part] as { status?: string; role_ko?: string; character_id?: string; picture?: string } | undefined;
    if (!p) return [];
    const picture = p.picture === "sheet" || p.picture === "original" ? p.picture : null;
    const done = p.status === "done";
    const role = p.role_ko || "캐릭터";
    const title = !done ? `${role} · 그림 없음` : picture === "sheet" ? `${role} · 캐릭터 시트` : `${role} · 원본 사진 (시트 아직 없음)`;
    return [{ part, role, characterId: p.character_id ?? null, picture, title, button: done && picture === "original" ? MAKE_SHEET : null, done }];
  });
}

/** 의뢰 기록: 회사 캐릭터에 시트가 없어 원본만 복사해 왔으면 그 회사 캐릭터 기록 번호, 아니면 null */
export function sheetMissingBrand(render: SheetRenderLike): string | null {
  const sheet = render.sheet_image;
  return sheet?.sheet_missing === true && typeof sheet.copied_from === "string" ? sheet.copied_from : null;
}
