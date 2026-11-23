import { storyboardFailure } from "./storyboardError";
import { DASHBOARD_API_URL, dashboardAuth } from "./env";

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
}

export const SHOT_COUNTS = [6, 9, 12] as const;
export type ShotCount = (typeof SHOT_COUNTS)[number];
export const MAX_STORY = 1000;

const PATH = "/api/dashboard/marketing/storyboard";

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
