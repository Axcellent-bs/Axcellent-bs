// 기획 → 마케팅 영상 제작안 (계약 ax_contracts.schemas.planning VideoPlan·VideoScene과 같은 칸, 화면은 camelCase).
// 기획 state 응답 proposals[].video_plan(fromBackend가 camelize하면 videoPlan)을 읽는다. node --test (videoPlan.test.ts)
// 모양이 틀리면 undefined (화면이 섹션을 그리지 않는다). 던지지 않는다. 값은 서버 그대로이고 지어내지 않는다

export interface VideoSceneView {
  n: number
  startS: number
  endS: number
  visual: string // 화면 구성 (한국어)
  caption: string // 자막 (비면 자막 없음)
  prompt: string // 영상 생성 프롬프트 (영문)
  // 아래 키프레임 칸은 예전 제작안엔 없다 (비면 칸 자체를 두지 않는다). 11/25 계약 VideoScene
  startFrame?: string // 장면 첫 화면 (한국어)
  endFrame?: string // 장면 끝 화면 (한국어)
  actionEn?: string // 첫→끝 사이 동작 (영문)
  cameraEn?: string // 샷 크기·카메라 움직임 (영문)
  // 기획이 그린 SVG 콘티 (11/25 계약 VideoScene.start_svg·end_svg). 화면은 <img data URI>로만 그린다
  startSvg?: string // 장면 첫 화면 구도
  endSvg?: string // 장면 끝 화면 구도
}

/** 광고 목적 (계약 VideoPlan.purpose). 없으면 칩을 그리지 않는다 */
export type VideoPurpose = 'solution_ad' | 'client_offer' | 'channel_content'
export const VIDEO_PURPOSE: Record<VideoPurpose, string> = {
  solution_ad: '솔루션 광고',
  client_offer: '고객 혜택 광고',
  channel_content: '채널 콘텐츠',
}

export interface VideoPlanView {
  concept: string
  durationS: number
  aspect: string // "9:16" · "1:1" · "16:9"
  script: string
  scenes: VideoSceneView[]
  style: string // 공통 화풍
  negative: string // 공통 금지
  writtenBy: 'llm' | 'rule' | 'human'
  mediaHints?: { season?: string; colorTheme?: string; category?: string }
  purpose?: VideoPurpose // 광고 목적 (예전 제작안·규칙 제작안은 없음)
  offerKo?: string // 혜택 한 줄
  ctaKo?: string // 마지막 행동 문구
  fallbackReason?: VideoFallbackReason // 규칙 초안이 된 이유 (11/25, 예전 제작안·LLM 제작안은 없음)
  svgReason?: VideoFallbackReason // 3D 마네킹 콘티를 못 그린 이유 (11/25, 그렸거나 예전 제작안은 없음)
  // 11/26 주인공 (계약 VideoPlan.character_source·character_ko·character_look). 예전 제작안은 없음
  characterSource?: 'company_character' | 'brand'
  characterKo?: string // 회사 캐릭터 외형 요약 (마케팅 시트 summary_ko)
  characterLook?: 'photo' | 'illustration'
  requestRules?: RequestRuleView[] // 11/26 의뢰 연출 지시와 지켰는지 (계약 VideoPlan.request_rules). 없으면 칸 없음
}

export type RequestRuleKind = 'quote' | 'ending' | 'setting' | 'prop' | 'action' | 'look'
export interface RequestRuleView { kind: RequestRuleKind; textKo: string; kept: boolean; where: string }
export const RULE_KIND: Record<RequestRuleKind, string> = {
  quote: '대사', ending: '마지막 장면', setting: '배경', prop: '소품', action: '동작', look: '외형',
}
/** "의뢰 지시 3/3 지킴". 지시가 없으면 '' (줄을 그리지 않는다) */
export function requestRulesChip(v: Pick<VideoPlanView, 'requestRules'>): string {
  const rules = v.requestRules ?? []
  return rules.length ? `의뢰 지시 ${rules.filter((r) => r.kept).length}/${rules.length} 지킴` : ''
}
/** 지시 한 줄 "보라 무대 → 장면 1~3" / "… → 못 지킴" */
export const requestRuleLine = (r: RequestRuleView) =>
  `${RULE_KIND[r.kind]} · ${r.kind === 'quote' ? `"${r.textKo}"` : r.textKo} → ${r.kept ? r.where || '지킴' : '못 지킴'}`
const RULE_KINDS = new Set<string>(Object.keys(RULE_KIND))
function toRules(raw: unknown): RequestRuleView[] {
  if (!Array.isArray(raw)) return []
  return raw.flatMap((x) => {
    const o = obj(x)
    if (!o) return []
    const kind = str(o.kind), textKo = str(pick(o, 'textKo', 'text_ko'))
    if (!RULE_KINDS.has(kind) || !textKo) return []
    return [{ kind: kind as RequestRuleKind, textKo, kept: o.kept === true, where: str(o.where) }]
  })
}

