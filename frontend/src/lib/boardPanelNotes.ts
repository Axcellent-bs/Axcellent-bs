// 스토리보드 그림 밑 칸별 글 (11/26 C). 그림에는 글자를 넣지 않고(글자 깨짐) 화면이 칸 밑에 얹는다.
// 칸 배치는 서버 구도판(svg_raster.grid_layout)과 같다: 4칸 이하 가로 한 줄, 5~8칸 4열.
// node --test (boardPanelNotes.test.ts)
import type { StoryboardShot } from "./storyboard.ts";

export interface PanelNote {
  no: number;
  /** "0~4초" (시간이 없으면 빈 칸) */
  time: string;
  /** 샷 크기 · 앵글 (눈높이는 뺀다) */
  shot: string;
  /** 칸 장면 (동작) */
  action: string;
  /** 자막 (없으면 빈 칸) */
  caption: string;
}

const num = (v: number | null | undefined) => (typeof v === "number" && Number.isFinite(v) ? v : null);
const secs = (v: number) => (Number.isInteger(v) ? String(v) : v.toFixed(1));

export function panelNotes(shots: readonly StoryboardShot[]): PanelNote[] {
  return shots.map((s) => {
    const a = num(s.start_s), b = num(s.end_s);
    const time = a !== null && b !== null ? `${secs(a)}~${secs(b)}초` : "";
    const shot = [s.shot_size_ko, s.angle === "eye_level" ? "" : s.angle_ko].filter(Boolean).join(" · ");
    return { no: s.no, time, shot, action: s.scene_ko, caption: s.caption_ko ?? "" };
  });
}

/** 칸 글 격자 열 수 (구도판과 같은 배치) */
export const noteColumns = (count: number) => (count <= 4 ? Math.max(count, 1) : 4);
