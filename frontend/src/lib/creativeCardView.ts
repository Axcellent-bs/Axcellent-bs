// 대화방 이미지 시트·스토리보드 카드(components/office/CreativeChatCard.tsx)의 탭 한 칸 계산. node --test (creativeCardView.test.ts)
// 11/24 ②: 제작 기록에 Higgsfield 그림(render.sheet_image·board_image)이 done인데도 카드는 배치판만 보였다.
// 그림이 있으면 그 그림을 먼저(제작 기록 화면과 같은 주소 assetUrl), 없을 때만 배치판. 탭 옆에 상태 칩.
import type { RenderPart } from "./creativeRecords";

/** 제작 기록 render 오류 코드 → 사람 말 (제작 기록 화면 RenderPanel과 같은 말) */
export const RENDER_ERROR_KO: Record<string, string> = {
  higgsfield_key_missing: "Higgsfield 키가 서버에 없어요",
  model_not_configured: "Higgsfield 모델 경로가 서버에 설정되지 않았어요",
  quota_exceeded: "이번 달 사용량 예산을 넘었어요",
  quota_not_configured: "월 사용량 예산이 설정되지 않았어요",
  sheet_image_missing: "이미지 시트 그림이 없어 만들 수 없어요",
  interrupted: "서버가 다시 켜져 중단됐어요",
  // 회사 Higgsfield 계정 연결(구독 크레딧)로 만들 때
  credits_insufficient: "Higgsfield 계정의 크레딧이 모자라요. Higgsfield에서 크레딧을 채운 뒤 다시 해 주세요",
  higgsfield_reconnect_needed: "Higgsfield 계정 연결이 끊겼어요. SNS 계정 연결에서 다시 연결해 주세요",
  higgsfield_not_connected: "Higgsfield 계정 연결이 없어요(승인 기다리는 사이 끊겼을 수 있어요)",
  higgsfield_app_missing: "서버에 Higgsfield 연결 설정(CLIENT_ID)이 없어요",
};
export const renderErrorText = (code?: string | null) => (code ? RENDER_ERROR_KO[code] ?? code : null);

const MAKING = new Set(["running", "awaiting_approval", "uploading"]);
const FAILED = new Set(["failed", "blocked", "rejected", "unavailable"]);

export type TabChip = { label: string; tone: "ok" | "run" | "fail" };
export interface RenderTab {
  /** image: 그림(asset) 표시 · board: 지금 배치판 */
  show: "image" | "board";
  /** 그림 주소 버전(sha256). 없으면 null */
  version: string | null;
  chip: TabChip | null;
}

/** 그림 칸 하나: done이고 저장된 그림(asset·sha256)이 있으면 그림, 아니면 배치판 + 만드는 중·실패(사유) 칩 */
export function renderTab(part: RenderPart | null | undefined): RenderTab {
  const s = part?.status ?? "";
  if (s === "done" && (part?.asset || part?.sha256)) return { show: "image", version: part?.sha256 ?? null, chip: { label: "완료", tone: "ok" } };
  if (MAKING.has(s)) return { show: "board", version: null, chip: { label: "만드는 중", tone: "run" } };
  if (FAILED.has(s)) {
    const why = renderErrorText(part?.error);
    return { show: "board", version: null, chip: { label: why ? `실패 · ${why}` : "실패", tone: "fail" } };
  }
  return { show: "board", version: null, chip: null };
}

/** 카드가 기록을 다시 읽어야 하는지: 그림 칸이 만드는 중이면 (끝나면 그림으로 바뀐다) */
export const renderPending = (...parts: (RenderPart | null | undefined)[]) => parts.some((p) => MAKING.has(p?.status ?? ""));

/** [크게 보기]: 제작 기록 화면 */
export const recordLink = (recordId: string) => `/marketing/records?id=${encodeURIComponent(recordId)}`;
