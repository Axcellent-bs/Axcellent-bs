// 완성 영상에 자막·엔드카드를 얹은 상태 (render.overlay). 순수 로직만 둔다(화면·네트워크 없음).
// 서버가 영상을 만든 뒤 한글 자막과 끝 카드를 ffmpeg로 얹는다. 못 얹으면 원본 영상이 그대로 나온다.

export type OverlayStatus = "applied" | "skipped" | "failed" | "running";
export type OverlaySource = "video" | "final_video";

export interface OverlayState {
  status?: OverlayStatus | string;
  error?: string | null;
  captions?: number;
  source_part?: OverlaySource | string;
  /** 11/25 재생 속도(#1760): 글꼴이 없거나 얹을 글이 없어 자막을 건너뛰고 배속만 했을 때의 이유 */
  captions_skipped?: string | null;
  /** 배속을 했나 (applied일 때) */
  speed_applied?: boolean;
  /** 서버가 이 얹기에 쓴 배속 (화면은 영상 요약 speed를 먼저 쓴다) */
  speed?: number | null;
}

export type PlayPart = "video" | "final_video" | "overlay_video";

export const CAPTION_MAX = 60;

interface PartLike { status?: string }
export interface OverlayRender {
  video?: PartLike | null;
  final_video?: PartLike | null;
  overlay?: OverlayState | null;
}

/** 가장 나중 영상: 완성 영상이 있으면 그것, 없으면 스토리보드 영상 */
export function latestVideoPart(render: OverlayRender | null | undefined): "video" | "final_video" {
  return render?.final_video?.status === "done" ? "final_video" : "video";
}

/**
 * 재생할 파일. 자막을 얹은 결과(applied)이고 그 바탕이 지금 가장 나중 영상이면 overlay_video,
 * 아니면(필드 없음·건너뜀·실패·진행 중·바탕이 옛 영상) 예전처럼 가장 나중 영상.
 */
export function pickVideoPart(render: OverlayRender | null | undefined): PlayPart {
  const latest = latestVideoPart(render);
  const o = render?.overlay;
  if (o?.status === "applied" && o.source_part === latest) return "overlay_video";
  return latest;
}

/** 칸(part)에서 실제로 재생할 파일: 그 칸이 가장 나중 영상일 때만 자막 입힌 파일로 바뀐다. 원본 보기면 늘 칸 그대로 */
export function playPartFor(part: "video" | "final_video", render: OverlayRender | null | undefined, showOriginal = false): PlayPart {
  if (showOriginal || latestVideoPart(render) !== part) return part;
  return pickVideoPart(render);
}

/** 자막 상태 칩과 [원본 보기]·[다시 얹기] 버튼을 이 칸에 보여 줄지(필드가 없으면 아무것도 안 보인다) */
export function overlayVisibleFor(part: "video" | "final_video", render: OverlayRender | null | undefined): boolean {
  return !!render?.overlay?.status && latestVideoPart(render) === part;
}

/** `note`: 칩 옆에 붙이는 이유 문구(배속만 하고 자막을 건너뛰었을 때만) */
export interface OverlayChip { text: string; tone: "ok" | "busy" | "warn"; note?: string }

const REASON_KO: Record<string, string> = {
  font_not_found: "서버에 한글 글꼴이 없어 자막 없이 나왔어요",
  ffmpeg_not_found: "서버에 영상 도구가 없어 자막 없이 나왔어요",
};

/** 배속만 하고 자막을 건너뛴 이유 (#1760 `captions_skipped`) */
const SKIPPED_KO: Record<string, string> = {
  font_not_found: "서버에 한글 글꼴이 없어요",
  nothing_to_overlay: "얹을 자막이 없어요",
};

const speedText = (n: number) => String(Math.round(n * 10) / 10);

/** `speed`: 영상 요약(render.video.summary.speed). 없으면 얹기 기록의 speed */
export function overlayChip(overlay: OverlayState | null | undefined, speed?: number | null): OverlayChip | null {
  const s = overlay?.status;
  if (!s) return null;
  if (s === "applied") {
    const x = typeof speed === "number" && speed > 1 ? speed : typeof overlay?.speed === "number" && overlay.speed > 1 ? overlay.speed : null;
    const times = x !== null ? `${speedText(x)}배속` : "배속";
    if (overlay?.captions_skipped) {
      return { text: `${times}만 · 자막 없음`, tone: "warn", note: SKIPPED_KO[overlay.captions_skipped] ?? "자막을 얹지 못했어요" };
    }
    if (overlay?.speed_applied) return { text: `자막·엔드카드 얹음 · ${times}`, tone: "ok" };
    return { text: "자막·엔드카드 얹음", tone: "ok" };
  }
  if (s === "running") return { text: "자막 얹는 중", tone: "busy" };
  if (s === "skipped" || s === "failed") return { text: (overlay?.error && REASON_KO[overlay.error]) || "자막을 얹지 못했어요", tone: "warn" };
  return null;
}

/** [원본 보기]는 자막을 얹은 영상을 보고 있을 때만 쓴다 */
export function canToggleOriginal(render: OverlayRender | null | undefined): boolean {
  return pickVideoPart(render) === "overlay_video";
}

export interface CaptionShot { no: number; caption_ko?: string }

/** 고칠 입력칸의 처음 값: 장면마다 지금 자막(없으면 빈 칸) */
export function captionDrafts(shots: readonly CaptionShot[] | null | undefined): { no: number; caption_ko: string }[] {
  return (shots ?? []).map((s) => ({ no: s.no, caption_ko: (s.caption_ko ?? "").slice(0, CAPTION_MAX) }));
}

/** 다시 얹기 요청 본문: 글자 수 상한을 넘으면 자르고, 앞뒤 공백은 없앤다 */
export function reapplyBody(drafts: readonly { no: number; caption_ko: string }[]): { captions: { no: number; caption_ko: string }[] } {
  return { captions: drafts.map((d) => ({ no: d.no, caption_ko: d.caption_ko.trim().slice(0, CAPTION_MAX) })) };
}

/** 다시 얹기가 가능한 때: 자막 얹는 중이 아니고 고칠 장면이 있다 */
export function canReapply(overlay: OverlayState | null | undefined, shots: readonly CaptionShot[] | null | undefined): boolean {
  return !!overlay?.status && overlay.status !== "running" && !!shots?.length;
}