const LOOK_LABEL: Record<'photo' | 'illustration', string> = { photo: '실사 그대로', illustration: '그림체 그대로' }
/** "주인공" 줄 (기획 video_plan.character_line과 같은 문구). 예전 제작안(칸 없음)은 '' (줄을 그리지 않는다) */
export function characterLine(v: Pick<VideoPlanView, 'characterSource' | 'characterKo' | 'characterLook'>): string {
  if (!v.characterSource) return ''
  if (v.characterSource !== 'company_character') return '회사 브랜드 기본 캐릭터'
  const head = v.characterKo ? `회사 캐릭터 (${v.characterKo})` : '회사 캐릭터'
  return [head, v.characterLook ? LOOK_LABEL[v.characterLook] : ''].filter(Boolean).join(' · ')
}

/** "장면 그림 자리" 위 이유 한 줄 (계약 VideoPlan.svg_reason) */
export const VIDEO_SVG_LINE: Record<VideoFallbackReason, string> = {
  no_llm: '회사 AI가 연결되지 않아 콘티를 못 그렸어요',
  timeout: 'AI가 제한 시간 안에 포즈를 주지 못했어요',
  bad_format: 'AI 포즈 답 형식이 맞지 않았어요',
  rejected: '포즈 답이 규칙에 걸렸어요',
  error: '콘티 호출이 실패했어요',
}
/** 콘티를 못 그린 이유가 있을 때만 그 한 줄, 아니면 '' (지금 화면 그대로) */
export const videoSvgLine = (v: Pick<VideoPlanView, 'svgReason'>) => (v.svgReason ? VIDEO_SVG_LINE[v.svgReason] : '')

export type VideoFallbackReason = 'no_llm' | 'timeout' | 'bad_format' | 'rejected' | 'error'
/** 규칙 초안 칩 옆 이유 한 줄 (계약 VideoPlan.fallback_reason) */
export const VIDEO_FALLBACK_LINE: Record<VideoFallbackReason, string> = {
  no_llm: '회사 AI가 연결되지 않아 규칙으로 썼어요',
  timeout: 'AI가 제한 시간 안에 답하지 못해 규칙으로 썼어요',
  bad_format: 'AI 답 형식이 맞지 않아 규칙으로 썼어요',
  rejected: 'AI 답이 금지 규칙에 걸려 규칙으로 썼어요',
  error: 'AI 호출이 실패해 규칙으로 썼어요',
}
const FALLBACKS = new Set<string>(Object.keys(VIDEO_FALLBACK_LINE))
/** 규칙 초안이고 이유가 있을 때만 그 한 줄, 아니면 '' (지금 화면 그대로) */
export const videoFallbackLine = (v: Pick<VideoPlanView, 'writtenBy' | 'fallbackReason'>) =>
  v.writtenBy === 'rule' && v.fallbackReason ? VIDEO_FALLBACK_LINE[v.fallbackReason] : ''

type Json = Record<string, unknown>
const obj = (v: unknown): Json | null => (v && typeof v === 'object' && !Array.isArray(v) ? (v as Json) : null)
/** snake_case(서버 원본)·camelCase(fromBackend camelize 뒤) 둘 다 */
const pick = (o: Json, camel: string, snake: string): unknown => (o[camel] !== undefined ? o[camel] : o[snake])
const str = (v: unknown) => (typeof v === 'string' ? v.trim() : '')
const int = (v: unknown) => (typeof v === 'number' && Number.isFinite(v) ? Math.round(v) : null)
const ASPECTS = new Set(['9:16', '1:1', '16:9'])

function toScene(raw: unknown): VideoSceneView | null {
  const o = obj(raw)
  if (!o) return null
  const n = int(o.n), startS = int(pick(o, 'startS', 'start_s')), endS = int(pick(o, 'endS', 'end_s'))
  const visual = str(o.visual), prompt = str(o.prompt)
  if (n === null || startS === null || endS === null || endS <= startS || !visual) return null
  const frames = {
    startFrame: str(pick(o, 'startFrame', 'start_frame')),
    endFrame: str(pick(o, 'endFrame', 'end_frame')),
    actionEn: str(pick(o, 'actionEn', 'action_en')),
    cameraEn: str(pick(o, 'cameraEn', 'camera_en')),
    startSvg: str(pick(o, 'startSvg', 'start_svg')),
    endSvg: str(pick(o, 'endSvg', 'end_svg')),
  }
  const kept = Object.fromEntries(Object.entries(frames).filter(([, v]) => v)) as Partial<typeof frames>
  return { n, startS, endS, visual, caption: str(o.caption), prompt, ...kept }
}

/** 장면 키프레임 한 줄 "첫 화면 → 끝 화면 · 동작 · 카메라". 끝 화면·동작·카메라가 다 비면 null
 *  (규칙 제작안은 첫 화면 = 화면 구성뿐이라 줄을 그리지 않는다. 기획 video_plan.keyframe_line과 같은 규칙) */
