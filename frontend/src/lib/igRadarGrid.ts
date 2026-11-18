// 인스타 레이더 "갑자기 뜬 게시물" 그리드의 순수 계산 (화면은 InstagramRadar.tsx).
// 모든 묶음의 대표 게시물을 media_id로 중복 제거하고 배수(ratio) 내림차순, 최대 12개.
// 숫자·배수·광고 재료 판정은 서버 값 그대로. 같은 게시물이 여러 묶음에 있으면 먼저 나온 묶음(서버 순서)의 판정을 쓴다.

import type { InstagramRadarGroup, InstagramRadarOutput, InstagramRadarPost } from "./data.ts";

export const GRID_MAX = 12;

export interface GridItem {
  post: InstagramRadarPost;
  group: InstagramRadarGroup;
}

export function gridItems(groups: InstagramRadarGroup[], max = GRID_MAX): GridItem[] {
  const seen = new Map<string, GridItem>();
  for (const group of groups) {
    for (const post of group.top_posts) {
      const prev = seen.get(post.media_id);
      if (!prev) seen.set(post.media_id, { post, group });
    }
  }
  return [...seen.values()].sort((a, b) => b.post.ratio - a.post.ratio).slice(0, max);
}

/** 그리드를 보일지: 판정이 끝난(known) 결과에 게시물이 있을 때만 (첫 바퀴 진행 중이면 숨김) */
export function showGrid(out: InstagramRadarOutput): boolean {
  return out.status === "known" && out.groups.some((g) => g.top_posts.length > 0);
}

/** 왼쪽 아래 종류: 릴스 · 사진 · 캐러셀 (모르면 null) */
export function kindLabel(p: Pick<InstagramRadarPost, "media_type" | "media_product_type">): string | null {
  if (p.media_product_type === "REELS") return "릴스";
  if (p.media_type === "CAROUSEL_ALBUM") return "캐러셀";
  if (p.media_type === "IMAGE") return "사진";
  if (p.media_type === "VIDEO") return "영상";
  return null;
}

/** 썸네일은 서버가 걸러 준 https 주소만 (그 밖은 회색 칸) */
export function thumbSrc(url: string | null | undefined): string | null {
  return typeof url === "string" && /^https:\/\//.test(url) ? url : null;
}
