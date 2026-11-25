// 제작 기록의 [영상 만들기 요청]을 띄울지 (11/25).
// 스토리보드 그림이 끝나면 띄운다. AI 가상 인물·그림 캐릭터는 올린 원본을 영상 참조로 쓰므로
// (마케팅 `board_needed`) 스토리보드 그림 없이도 띄운다. 정말 그림이 필요한 경우(모델·경로)는
// 서버가 board_image_not_ready로 거절하고 화면이 그 이유를 보여 준다.

export type SheetKind = "real_photo" | "virtual_human" | "illustration";

const BUSY = ["awaiting_approval", "running", "done"];

export const usesOriginalReference = (kind: SheetKind | null | undefined): boolean =>
  kind === "virtual_human" || kind === "illustration";

export function canAskVideo(boardStatus: string | null | undefined, videoStatus: string | null | undefined, kind: SheetKind | null | undefined): boolean {
  if (BUSY.includes(videoStatus ?? "")) return false;
  return boardStatus === "done" || usesOriginalReference(kind);
}
