// 기획 제안 (11/4 데이터분석·기획 합의)
// 흐름: 데이터분석 인사이트(근거 수치) → 기획 판단(할 일·이유·목표) → 사람 확정·폐기
//       → 확정하면 마케팅 실행 또는 개발(요청 → 기획서) → 데이터분석 성과 측정 → 여기에 기록
// 원칙: 수치는 데이터분석만 만든다(facts) · 기획이 넣는 숫자는 목표치(target)뿐 · 설명(bodyMd)의 숫자가
//       facts·target에 없으면 경고 · id 사슬 insightId → 제안 id → campaign → result
// 백엔드: planning receive_insight · pull_insights(data promo_concepts) · marketing receive_campaign_brief ·
//         planning receive_campaign_update (마케팅 게시 · 데이터분석 성과)

export type ProposalFrom = 'data' | 'marketing'
export type ProposalStatus = '확인 대기' | '기획안' | '마케팅 전달' | '진행 중' | '성과 확인' | '기획서로' | '폐기'

/** 데이터 근거 한 줄: 무엇이 얼마인지 + 어느 skill이 낸 값인지 (데이터분석 값 그대로) */
export interface ProposalFact { label: string; value: string; source: string; link?: string }
/** 기획이 정한 목표치 (기획이 넣는 유일한 숫자) */
export interface ProposalTarget { label: string; value: string; measure?: string }
export interface ProposalResult { direction: '늘었음' | '같음' | '줄었음' | '확인 불가'; summary: string; facts?: ProposalFact[]; at: string }
export interface ProposalHandoff { ok: boolean; reason?: string; campaign?: string; at?: string }

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
}

export const PROPOSAL_STAGES = ['받음', '기획안', '실행', '성과'] as const
/** 상태 → 흐름 칸 (폐기는 받음에서 끝난다). 확정하면 기획안을 먼저 만들고, 마케팅·개발로 넘기는 건 그다음 */
/** 마케팅에 넘기다 실패한 채 남은 기록 (서버는 이제 기획안으로 되돌리지만 예전 기록이 남아 있을 수 있다) */
export const handoffFailed = (p: Proposal) => p.status === '마케팅 전달' && p.handoff?.ok === false
export const proposalStage = (p: Proposal): number =>
  p.status === '확인 대기' || p.status === '폐기' ? 0 : p.status === '기획안' || handoffFailed(p) ? (p.spec ? 1 : 0) : p.status === '성과 확인' ? 3 : 2
export const proposalOpen = (p: Proposal) => p.status === '확인 대기'
/** 기획안을 만든 뒤 마케팅·개발로 넘길 수 있는 상태 */
export const proposalReady = (p: Proposal) => p.status === '기획안' || handoffFailed(p)
export const PROPOSAL_ORIGIN = 'proposal' // 기획 제안에서 만든 기획서 (Spec.origin)
export const proposalEnded = (p: Proposal) => p.status === '폐기'

