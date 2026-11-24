// 인스타 레이더 "갑자기 뜬 게시물" 그리드의 순수 계산 (화면은 InstagramRadar.tsx).
// 모든 묶음의 대표 게시물을 media_id로 중복 제거하고 배수(ratio) 내림차순, 최대 12개.
// 숫자·배수·광고 재료 판정은 서버 값 그대로. 같은 게시물이 여러 묶음에 있으면 먼저 나온 묶음(서버 순서)의 판정을 쓴다.

import type { InstagramRadarGroup, InstagramRadarOutput, InstagramRadarPost } from "./data.ts";

export const GRID_MAX = 12;

export interface GridItem {
  post: InstagramRadarPost;
  /** 묶음 판정이 없는 게시물(밈 계정 게시물만 있는 경우)은 null */
  group: InstagramRadarGroup | null;
}

export type GridFilter = "all" | "benchmark" | "meme";
export type GridSort = "ratio" | "engagement";

/** 옛 저장 결과에는 source_kind가 없다: 벤치마크로 본다 */
export const isMeme = (p: Pick<InstagramRadarPost, "source_kind">): boolean => p.source_kind === "meme";

/** 묶음 대표 게시물 + 밈 계정 게시물을 media_id로 중복 제거 (묶음 판정이 먼저), 배수 내림차순 */
export function mergedItems(groups: InstagramRadarGroup[], memePosts: InstagramRadarPost[] = []): GridItem[] {
  const seen = new Map<string, GridItem>();
  for (const group of groups) {
    for (const post of group.top_posts) {
      if (!seen.has(post.media_id)) seen.set(post.media_id, { post, group });
    }
  }
  for (const post of memePosts) {
    if (!seen.has(post.media_id)) seen.set(post.media_id, { post, group: null });
  }
  return [...seen.values()].sort((a, b) => b.post.ratio - a.post.ratio);
}

/** 칩에 맞게 거르고 정렬한다. 정렬은 밈 칩에서만 바뀐다: engagement = 팔로워 대비 반응 내림차순, 값 없는 건 맨 뒤 */
export function gridView(
  groups: InstagramRadarGroup[], memePosts: InstagramRadarPost[] | undefined,
  filter: GridFilter = "all", sort: GridSort = "ratio", max = GRID_MAX,
): GridItem[] {
  let items = mergedItems(groups, memePosts ?? []);
  if (filter === "meme") items = items.filter((i) => isMeme(i.post));
  else if (filter === "benchmark") items = items.filter((i) => !isMeme(i.post));
  if (filter === "meme" && sort === "engagement") {
    const eng = (i: GridItem) => i.post.follower_engagement;
    items = [...items].sort((a, b) => {
      const x = eng(a), y = eng(b);
      if (x == null && y == null) return b.post.ratio - a.post.ratio;
      if (x == null) return 1;
      if (y == null) return -1;
      return y - x || b.post.ratio - a.post.ratio;
    });
  }
  return items.slice(0, max);
}

/** 묶음 대표 게시물만 (밈 게시물 없이): 모든 항목에 묶음이 있다 */
export function gridItems(groups: InstagramRadarGroup[], max = GRID_MAX): (GridItem & { group: InstagramRadarGroup })[] {
  return mergedItems(groups).slice(0, max) as (GridItem & { group: InstagramRadarGroup })[];
}

/** 칩을 보일지: 밈 계정 게시물이 하나라도 있을 때만 */
export function showChips(out: Pick<InstagramRadarOutput, "meme_posts">): boolean {
  return (out.meme_posts?.length ?? 0) > 0;
}

/** 팔로워 대비 반응 문구 (서버 값 × 100, 소수 1자리). 값이 없으면 null */
export function engagementLabel(p: Pick<InstagramRadarPost, "source_kind" | "follower_engagement">): string | null {
  if (!isMeme(p) || p.follower_engagement == null || !Number.isFinite(p.follower_engagement)) return null;
  return `팔로워 대비 반응 ${(p.follower_engagement * 100).toFixed(1)}%`;
}

/** 그리드를 보일지: 판정이 끝난(known) 결과에 게시물이 있을 때만 (첫 바퀴 진행 중이면 숨김) */
export function showGrid(out: InstagramRadarOutput): boolean {
  return out.status === "known" && (out.groups.some((g) => g.top_posts.length > 0) || (out.meme_posts?.length ?? 0) > 0);
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
