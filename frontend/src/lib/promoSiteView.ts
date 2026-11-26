// 홍보 사이트에 올리기(2026-11-26)의 순수 계산. node --test (promoSite.test.ts). 읽기·주소는 lib/promoSite.ts.

export type PromoState = "awaiting_approval" | "approved" | "published" | "rejected" | "failed" | "removed";

export interface PromoItem {
  item_id: string;
  status?: PromoState | string | null;
  title?: string | null;
  customer?: string | null;
  review_id?: string | null;
  error?: string | null;
  published_at?: string | null;
}

export interface PromoStatus {
  /** 이 회사가 AXcellent 홍보 사이트에 올릴 수 있나 */
  available: boolean;
  /** 서버에 공개 폴더가 설정됐나 */
  feed_ready: boolean;
  limit: number;
  /** 쓰고 있는 자리(승인 대기 포함, 회사 전체) */
  used: number;
  items: PromoItem[];
}

export const TITLE_MAX = 40;
export const CUSTOMER_MAX = 20;

const ERROR_KO: Record<string, string> = {
  company_not_allowed: "이 회사에서는 AXcellent 홍보 사이트에 올릴 수 없어요",
  rights_not_confirmed: "공개해도 되는 영상인지 확인해 주세요",
  title_required: "제목과 고객사 이름을 넣어 주세요",
  feed_not_configured: "서버에 공개 폴더가 아직 설정되지 않았어요",
  limit_reached: "이미 3개가 올라가 있어요. 하나를 내린 뒤 올려 주세요",
  not_found: "영상을 찾지 못했어요",
  video_not_ready: "끝난 영상이 없어요",
  label_failed: "영상에 AI 표시를 입히지 못했어요",
  media_dir_not_configured: "서버 미디어 폴더가 설정되지 않았어요",
  media_dir_mismatch: "서버 미디어 폴더 설정이 맞지 않아요",
  gate_held: "내리기가 사람 확인 대기에 걸렸어요. 안전 처리 로그를 확인해 주세요",
  gate_blocked: "안전 관문이 막았어요",
  gate_rejected: "사람이 거절했어요",
  gate_failed: "안전 관문이 처리하지 못했어요. 다시 올릴 수 있어요",
  feed_write_failed: "공개 폴더에 쓰지 못했어요",
  source_changed: "승인 뒤에 영상 파일이 바뀌어 올리지 않았어요. 다시 올려 주세요",
  video_too_large: "영상 파일이 너무 커서 올릴 수 없어요",
};

/** 오류 코드 → 사람 말. 모르는 값은 그대로 */
export const promoErrorKo = (code: string | null | undefined): string | null => (code ? ERROR_KO[code] ?? code : null);

/** 자리를 차지하는 상태(올렸거나 올리는 중) */
const HOLDING = new Set<string>(["awaiting_approval", "approved", "published"]);
export const isHolding = (item: PromoItem | null | undefined): boolean => HOLDING.has(item?.status ?? "");

export const promoItem = (status: PromoStatus | null | undefined, itemId: string): PromoItem | null =>
  status?.items.find((i) => i.item_id === itemId) ?? null;

/** 승인이나 폴더 쓰기를 기다리는 항목이 있으면 다시 읽어야 한다 */
export const promoPending = (status: PromoStatus | null | undefined): boolean =>
  !!status?.items.some((i) => i.status === "awaiting_approval" || i.status === "approved");

/** 올리기 칸을 막는 이유. 올릴 수 있으면 null */
export function publishBlock(status: PromoStatus): string | null {
  if (!status.feed_ready) return ERROR_KO.feed_not_configured;
  if (status.used >= status.limit) return `이미 ${status.limit}개가 올라가 있어요. 하나를 내린 뒤 올려 주세요`;
  return null;
}

/** 제목 칸의 처음 값: 한 줄로 줄이고 상한을 넘으면 자른다 */
export const defaultPromoTitle = (title: string): string => title.replace(/\s+/g, " ").trim().slice(0, TITLE_MAX).trim();

export type PromoChip = { text: string; tone: "ok" | "wait" };

/** 목록 줄의 칩: 이 작업의 영상 중 올라가 있는 것이 먼저, 없으면 대기 중인 것 */
export function promoChip(status: PromoStatus | null | undefined, itemIds: (string | null | undefined)[]): PromoChip | null {
  const mine = itemIds.flatMap((id) => (id ? [promoItem(status, id)] : []));
  if (mine.some((i) => i?.status === "published")) return { text: "홍보 중", tone: "ok" };
  if (mine.some((i) => i?.status === "awaiting_approval" || i?.status === "approved")) return { text: "홍보 승인 대기", tone: "wait" };
  return null;
}
