// Higgsfield 웹 구독(Ultra)으로 영상을 만들 때 쓰는 순수 함수 (2026-11-23).
// API 키 없이 사람이 Higgsfield 화면에서 만든다: 영상 프롬프트를 복사하고 새 탭으로 Higgsfield를 연 뒤,
// 스토리보드 그림을 시작 이미지로 올려 만든다. 만든 영상은 제작 기록의 "스토리보드 영상"·"완성 영상" 칸에 올린다.
// 칸에 올리는 파일 검사(형식·크기)도 여기 둔다. 서버(dashboard-api `creative_upload`)가 같은 상한과
// 앞 바이트로 다시 확인하므로 여기는 미리 알려 주는 검사다.

import type { RenderPartKey } from "./creativeRecords";

export const HIGGSFIELD_URL = "https://higgsfield.ai/";

/**
 * 스토리보드 영상 프롬프트. 마케팅 `creative_render.storyboard_video_prompt`(영상 만들기 요청이
 * Higgsfield API에 보내는 문장)와 같다. 바꾸면 둘 다 바꾼다(양쪽 테스트가 같은 문장을 본다)
 */
export function storyboardVideoPrompt(styleEn: string | null | undefined): string {
  return (
    "Animate this storyboard as one continuous short film, panel by panel in order, "
    + "keeping the same characters, outfits and art style. "
    + (styleEn ?? "")
  ).trim();
}

const MB = 1024 * 1024;
/** dashboard-api `UPLOAD_MAX_BYTES`와 같은 상한 */
export const UPLOAD_MAX_BYTES = { image: 20 * MB, video: 200 * MB } as const;

const VIDEO_PARTS: ReadonlySet<RenderPartKey> = new Set<RenderPartKey>(["video", "final_video"]);
export const isVideoPart = (part: RenderPartKey) => VIDEO_PARTS.has(part);

/** `<input accept>`에 쓰는 값. 검사 규칙과 같은 형식이다 */
export const acceptFor = (part: RenderPartKey) => (isVideoPart(part) ? "video/mp4" : "image/jpeg,image/png");

const TYPES = { video: ["video/mp4"], image: ["image/jpeg", "image/png"] } as const;
const EXTS = { video: [".mp4"], image: [".jpg", ".jpeg", ".png"] } as const;

export interface FileLike { name: string; type: string; size: number }

/** 칸에 올릴 수 없는 이유(한국어). 올릴 수 있으면 null. 파일 고르기와 끌어 놓기가 같이 쓴다 */
export function uploadProblem(part: RenderPartKey, file: FileLike): string | null {
  const kind = isVideoPart(part) ? "video" : "image";
  const type = file.type.toLowerCase();
  const name = file.name.toLowerCase();
  // 브라우저가 형식을 모르면(type 빈칸) 확장자로 본다
  const typeOk = type ? (TYPES[kind] as readonly string[]).includes(type) : EXTS[kind].some((e) => name.endsWith(e));
  if (!typeOk) return kind === "video" ? "MP4 영상만 올릴 수 있어요" : "JPG·PNG 그림만 올릴 수 있어요";
  if (file.size <= 0) return "빈 파일이에요";
  const limit = UPLOAD_MAX_BYTES[kind];
  if (file.size > limit) return `파일이 너무 커요 (최대 ${limit / MB}MB)`;
  return null;
}

/** 끌어 놓은 파일 목록에서 올릴 한 개. 여러 개면 올리지 않고 이유를 준다 */
export function pickDropped(files: readonly FileLike[]): { file: FileLike | null; problem: string | null } {
  if (files.length === 0) return { file: null, problem: "파일이 아니에요" };
  if (files.length > 1) return { file: null, problem: "한 번에 파일 하나만 올릴 수 있어요" };
  return { file: files[0], problem: null };
}
