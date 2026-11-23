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
export function storyboardVideoPrompt(
  styleEn: string | null | undefined,
  shots?: readonly ScriptShot[] | null,
  cta?: string | null,
): string {
  const base = (
    "Animate this storyboard as one continuous short film, panel by panel in order, "
    + "keeping the same characters, outfits and art style. "
    + (styleEn ?? "")
  ).trim().slice(0, VIDEO_PROMPT_MAX);
  const flow = scriptFlow(shots, cta, VIDEO_PROMPT_MAX - base.length - 1);
  return flow ? `${base} ${flow}`.slice(0, VIDEO_PROMPT_MAX) : base;
}

/** 키 길·MCP 길이 영상 프롬프트를 자르는 길이(마케팅 `creative_render.VIDEO_PROMPT_MAX`) */
export const VIDEO_PROMPT_MAX = 2000;
const SCRIPT_HEAD = "Follow this script panel by panel:";

/** 대본을 따라 만든 스토리보드 칸(시간·대사·자막이 있다) */
export interface ScriptShot {
  no: number;
  scene_ko?: string | null;
  start_s?: number | null;
  end_s?: number | null;
  speaker_ko?: string | null;
  line_ko?: string | null;
  caption_ko?: string | null;
}

const one = (t: unknown) => String(t ?? "").split(/\s+/).filter(Boolean).join(" ");

/** 초 표기(마케팅 `_secs`와 같게): 소수 한 자리 반올림, 정수면 소수점 없이 */
export const secs = (x: number | null | undefined) => String(Math.floor(Number(x ?? 0) * 10 + 0.5) / 10);

function sceneLine(s: ScriptShot): string {
  let line = `Scene ${s.no} (${secs(s.start_s)}-${secs(s.end_s)}s): ${one(s.scene_ko)}`;
  const said = one(s.line_ko);
  if (said) {
    const who = one(s.speaker_ko);
    line += ` / ${who ? `${who}: ` : "narration: "}"${said}"`;
  }
  const caption = one(s.caption_ko);
  if (caption) line += ` / on-screen text "${caption}"`;
  return `${line}.`;
}

/** 장면 흐름·대사 요지·자막. 장면마다 같은 몫까지만 쓰고 끝 CTA는 늘 남긴다(마케팅 `script_flow`) */
export function scriptFlow(shots: readonly ScriptShot[] | null | undefined, cta: string | null | undefined, room: number): string {
  const rows = (shots ?? []).filter((s) => s.start_s !== null && s.start_s !== undefined);
  if (rows.length === 0) return "";
  const tail = one(cta) ? ` End with the call to action "${one(cta).slice(0, 100)}".` : "";
  const share = Math.max(0, Math.floor((room - SCRIPT_HEAD.length - tail.length) / rows.length) - 1);
  return `${SCRIPT_HEAD} ${rows.map((s) => sceneLine(s).slice(0, share)).join(" ")}${tail}`;
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
