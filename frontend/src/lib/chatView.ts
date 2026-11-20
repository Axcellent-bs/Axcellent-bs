// AI 오피스·AI 대화방 대화 칸의 순수 계산. node --test (chatView.test.ts)
// - markSeen: 말풍선 id → 처음 본 시각 (지난 일 다시 재생 막기)
// - scrollAnchor·restoreScroll: 위로 올려 보던 중 위쪽에 줄이 끼어도 보던 줄 그대로 (스크롤 밀림 막기)
// - messageWidth: 한 칸일 때 대화 줄을 가운데 최대 880px로

/** 화면을 연(방을 바꾼) 시각보다 이만큼 앞선 줄은 "예전 줄"로 본다 */
export const SEEN_GRACE_MS = 5_000;

type Seenable = { id: string; at?: string; lastAt?: string; kind?: string; state?: string };

const timeOf = (it: Seenable): number => {
  const t = Date.parse(it.at || it.lastAt || "");
  return Number.isFinite(t) ? t : NaN;
};

/**
 * 처음 보는 줄에 처음 본 시각을 단다 (map을 고친다). 0 = 이미 본 것(걸어가기·말풍선·"✓ 결과" 재생 없음), 그 밖 = now.
 * 11/20: 예전에는 "맨 처음 그릴 때 받은 줄"만 0이라 방을 바꾸거나 의뢰 목록이 늦게 와 거르기가 바뀌거나 공통 로그 창(1000줄)이
 * 움직이면 늦게 들어온 예전 줄이 지금 일처럼 다시 재생됐다. 이제 줄 시각(it.at, 없으면 lastAt)이 openedAt − 5초보다 이르면 0이다.
 * 시각을 모르는 줄도 0. 보류 카드의 승인(`<id>:approved`)도 같은 규칙: 보류를 이미 보던 중에 승인되면 now, 처음 볼 때 이미 승인돼 있으면 시각으로
 */
export function markSeen(map: Map<string, number>, items: readonly Seenable[], openedAt: number, now: number): Map<string, number> {
  const stamp = (it: Seenable) => {
    const t = timeOf(it);
    return Number.isFinite(t) && t >= openedAt - SEEN_GRACE_MS ? now : 0;
  };
  for (const it of items) {
    const known = map.has(it.id);
    if (!known) map.set(it.id, stamp(it));
    if (it.kind === "hold" && it.state === "approved") {
      const key = `${it.id}:approved`;
      if (!map.has(key)) map.set(key, known ? now : stamp(it));
    }
  }
  return map;
}

// ---- 스크롤 밀림 막기

/** 대화 칸 안 말풍선 하나의 위치 (칸 맨 위 기준 top, 높이) */
export interface RowBox { id: string; top: number; height: number }
/** 기억해 둘 기준: 화면 맨 위에 보이는 말풍선(과 그 뒤 id들, 사라지면 다음 것을 쓴다)과 화면 위치 */
export interface ScrollAnchor { ids: string[]; offset: number }

/** 지금 화면 맨 위에 걸쳐 보이는 말풍선 = 아래 끝이 scrollTop보다 아래인 첫 줄. offset = 그 줄 top − scrollTop */
export function scrollAnchor(rows: readonly RowBox[], scrollTop: number): ScrollAnchor | null {
  const i = rows.findIndex((r) => r.top + r.height > scrollTop);
  if (i < 0) return null;
  return { ids: rows.slice(i, i + 20).map((r) => r.id), offset: rows[i].top - scrollTop };
}

/** 다시 그린 뒤: 기준 말풍선(없으면 그 다음 것)이 같은 화면 위치에 오는 scrollTop. 하나도 없으면 null (그대로 둔다) */
export function restoreScroll(anchor: ScrollAnchor | null, rows: readonly RowBox[]): number | null {
  if (!anchor) return null;
  const at = new Map(rows.map((r) => [r.id, r.top]));
  for (const id of anchor.ids) {
    const top = at.get(id);
    if (top !== undefined) return Math.max(0, top - anchor.offset);
  }
  return null;
}

// ---- 폭

/** 대화 줄 폭: 한 칸 보기면 가운데 최대 880px, 여러 칸이면 칸 폭 그대로 */
export const messageWidth = (panes: number) => (panes <= 1 ? "mx-auto w-full max-w-[880px]" : "w-full");
