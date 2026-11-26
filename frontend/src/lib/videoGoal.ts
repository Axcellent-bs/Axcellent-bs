// 영상 목적 블록 (11/26 K 시안 ⓪, 재현 님 "홍보인지 재미인지 광고인지 모르겠다"). 계약 VideoPlan.goal
// (fun·info·promo·ad)을 제작안 맨 위 "목적" 블록으로: 목적 칩 4개(고른 것 표시) · 왜 · 흐름 · 끝 행동 · 성공 기준.
// goal이 없는 예전 제작안은 purpose로 짐작한다(channel_content → 재미, client_offer·solution_ad → 광고).
// 순수 계산만 한다 (node --test videoGoal.test.ts). 문구는 기획 video_plan.GOAL_FLOW·GOAL_SUCCESS와 같다
import type { VideoPlanView } from './videoPlan'

export type VideoGoal = 'fun' | 'info' | 'promo' | 'ad'
export const GOAL_ORDER: VideoGoal[] = ['fun', 'info', 'promo', 'ad']
export const GOAL_INFO: Record<VideoGoal, { label: string; flow: string; success: string }> = {
  fun: { label: '재미', flow: '재밌는 릴스, 끝에 채널 이름만', success: '끝까지 본 비율·공유' },
  info: { label: '정보', flow: '문제 제기 → 팁·추천 2~3개 → 채널 이름', success: '저장·팔로우' },
  promo: { label: '홍보', flow: '브랜드·상품을 소품·자막으로 → 채널 이름', success: '저장·팔로우' },
  ad: { label: '광고', flow: '혜택 → 쓰는 장면 → 행동 문구', success: '클릭·구매' },
}
// AXcellent 자사 솔루션 광고 (purpose solution_ad)
export const AD_B2B = { flow: '문제 → 해결 → 문의 (B2B 템플릿)', success: '문의·데모' }
// 기획 video_plan.GOAL_WORDS와 같은 낱말 (왜 이 목적인지 의뢰문에서 찾는다)
const GOAL_WORDS: Record<VideoGoal, string[]> = {
  ad: ['광고', '할인', '이벤트', '쿠폰', '세일', '특가', '솔루션 소개'],
  info: ['꿀팁', '팁', '추천', '방법', '하는 법', '하는법', '정리', '비교', '리스트'],
  promo: ['홍보', '알리기', '알리는', '소개', '신상', '런칭', '브랜드'],
  fun: ['재밌', '재미있', '웃긴', '웃기', '밈', '챌린지'],
}
const GOALS = new Set<string>(GOAL_ORDER)

export const isGoal = (x: unknown): x is VideoGoal => typeof x === 'string' && GOALS.has(x)

/** 제작안 목적. goal이 있으면 그것, 없으면 purpose로 짐작(guessed). 둘 다 없으면 null */
export function goalOf(v: Pick<VideoPlanView, 'goal' | 'purpose'>): { goal: VideoGoal; guessed: boolean } | null {
  if (v.goal) return { goal: v.goal, guessed: false }
  if (v.purpose === 'channel_content') return { goal: 'fun', guessed: true }
  if (v.purpose === 'client_offer' || v.purpose === 'solution_ad') return { goal: 'ad', guessed: true }
  return null
}

/** 왜 이 목적인지 한 줄. 사람이 쓴 제작안 → "사람이 고름", 의뢰문에 그 목적 낱말이 있으면 그 낱말 */
export function goalWhy(goal: VideoGoal, v: Pick<VideoPlanView, 'writtenBy'>, guessed: boolean, requestText?: string | null): string {
  if (guessed) return '예전 제작안이라 광고 목적 칸으로 짐작'
  if (v.writtenBy === 'human') return '사람이 고름'
  const text = requestText ?? ''
  const word = GOAL_WORDS[goal].find((w) => text.includes(w))
  return word ? `의뢰문 "${word}"` : ''
}

export interface GoalBlockView {
  goal: VideoGoal
  label: string
  chips: { goal: VideoGoal; label: string; on: boolean }[]
  why: string
  flow: string
  cta: string // 끝 행동 (비면 없음)
  success: string
}

export function goalBlock(v: Pick<VideoPlanView, 'goal' | 'purpose' | 'writtenBy' | 'ctaKo'>, requestText?: string | null): GoalBlockView | null {
  const got = goalOf(v)
  if (!got) return null
  const { goal, guessed } = got
  const b2b = goal === 'ad' && v.purpose === 'solution_ad'
  return {
    goal,
    label: GOAL_INFO[goal].label,
    chips: GOAL_ORDER.map((g) => ({ goal: g, label: GOAL_INFO[g].label, on: g === goal })),
    why: goalWhy(goal, v, guessed, requestText),
    flow: b2b ? AD_B2B.flow : GOAL_INFO[goal].flow,
    cta: (v.ctaKo ?? '').trim(),
    success: b2b ? AD_B2B.success : GOAL_INFO[goal].success,
  }
}

/** 의뢰 카드 한 줄 "목적 · 홍보 · 끝 행동 "…"" (목적이 없으면 빈 문자열) */
export function goalLine(v: Pick<VideoPlanView, 'goal' | 'purpose' | 'writtenBy' | 'ctaKo'>): string {
  const b = goalBlock(v)
  if (!b) return ''
  return [`목적 · ${b.label}`, b.cta ? `끝 행동 "${b.cta}"` : ''].filter(Boolean).join(' · ')
}
