// 기획서 양식 (백엔드 planning_agent/spec_template.py와 이름·순서를 맞춘다)
// - plan 기획서 (요청 → 개발): 문제 · 목표 · 범위 · 요구사항 · 완료 기준 · 고려사항
// - proposal 기획 제안서 (기획 제안 → 마케팅·개발): 배경 · 목표 · 타깃 · 핵심 메시지 · 실행안 · 성공 지표 · 일정·예산 · 고려사항
// 기존 칸(problem·goal·요구 R·완료 C)은 그대로 쓰고, 양식에만 있는 칸은 Spec.extra[key]에 둔다

export type SectionSource = 'problem' | 'goal' | 'req' | 'done' | 'extra'
export interface Section { key: string; label: string; source: SectionSource; placeholder: string }
export type TemplateName = 'plan' | 'proposal'

export const TEMPLATES: Record<TemplateName, Section[]> = {
  plan: [
    { key: 'problem', label: '문제', source: 'problem', placeholder: '무엇이 문제인지, 누가 얼마나 겪는지' },
    { key: 'goal', label: '목표', source: 'goal', placeholder: '바뀐 뒤 무엇이 달라지는지' },
    { key: 'scope', label: '범위', source: 'extra', placeholder: '하는 것 · 하지 않는 것' },
    { key: 'req', label: '요구사항', source: 'req', placeholder: '' },
    { key: 'done', label: '완료 기준', source: 'done', placeholder: '' },
    { key: 'risks', label: '고려사항', source: 'extra', placeholder: '정책·제약·의존성·위험' },
  ],
  proposal: [
    { key: 'problem', label: '배경', source: 'problem', placeholder: '왜 지금: 데이터 인사이트·상황·근거 수치' },
    { key: 'goal', label: '목표', source: 'goal', placeholder: '이 캠페인으로 얻으려는 결과' },
    { key: 'audience', label: '타깃', source: 'extra', placeholder: '누구에게 (연령대·관심사·상황)' },
    { key: 'message', label: '핵심 메시지', source: 'extra', placeholder: '타깃에게 남길 한 줄' },
    { key: 'req', label: '실행안', source: 'req', placeholder: '' },
    { key: 'done', label: '성공 지표', source: 'done', placeholder: '' },
    { key: 'schedule', label: '일정·예산', source: 'extra', placeholder: '언제까지, 얼마로' },
    { key: 'risks', label: '고려사항', source: 'extra', placeholder: '브랜드·저작권·실존 이름 주의' },
  ],
}

export const templateOf = (origin?: string): TemplateName => (origin === 'proposal' ? 'proposal' : 'plan')
export const TEMPLATE_NAME: Record<TemplateName, string> = { plan: '기획서', proposal: '기획 제안서' }
