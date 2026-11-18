// 기획 제안 (11/4 데이터분석·기획 합의)
// 흐름: 데이터분석 인사이트(근거 수치) → 기획 판단(할 일·이유·목표) → 사람 확정·폐기
//       → 확정하면 마케팅 실행 또는 개발(요청 → 기획서) → 데이터분석 성과 측정 → 여기에 기록
// 원칙: 수치는 데이터분석만 만든다(facts) · 기획이 넣는 숫자는 목표치(target)뿐 · 설명(bodyMd)의 숫자가
//       facts·target에 없으면 경고 · id 사슬 insightId → 제안 id → campaign → result
// 백엔드: planning receive_insight · pull_insights(data promo_concepts) · marketing receive_campaign_brief ·
//         planning receive_campaign_update (마케팅 게시 · 데이터분석 성과)

export type ProposalFrom = 'data' | 'marketing' | 'finance'
export type ProposalStatus = '확인 대기' | '기획안' | '마케팅 전달' | '진행 중' | '성과 확인' | '기획서로' | '폐기'

/** 데이터 근거 한 줄: 무엇이 얼마인지 + 어느 skill이 낸 값인지 (데이터분석 값 그대로) */
export interface ProposalFact { label: string; value: string; source: string; link?: string }
/** 기획이 정한 목표치 (기획이 넣는 유일한 숫자) */
export interface ProposalTarget { label: string; value: string; measure?: string }
export interface ProposalResult { direction: '늘었음' | '같음' | '줄었음' | '확인 불가'; summary: string; facts?: ProposalFact[]; at: string }
export interface ProposalHandoff { ok: boolean; reason?: string; campaign?: string; at?: string; retry?: number } // retry: 마케팅 응답이 없어 자동으로 다시 보낸 횟수

/** 이 인사이트로 했던 지난 캠페인 결과 (데이터분석 문장 그대로) */
export interface ProposalPast { campaignId: string; viewsLabel: string; salesLabel: string; band: string; summaryLine: string; noteLine?: string; actions: string[] }

/** 기획 방향 선택지 (11/18): 갈래·추천은 백엔드 코드 규칙(idea_options.py)이 정한다 */
export type IdeaAngle = 'ride_trend' | 'own_trend' | 'with_product'
/** 올라탈 유행 후보 (데이터분석 값 그대로) */
export interface TrendRef { topic: string; medianViews?: number; at?: string; examples: string[] }
export interface IdeaOption { id: IdeaAngle; label: string; available: boolean; blockedReason?: string; refs: TrendRef[]; recommended: boolean; reason?: string }
/** 고객사 규모 숫자 (받은 때 기준, 모르면 비움) */
export interface ScaleFacts { followers?: number; followersChannel?: string; pastViewsMedian?: number; pastViewsCount: number }

export interface Proposal {
  id: string // PROP-n
  title: string
  from: ProposalFrom
  skill: string // 인사이트를 낸 skill
  insightId?: string // 보낸 쪽 인사이트 id
  insight: string // 받은 인사이트 (무엇을 발견했나)
  facts: ProposalFact[]
  products: string[]
  audience?: string // 타깃 연령대 (데이터분석)
  shortsFormat?: string // 잘 맞는 숏츠 형식 (데이터분석)
  bodyMd?: string // 보낸 쪽 설명 (마크다운)
  // 기획 판단 (AI 초안 → 사람이 고침)
  idea: string // 무엇을 할지
  why: string // 왜 해야 하는지
  target?: ProposalTarget
  format?: string
  writtenBy: 'llm' | 'rule' | 'human'
  warnings: string[] // 근거·목표에 없는 숫자 (코드 검사)
  status: ProposalStatus
  created: string // KST "MM-DD HH:mm"
  note?: string // 폐기 이유 · 확정 메모
  handoff?: ProposalHandoff
  result?: ProposalResult
  request?: string // 개발로 보낸 요청 REQ-n
  spec?: string // 확정해 만든 기획안 (기획서 SPEC-n, origin 'proposal')
  past?: ProposalPast[] // 지난 캠페인 결과 (받은 때 기준)
  followOf?: string // 후속 기획이면 이어 가는 앞 제안 PROP-n
  options: IdeaOption[] // 방향 선택지 (11/18 이전 제안은 빈 목록)
  scale?: ScaleFacts
  angle?: IdeaAngle // 사람이 고른 방향 (없으면 방향 선택 대기)
}

