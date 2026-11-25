import { storyboardFailure } from "./storyboardError";
import { DASHBOARD_API_URL, dashboardAuth } from "./env";
import type { PlanScene } from "./keyframes";

// 마케팅 › SNS·미디어 › 스토리보드. 줄거리와 이미지 시트의 캐릭터 고정 문장(consistency_en)으로
// 장면마다 샷 크기·카메라 앵글·화면 배치를 정한 스토리보드 칸과 이미지 프롬프트를 만든다.
// dashboard-api POST /marketing/storyboard → 마케팅 `storyboard_prompt`(read). 이미지는 만들지 않는다.

export interface StoryboardShot {
  no: number;
  scene_ko: string;
  shot_size: string; // extreme_wide·wide·full·medium·close_up·extreme_close_up·detail·over_shoulder·pov
  shot_size_ko: string;
  angle: string; // eye_level·low·high·birds_eye·ground·dutch
  angle_ko: string;
  composition_ko: string;
  has_character: boolean;
  prompt_en: string;
  /** 대본을 따라 만든 칸이면 그 장면의 시간·말하는 사람·대사·자막(11/23). 옛 기록은 없다 */
  start_s?: number | null;
  end_s?: number | null;
  speaker_ko?: string;
  line_ko?: string;
  caption_ko?: string;
  /** AI가 그린 SVG 콘티 글(board_source가 "svg"일 때). 없으면 구도 그림 칸. 늘 <img data URI>로만 보인다 */
  svg?: string | null;
}

export interface StoryboardResult {
  title_ko: string;
  logline_ko: string;
  style_en: string;
  character_used: boolean;
  columns: number;
  shots: StoryboardShot[];
  sheet_prompt_en: string;
  negative_prompt_en: string;
  record_id?: string | null; // 붙인 제작 기록 번호
  /** 기록의 대본 장면을 칸으로 썼는지, 대본의 끝 CTA(11/23) */
  script_used?: boolean;
  cta_ko?: string;
  /** 칸 그림의 출처. 없으면 "higgsfield"(지금 화면) */
  board_source?: "svg" | "higgsfield" | "layout";
  /** 11/25 의뢰 기록: 기획 장면의 키프레임 칸(동작·카메라 문장). 없으면 지금 화면 */
  plan_scenes?: PlanScene[] | null;
}

export const SHOT_COUNTS = [6, 9, 12] as const;
export type ShotCount = (typeof SHOT_COUNTS)[number];
export const MAX_STORY = 1000;

const PATH = "/api/dashboard/marketing/storyboard";

/** 11/25 뒤에서 만드는 스토리보드의 마지막 상태. 서버를 재시작하면 "none" */
export interface StoryboardJob { status: "none" | "running" | "done" | "failed"; code?: string; message?: string }

export async function storyboardJob(recordId: string): Promise<StoryboardJob> {
  const res = await fetch(`${DASHBOARD_API_URL}${PATH}/jobs/${encodeURIComponent(recordId)}`, { headers: dashboardAuth(), cache: "no-store" });
  const parsed: unknown = await res.json().catch(() => undefined);
  if (!res.ok) return { status: "none" };
  return (parsed as { data: StoryboardJob }).data;
}

/** 11/25 기록에 붙일 스토리보드를 뒤에서 만들게 한다(바로 답함). 끝나면 기록의 storyboard_at이 바뀐다 */
export async function startStoryboard(body: { story: string; character_en?: string; shots: ShotCount; record_id: string }): Promise<void> {
  const res = await fetch(`${DASHBOARD_API_URL}${PATH}`, {
    method: "POST",
    headers: { "Content-Type": "application/json", ...dashboardAuth() },
    body: JSON.stringify({ ...body, story: body.story.trim(), character_en: body.character_en?.trim() || undefined, background: true }),
    cache: "no-store",
  });
  const parsed: unknown = await res.json().catch(() => undefined);
  if (!res.ok) throw storyboardFailure(parsed, res.status);
}

export async function makeStoryboard(body: { story: string; character_en?: string; shots: ShotCount; record_id?: string | null }): Promise<StoryboardResult> {
  const res = await fetch(`${DASHBOARD_API_URL}${PATH}`, {
    method: "POST",
    headers: { "Content-Type": "application/json", ...dashboardAuth() },
    body: JSON.stringify({ ...body, story: body.story.trim(), character_en: body.character_en?.trim() || undefined, record_id: body.record_id || undefined }),
    cache: "no-store",
  });
  const parsed: unknown = await res.json().catch(() => undefined);
  if (!res.ok) throw storyboardFailure(parsed, res.status);
  return (parsed as { data: StoryboardResult }).data;
}