export const PROPOSAL_SKILL_LABEL: Record<string, string> = {
  promo_concepts: '숏츠 기획안 재료',
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
  | { type: 'editProposal'; proposal: string; title?: string; idea?: string; why?: string; targetLabel?: string; targetValue?: string }

export const PROPOSAL_ACTIONS = new Set<string>(['makePlan', 'measureProposal', 'pullInsights', 'approveProposal', 'rejectProposal', 'deleteProposal', 'editProposal', 'proposalToRequest'])

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

export const SEED_PROPOSALS: Proposal[] = [
  {
    id: 'PROP-1', title: '가을 밤 독서 루틴 숏츠', from: 'data', skill: 'trend_history', insightId: 'ins-1104-1',
    insight: '"독서" 검색이 해마다 11월에 오르고, 루틴 브이로그 형식 숏츠의 조회가 책 추천 형식보다 높다',
    facts: [
      { label: '"독서" 검색 작년 11월', value: '평균의 1.6배', source: 'trend_history' },
      { label: '루틴 브이로그 숏츠 평균 조회', value: '4.2만', source: 'shorts_stats' },
      { label: '"책 추천" 숏츠 평균 조회', value: '1.1만', source: 'shorts_stats' },
      { label: '밤의 도서관 판매가', value: '16,800원', source: 'shop-api' },
    ],
    products: ['밤의 도서관', '하루 10분 독서 노트'], audience: '20대 여성', shortsFormat: '루틴 브이로그',
    bodyMd: '## 왜 이 소재인가\n- 11월은 해마다 "독서" 검색이 오르는 달이다 (작년 같은 달 평균의 1.6배)\n- 루틴 브이로그 형식은 책 추천 형식보다 평균 조회가 높다 (4.2만 대 1.1만)\n\n## 피할 것\n- 첫 컷부터 책을 보여 주면 광고로 읽혀 넘긴다',
    idea: '잠들기 전 독서 루틴을 브이로그 형식 숏츠로 보여 주고, 마지막 컷에서만 밤의 도서관 표지를 보여 준다',
    why: '"독서" 검색이 11월에 평균의 1.6배로 오르고, 루틴 브이로그 숏츠 평균 조회(4.2만)가 책 추천 형식(1.1만)보다 높다',
    target: { label: '캠페인 기간 주문 증분', value: '+10%', measure: 'campaign_effect' }, format: '숏폼 30초',
    writtenBy: 'llm', warnings: [], status: '확인 대기', created: '11-04 09:10',
  },
  {
    id: 'PROP-2', title: '출퇴근 오디오북 한 줄 숏츠', from: 'data', skill: 'trend_scan', insightId: 'ins-1103-4',
    insight: '"오디오북" 검색이 급상승했고 YouTube 인기 영상 태그에도 같이 보인다',
    facts: [
      { label: '"오디오북" 검색 급상승', value: '기준선 2.3배', source: 'trend_scan', link: 'https://datalab.naver.com/' },
      { label: 'YouTube 인기 영상 태그', value: '7개 영상', source: 'trend_candidates' },
    ],
    products: ['오디오북 이용권 1개월'], audience: '20~30대 직장인', shortsFormat: '책 속 한 줄',
    idea: '지하철에서 이어폰 끼고 듣는 장면에 책 속 한 줄 자막을 얹어 오디오북 이용권을 알린다',
    why: '"오디오북" 검색이 기준선의 2.3배로 올랐고 인기 영상 7개에 같은 태그가 있다',
    target: { label: '이용권 쿠폰 사용률', value: '20% 이상', measure: 'campaign_effect' }, format: '숏폼 15초',
    writtenBy: 'human', warnings: [], status: '진행 중', created: '11-03 14:20', handoff: { ok: true, campaign: 'CMP-12', at: '11-03 16:02' },
  },
  {
    id: 'PROP-3', title: '독서의 계절 기획전 배너', from: 'data', skill: 'campaign_effect', insightId: 'ins-1028-2',
    insight: '지난 기획전 배너 기간 주문이 늘었고 쿠폰 사용률도 높았다',
    facts: [
      { label: '기획전 기간 주문', value: '+18%', source: 'campaign_effect' },
      { label: '캠페인 쿠폰 사용률', value: '31%', source: 'campaign_effect' },
    ],
    products: [],
    idea: '같은 배너 형식으로 12월 연말 선물 기획전을 연다',
    why: '지난 기획전 기간 주문이 +18%, 쿠폰 사용률이 31%였다',
    target: { label: '캠페인 기간 주문 증분', value: '+15%', measure: 'campaign_effect' }, format: '배너',
    writtenBy: 'llm', warnings: [], status: '성과 확인', created: '10-28 11:00', handoff: { ok: true, campaign: 'CMP-9', at: '10-28 13:40' },
    result: { direction: '늘었음', summary: '주문 +18%, 쿠폰 사용률 31%', facts: [{ label: '캠페인 기간 주문', value: '+18%', source: 'campaign_effect' }], at: '11-02 10:00' },
  },
  {
    id: 'PROP-4', title: '"책 속 한 줄" 숏츠가 뜨는 이유', from: 'data', skill: 'youtube_velocity', insightId: 'ins-1104-2',
    insight: '시간당 조회 증가 상위 숏츠 3개 중 2개가 "책 속 한 줄" 형식이다',
    facts: [
      { label: '시간당 조회 증가 상위 3개 중', value: '2개가 같은 형식', source: 'youtube_velocity' },
      { label: '해당 형식 평균 길이', value: '18초', source: 'shorts_stats' },
    ],
    products: ['밤의 도서관', '불편한 편의점'],
    idea: '신간 3권의 한 문장을 검은 화면 자막으로 보여 주는 15초 숏츠를 만든다',
    why: '조회가 빠르게 느는 숏츠 3개 중 2개가 같은 형식이고 평균 길이가 18초로 짧다',
    format: '숏폼 15초',
    writtenBy: 'llm', warnings: ['할 일의 숫자 15가 근거·목표에 없음'], status: '확인 대기', created: '11-04 08:30',
  },
]

const POOL: Omit<Proposal, 'id' | 'created' | 'status'>[] = [
  {
    title: '수능 끝난 날 읽을 책 숏츠', from: 'data', skill: 'trend_history', insightId: 'ins-pool-1',
    insight: '"수능 끝나고" 검색이 작년 11월 셋째 주에 크게 올랐다',
    facts: [
      { label: '"수능 끝나고" 검색 작년 11월', value: '평균의 2.1배', source: 'trend_history' },
      { label: '질문형 숏츠 평균 조회', value: '2.8만', source: 'shorts_stats' },
    ],
    products: ['스무 살의 책장'],
    idea: '"수능 끝나고 처음 읽은 책" 질문형 숏츠로 댓글 참여를 받는다',
    why: '"수능 끝나고" 검색이 작년 11월 평균의 2.1배였고 질문형 숏츠 평균 조회가 2.8만이다',
    target: { label: '숏츠 댓글 수', value: '100개 이상', measure: 'shorts_stats' }, format: '숏폼 30초',
    writtenBy: 'llm', warnings: [],
  },
]

const nextId = (xs: Proposal[]) => `PROP-${Math.max(0, ...xs.map((x) => Number(x.id.split('-')[1]) || 0)) + 1}`

/** 예시 모드 reducer (planningData inner가 부른다). 요청 만들기는 planningData 쪽이 한다 */
export function reduceProposals(xs: Proposal[], a: ProposalAction, t: string): Proposal[] {
  const upd = (id: string, f: (p: Proposal) => Proposal) => xs.map((p) => (p.id === id ? f(p) : p))
  switch (a.type) {
    case 'pullInsights': {
      const src = POOL[xs.length % POOL.length]
      return [...xs, { ...src, id: nextId(xs), insightId: `${src.insightId}-${xs.length}`, created: t, status: '확인 대기' }]
    }
    case 'approveProposal':
      return upd(a.proposal, (p) => (proposalOpen(p) || proposalReady(p) ? { ...p, status: '마케팅 전달', note: a.note || p.note, handoff: { ok: true, at: t } } : p))
    case 'rejectProposal':
      return upd(a.proposal, (p) => (proposalOpen(p) ? { ...p, status: '폐기', note: a.reason } : p))
    case 'editProposal':
      return upd(a.proposal, (p) => {
        if (!proposalOpen(p)) return p
        const target = a.targetLabel?.trim() && a.targetValue?.trim() ? { label: a.targetLabel.trim(), value: a.targetValue.trim(), measure: p.target?.measure ?? 'campaign_effect' } : p.target
        const next = { ...p, title: a.title || p.title, idea: a.idea || p.idea, why: a.why || p.why, target, writtenBy: 'human' as const }
        return { ...next, warnings: numberWarnings(next) }
      })
    case 'measureProposal':
      // 예시 모드: 실제로 재지 않고 예시 결과를 붙인다
      return upd(a.proposal, (p) => (p.handoff?.campaign && !p.result ? { ...p, status: '성과 확인', result: { direction: '늘었음', summary: '예시 결과 · 주문이 기대보다 늘었다', facts: [{ label: '캠페인 기간 주문 증분', value: '+24건 (기대 120건)', source: 'campaign_effect' }], at: t } } : p))
    case 'proposalToRequest':
    case 'makePlan':
    case 'deleteProposal':
      return xs
  }
}