export function sceneKeyframeLine(s: Pick<VideoSceneView, 'startFrame' | 'endFrame' | 'actionEn' | 'cameraEn'>): string | null {
  if (!s.endFrame && !s.actionEn && !s.cameraEn) return null
  const frames = [s.startFrame, s.endFrame].filter(Boolean).join(' → ')
  return [frames, s.actionEn, s.cameraEn].filter(Boolean).join(' · ')
}

const PURPOSES = new Set<string>(Object.keys(VIDEO_PURPOSE))
/** 광고 목적 칩 글. 모르는 값·없으면 null */
export const videoPurposeChip = (v: Pick<VideoPlanView, 'purpose'>): string | null => (v.purpose ? VIDEO_PURPOSE[v.purpose] : null)

/** 응답의 video_plan(또는 videoPlan) → 화면 값. 콘셉트·길이·장면이 없거나 장면 하나라도 모양이 틀리면 undefined */
export function toVideoPlan(raw: unknown): VideoPlanView | undefined {
  const o = obj(raw)
  if (!o) return undefined
  const concept = str(o.concept)
  const durationS = int(pick(o, 'durationS', 'duration_s'))
  const list = Array.isArray(o.scenes) ? o.scenes : []
  const scenes = list.map(toScene)
  if (!concept || durationS === null || durationS <= 0 || scenes.length === 0 || scenes.some((x) => x === null)) return undefined
  const aspect = str(o.aspect) || '9:16'
  if (!ASPECTS.has(aspect)) return undefined
  const wb = pick(o, 'writtenBy', 'written_by')
  const h = obj(pick(o, 'mediaHints', 'media_hints'))
  const hints = h ? { season: str(h.season) || undefined, colorTheme: str(pick(h, 'colorTheme', 'color_theme')) || undefined, category: str(h.category) || undefined } : undefined
  const purpose = str(o.purpose)
  const offerKo = str(pick(o, 'offerKo', 'offer_ko')), ctaKo = str(pick(o, 'ctaKo', 'cta_ko'))
  const fallback = str(pick(o, 'fallbackReason', 'fallback_reason'))
  const svgWhy = str(pick(o, 'svgReason', 'svg_reason'))
  const source = str(pick(o, 'characterSource', 'character_source'))
  const characterKo = str(pick(o, 'characterKo', 'character_ko'))
  const look = str(pick(o, 'characterLook', 'character_look'))
  const rules = toRules(pick(o, 'requestRules', 'request_rules'))
  return {
    concept, durationS, aspect, script: str(o.script),
    scenes: (scenes as VideoSceneView[]).slice().sort((a, b) => a.n - b.n),
    style: str(o.style), negative: str(o.negative),
    writtenBy: wb === 'llm' || wb === 'human' ? wb : 'rule',
    ...(hints && (hints.season || hints.colorTheme || hints.category) ? { mediaHints: hints } : {}),
    ...(PURPOSES.has(purpose) ? { purpose: purpose as VideoPurpose } : {}),
    ...(offerKo ? { offerKo } : {}),
    ...(ctaKo ? { ctaKo } : {}),
    ...(FALLBACKS.has(fallback) ? { fallbackReason: fallback as VideoFallbackReason } : {}),
    ...(FALLBACKS.has(svgWhy) ? { svgReason: svgWhy as VideoFallbackReason } : {}),
    ...(source === 'company_character' || source === 'brand' ? { characterSource: source } : {}),
    ...(source === 'company_character' && characterKo ? { characterKo } : {}),
    ...(source === 'company_character' && (look === 'photo' || look === 'illustration') ? { characterLook: look } : {}),
    ...(rules.length ? { requestRules: rules } : {}),
  }
}

export const VIDEO_WRITTEN_BY: Record<VideoPlanView['writtenBy'], string> = { llm: 'AI 초안', rule: '규칙 초안', human: '사람' }
/** 장면 시간 "0~2초" */
export const sceneTime = (s: Pick<VideoSceneView, 'startS' | 'endS'>) => `${s.startS}~${s.endS}초`
/** 섹션 머리 칩 "AI 초안 · 15초 · 9:16" */
export const videoPlanChip = (v: Pick<VideoPlanView, 'writtenBy' | 'durationS' | 'aspect'>) => `${VIDEO_WRITTEN_BY[v.writtenBy]} · ${v.durationS}초 · ${v.aspect}`
/** 요약 한 줄 "영상 제작안 · 15초 · 9:16 · 장면 4" */
export const videoPlanSummary = (v: Pick<VideoPlanView, 'durationS' | 'aspect' | 'scenes'>) => `영상 제작안 · ${v.durationS}초 · ${v.aspect} · 장면 ${v.scenes.length}`
