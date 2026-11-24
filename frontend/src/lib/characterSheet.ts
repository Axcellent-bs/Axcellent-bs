import { failureError } from "./apiError";
import { DASHBOARD_API_URL, dashboardAuth } from "./env";

// 마케팅 › SNS·미디어 › 캐릭터 시트 프롬프트. 올린 캐릭터 이미지 한 장으로 같은 캐릭터의
// 캐릭터 시트(방향 7칸·표정·포즈·색 팔레트) 이미지 프롬프트를 만든다. dashboard-api
// POST /marketing/character-sheet → 마케팅 `character_sheet_prompt`(read). 이미지는 저장하지 않는다.

export interface CharacterSheetPanel {
  kind: "view" | "expression" | "pose" | "palette" | "details";
  id: string;
  label_ko: string;
  prompt_en: string;
}

export interface PaletteColor {
  name: string;
  hex: string;
  use?: string | null;
}

export interface CharacterSheetResult {
  summary_ko: string;
  personality_ko?: string | null;
  seen_ko: string[];
  guessed_ko: string[];
  stylized_from_photo: boolean;
  note_applied?: string | null;
  appearance_en: string;
  outfit_en: string;
  proportions: string;
  palette: PaletteColor[];
  consistency_en: string;
  panels: CharacterSheetPanel[];
  layout_ko: string[];
  prompt_en: string;
  negative_prompt_en: string;
  record_id?: string | null; // 제작 기록 번호(CR-…)
}

/** 올리기 전에 긴 변을 이 크기로 줄인다(서버 한도 2MB, 앞단 요청 크기도 작게) */
export const MAX_SIDE = 1024;
export const ACCEPT = "image/png,image/jpeg,image/webp";
const PATH = "/api/dashboard/marketing/character-sheet";

/** 이미지 파일을 긴 변 MAX_SIDE 이하의 JPEG로 줄여 base64로 돌려준다(투명 배경은 흰색) */
/** 제작 기록에 남길 작은 미리보기(긴 변) */
export const THUMB_SIDE = 256;

/** base64 길이로 원래 바이트 수 (패딩 무시한 근사) */
export const base64Bytes = (b64: string) => Math.floor((b64.length * 3) / 4);

/** 가상 인물·일러스트: 긴 변 2048 → 1536 → 1024 순으로 줄여 2MB 이하인 첫 결과 */
export async function shrinkReference(file: File): Promise<Awaited<ReturnType<typeof shrinkImage>>> {
  let last: Awaited<ReturnType<typeof shrinkImage>> | null = null;
  for (const side of REFERENCE_SIDES) {
    last = await shrinkImage(file, side);
    if (base64Bytes(last.data_base64) <= MAX_IMAGE_BYTES) return last;
  }
  return last as Awaited<ReturnType<typeof shrinkImage>>;
}

export async function shrinkImage(file: File, maxSide: number = MAX_SIDE): Promise<{ media_type: "image/jpeg"; data_base64: string; preview: string; thumb_base64: string }> {
  if (!ACCEPT.split(",").includes(file.type)) throw new Error("PNG·JPEG·WebP 이미지만 올릴 수 있어요");
  const url = URL.createObjectURL(file);
  try {
    const img = await new Promise<HTMLImageElement>((resolve, reject) => {
      const el = new Image();
      el.onload = () => resolve(el);
      el.onerror = () => reject(new Error("이미지를 읽을 수 없어요"));
      el.src = url;
    });
    const scale = Math.min(1, maxSide / Math.max(img.naturalWidth, img.naturalHeight));
    const canvas = document.createElement("canvas");
    canvas.width = Math.max(1, Math.round(img.naturalWidth * scale));
    canvas.height = Math.max(1, Math.round(img.naturalHeight * scale));
    const ctx = canvas.getContext("2d");
    if (!ctx) throw new Error("이미지를 줄일 수 없어요");
    ctx.fillStyle = "#ffffff";
    ctx.fillRect(0, 0, canvas.width, canvas.height);
    ctx.drawImage(img, 0, 0, canvas.width, canvas.height);
    const preview = canvas.toDataURL("image/jpeg", 0.9);
    const t = Math.min(1, THUMB_SIDE / Math.max(canvas.width, canvas.height));
    const small = document.createElement("canvas");
    small.width = Math.max(1, Math.round(canvas.width * t));
    small.height = Math.max(1, Math.round(canvas.height * t));
    small.getContext("2d")?.drawImage(canvas, 0, 0, small.width, small.height);
    const thumb = small.toDataURL("image/jpeg", 0.8);
    return { media_type: "image/jpeg", data_base64: preview.slice(preview.indexOf(",") + 1), preview, thumb_base64: thumb.slice(thumb.indexOf(",") + 1) };
  } finally {
    URL.revokeObjectURL(url);
  }
}

/** 11/24 올린 이미지 종류: 실제 사람 사진(그림체로 바꿈) · AI 가상 인물(그대로 참조) · 그림·일러스트(그대로) */
export type UploadKind = "real_photo" | "virtual_human" | "illustration";
/** 가상 인물·일러스트는 원본을 참조로 쓰므로 긴 변 2048px까지 보낸다(2MB를 넘으면 더 줄인다) */
export const REFERENCE_SIDES = [2048, 1536, MAX_SIDE];
export const MAX_IMAGE_BYTES = 2 * 1024 * 1024;

export async function makeCharacterSheet(image: { media_type: string; data_base64: string }, note: string, thumb?: string,
  opts: { kind?: UploadKind; virtualConfirmed?: boolean } = {}): Promise<CharacterSheetResult> {
  const res = await fetch(`${DASHBOARD_API_URL}${PATH}`, {
    method: "POST",
    headers: { "Content-Type": "application/json", ...dashboardAuth() },
    body: JSON.stringify({
      image, note: note.trim() || undefined, thumbnail_base64: thumb || undefined,
      ...(opts.kind && opts.kind !== "real_photo" ? { kind: opts.kind, virtual_confirmed: !!opts.virtualConfirmed } : {}),
    }),
    cache: "no-store",
  });
  const parsed: unknown = await res.json().catch(() => undefined);
  if (!res.ok) throw failureError(parsed, res.status, "캐릭터 시트 프롬프트를 만들지 못했어요");
  return (parsed as { data: CharacterSheetResult }).data;
}