export const PROPOSAL_STAGES = ['받음', '기획안', '실행', '성과'] as const
/** 상태 → 흐름 칸 (폐기는 받음에서 끝난다). 확정하면 기획안을 먼저 만들고, 마케팅·개발로 넘기는 건 그다음 */
/** 마케팅에 넘기다 실패한 채 남은 기록 (서버는 이제 기획안으로 되돌리지만 예전 기록이 남아 있을 수 있다) */
export const handoffFailed = (p: Proposal) => p.status === '마케팅 전달' && p.handoff?.ok === false
/** 마케팅이 응답하지 않아 기획 AI가 자동으로 다시 보내는 중 (몇 번째인지) */
export const handoffRetrying = (p: Proposal) => (handoffFailed(p) ? p.handoff?.retry ?? 0 : 0)
export const proposalStage = (p: Proposal): number =>
  p.status === '확인 대기' || p.status === '폐기' ? 0 : p.status === '기획안' || handoffFailed(p) ? (p.spec ? 1 : 0) : p.status === '성과 확인' ? 3 : 2
export const proposalOpen = (p: Proposal) => p.status === '확인 대기'
/** 기획안을 만든 뒤 마케팅·개발로 넘길 수 있는 상태 */
export const proposalReady = (p: Proposal) => p.status === '기획안' || handoffFailed(p)
export const PROPOSAL_ORIGIN = 'proposal' // 기획 제안에서 만든 기획서 (Spec.origin)
export const proposalEnded = (p: Proposal) => p.status === '폐기'

export const PROPOSAL_SKILL_LABEL: Record<string, string> = {
  promo_concepts: '숏츠 아이디어',
  trend_insight_brief: '트렌드 인사이트',
  content_recommend: '소재 추천',
  youtube_velocity: '조회 급상승',
  trend_history: '시즌 흐름',
  trend_scan: '검색 급상승',
  campaign_effect: '캠페인 효과',
}
export const WRITTEN_BY_LABEL: Record<Proposal['writtenBy'], string> = { llm: 'AI 초안', rule: '규칙 초안', human: '사람 수정' }

export type ProposalAction =
  | { type: 'pullInsights' } // 데이터분석에서 새 인사이트 받아오기
  | { type: 'makePlan'; proposal: string } // 사람 확정 → 기획안 문서 만들기
  | { type: 'approveProposal'; proposal: string; note?: string } // 기획안 → 마케팅 (다음 단계)
  | { type: 'proposalToRequest'; proposal: string } // 기획안 → 개발 (다음 단계)
  | { type: 'rejectProposal'; proposal: string; reason: string } // 폐기
  | { type: 'deleteProposal'; proposal: string } // 삭제 (기획안으로 만든 기획서·요청도 같이, 개발 작업이 나갔으면 안 됨)
  | { type: 'measureProposal'; proposal: string } // 성과 재기 (데이터분석 campaign_effect, insight_id·제안 id 함께)
  | { type: 'chooseAngle'; proposal: string; angle: IdeaAngle } // 방향 고르기 → 그 방향으로 기획 판단 다시 쓰기
  | { type: 'editProposal'; proposal: string; title?: string; idea?: string; why?: string; targetLabel?: string; targetValue?: string }


// ---- 숫자 검사 (백엔드 proposals.number_warnings와 같은 규칙): 글의 숫자가 근거·목표·제목에 없으면 경고
const nums = (t: string) => new Set((t.match(/\d[\d,]*(?:\.\d+)?/g) ?? []).map((x) => x.replace(/,/g, '')))
export function numberWarnings(p: Pick<Proposal, 'idea' | 'why' | 'bodyMd' | 'facts' | 'target' | 'title'>): string[] {
  const ok = new Set<string>([...nums(p.title), ...p.facts.flatMap((f) => [...nums(`${f.label} ${f.value}`)]), ...(p.target ? nums(`${p.target.label} ${p.target.value}`) : [])])
  const out: string[] = []
  for (const [name, text] of [['할 일', p.idea], ['이유', p.why], ['설명', p.bodyMd ?? '']] as const) {
    const extra = [...nums(text)].filter((x) => !ok.has(x)).sort()
    if (extra.length) out.push(`${name}의 숫자 ${extra.join(', ')}가 근거·목표에 없음`)
  }
  return out
}
