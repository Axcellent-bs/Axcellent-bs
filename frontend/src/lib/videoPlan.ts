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
  beatKo?: string // 11/26 형식 카드 비트 이름 (계약 VideoScene.beat_ko). 형식이 없으면 칸 없음
  beatPunch?: boolean // 그 비트가 펀치라인 비트 (화면 강조)
  placeKo?: string // 11/26 그 장면의 장소 (계약 VideoScene.place_ko). 비면 칸 없음
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
  fallbackDetail?: string // 11/26 U 금지 규칙 코드 (계약 fallback_detail, FALLBACK_DETAIL 키만)
  svgDetail?: string // 11/26 U 콘티 호출 예외 종류 이름 (계약 svg_detail)
  characterSourceReason?: CharacterSourceReason // 11/26 U 회사 브랜드로 쓴 이유 (계약 character_source_reason)
  // 11/26 주인공 (계약 VideoPlan.character_source·character_ko·character_look). 예전 제작안은 없음
  characterSource?: 'company_character' | 'brand'
  characterKo?: string // 회사 캐릭터 외형 요약 (마케팅 시트 summary_ko)
  characterLook?: 'photo' | 'illustration'
  requestRules?: RequestRuleView[] // 11/26 의뢰 연출 지시와 지켰는지 (계약 VideoPlan.request_rules). 없으면 칸 없음
  version?: number // 11/26 제작안 판 (계약 VideoPlan.version). 1판·예전 제작안은 칸 없음
  formatId?: string // 11/26 따른 형식 카드 (계약 VideoPlan.format_id·format_name). 없으면 칸 없음
  formatName?: string
  situationKo?: string // 보고서 웃음 재료 상황 한 줄
  checkLog?: CheckAttemptView[] // 11/26 제작 검사 기록 (계약 VideoPlan.check_log). 예전 제작안은 없음
  adjustments?: PlanAdjustmentView[] // 11/26 제작 검사 때문에 의뢰와 달라진 점
}

export type CheckKind = 'people' | 'actions' | 'props' | 'request_rule' | 'duration' | 'refs' | 'text'
/** kind 안의 자세한 검사 이름 (11/26 계약 CheckProblem.code). 모르는 값은 버린다(칸 없음 = 지금 줄) */
export type CheckCode = 'close_person' | 'place_change'
export const CHECK_CODE: Record<CheckCode, string> = { close_person: '참조 없는 인물', place_change: '장소 전환' }
export interface CheckProblemView { kind: CheckKind; scene: number | null; detailKo: string; code?: CheckCode }
export interface CheckAttemptView { attempt: number; by: 'planning' | 'marketing'; passed: boolean; problems: CheckProblemView[] }
export interface PlanAdjustmentView { ruleTextKo: string; changedToKo: string; whyKo: string }
const CHECK_KINDS = new Set<string>(['people', 'actions', 'props', 'request_rule', 'duration', 'refs', 'text'])
const CHECK_BY: Record<CheckAttemptView['by'], string> = { planning: '기획 검사', marketing: '마케팅 확인' }
/** 검사 한 줄 제목 "1차 초안 · 기획 검사 · 통과 못 함" */
export const checkAttemptTitle = (a: CheckAttemptView) => `${a.attempt}차 초안 · ${CHECK_BY[a.by]} · ${a.passed ? '통과' : '통과 못 함'}`
/** 문제 한 줄 "장면 2 · 인물 3명 (허용 2명)". code가 있으면 앞에 이름 "장소 전환 · 장면 4 · …" */
export const checkProblemLine = (p: CheckProblemView) => {
  const line = p.scene ? `장면 ${p.scene} · ${p.detailKo}` : p.detailKo
  return p.code ? `${CHECK_CODE[p.code]} · ${line}` : line
}
/** 검사 칩: 다시 써서 통과 / 검사 통과 / 확인 필요. 기록이 없으면 '' */
export function checkChip(v: Pick<VideoPlanView, 'checkLog'>): string {
  const log = v.checkLog ?? []
  if (!log.length) return ''
  const last = log[log.length - 1]
  if (!last.passed) return '확인 필요'
  return log.some((a) => !a.passed) ? '다시 써서 통과' : '검사 통과'
}
/** 판 칩 "2판(마케팅 요청으로 고침)". 1판·예전 제작안은 '' */
export function versionChip(v: Pick<VideoPlanView, 'version' | 'checkLog'>): string {
  if (!v.version || v.version < 2) return ''
  const byMarketing = (v.checkLog ?? []).some((a) => a.by === 'marketing')
  return `${v.version}판${byMarketing ? '(마케팅 요청으로 고침)' : ''}`
}
/** 형식 칩 "F-02 기대 vs 현실 · 보고서 추천". 형식이 없으면 '' (지금 화면) */
export const formatChip = (v: Pick<VideoPlanView, 'formatId' | 'formatName'>): string =>
  v.formatId ? `${[v.formatId, v.formatName].filter(Boolean).join(' ')} · 보고서 추천` : ''
/** "의뢰와 달라진 점" 한 줄 */
export const adjustmentLine = (a: PlanAdjustmentView) => `"${a.ruleTextKo}" → ${a.changedToKo} (${a.whyKo})`
function toCheckLog(raw: unknown): CheckAttemptView[] {
  if (!Array.isArray(raw)) return []
  return raw.flatMap((x) => {
    const o = obj(x)
    const attempt = o ? int(o.attempt) : null
    const by = o ? str(o.by) : ''
    if (!o || attempt === null || (by !== 'planning' && by !== 'marketing')) return []
    const problems = (Array.isArray(o.problems) ? o.problems : []).flatMap((y) => {
      const q = obj(y)
      const kind = q ? str(q.kind) : ''
      const detailKo = q ? str(pick(q, 'detailKo', 'detail_ko')) : ''
      if (!q || !CHECK_KINDS.has(kind) || !detailKo) return []
      const code = str(q.code)
      return [{ kind: kind as CheckKind, scene: int(q.scene), detailKo, ...(Object.prototype.hasOwnProperty.call(CHECK_CODE, code) ? { code: code as CheckCode } : {}) }]
    })
    return [{ attempt, by: by as CheckAttemptView['by'], passed: o.passed === true, problems }]
  })
}
function toAdjustments(raw: unknown): PlanAdjustmentView[] {
  if (!Array.isArray(raw)) return []
  return raw.flatMap((x) => {
    const o = obj(x)
    if (!o) return []
    const a = { ruleTextKo: str(pick(o, 'ruleTextKo', 'rule_text_ko')), changedToKo: str(pick(o, 'changedToKo', 'changed_to_ko')), whyKo: str(pick(o, 'whyKo', 'why_ko')) }
    return a.ruleTextKo && a.changedToKo && a.whyKo ? [a] : []
  })
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
/** 금칙어 든 의뢰 대사 (기획 plan_checks.BLOCKED_WHERE와 같은 글, 11/26) */
export const RULE_BLOCKED_WHERE = '광고 금칙어라 넣지 않아요'
const ruleResult = (r: RequestRuleView) =>
  r.kept ? r.where || '지킴' : r.where === RULE_BLOCKED_WHERE ? '이 문장은 광고 금칙어라 못 넣어요' : '못 지킴'
/** 지시 한 줄 "보라 무대 → 장면 1~3" / "… → 못 지킴" / 대사 "… → 이 문장은 광고 금칙어라 못 넣어요" */
export const requestRuleLine = (r: RequestRuleView) =>
  `${RULE_KIND[r.kind]} · ${r.kind === 'quote' ? `"${r.textKo}"` : r.textKo} → ${ruleResult(r)}`
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
export function characterLine(v: Pick<VideoPlanView, 'characterSource' | 'characterKo' | 'characterLook' | 'characterSourceReason'>): string {
  if (!v.characterSource) return ''
  if (v.characterSource !== 'company_character') {
    const why = v.characterSourceReason ? CHARACTER_REASON[v.characterSourceReason] : ''
    return why ? `회사 브랜드 기본 캐릭터 · ${why}` : '회사 브랜드 기본 캐릭터'
  }
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
/** 콘티 호출 실패의 자세한 코드 (11/26 C, 기획 storyboard_svg.failure_detail) → 짧은 한국어 */
export const VIDEO_SVG_DETAIL: Record<string, string> = {
  LLMResponseError_ReadTimeout: 'AI 응답이 늦어 끊겼어요',
  LLMResponseError_429: 'AI 호출 한도에 걸렸어요',
  // 11/26 F: 400(잘못된 요청) 공급자 메시지 갈래 (기획 storyboard_svg.bad_request_kind)
  LLMResponseError_400_schema: 'AI 응답 형식이 맞지 않았어요',
  LLMResponseError_400_length: 'AI 입력·출력이 너무 길어요',
  LLMResponseError_400_model: 'AI 모델 설정이 맞지 않아요',
}
/** 콘티를 못 그린 이유가 있을 때만 그 한 줄, 아니면 '' (지금 화면 그대로).
 *  예외 종류 이름(계약 svg_detail, 예: LLMResponseError)이 있으면 괄호로 붙인다 (11/26 U).
 *  아는 코드(ReadTimeout·429)면 괄호 대신 짧은 한국어를 붙인다 (11/26 C) */
export function videoSvgLine(v: Pick<VideoPlanView, 'svgReason' | 'svgDetail'>): string {
  if (!v.svgReason) return ''
  const base = VIDEO_SVG_LINE[v.svgReason]
  if (!v.svgDetail) return base
  const known = Object.prototype.hasOwnProperty.call(VIDEO_SVG_DETAIL, v.svgDetail) ? VIDEO_SVG_DETAIL[v.svgDetail] : ''
  return known ? `${base} · ${known}` : `${base} (${v.svgDetail})`
}

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
/** 금지 규칙에 걸린 진짜 이유 (계약 VideoPlan.fallback_detail, 기획 reject_detail 코드, 11/26 U).
 *  코드만 받는다 — 고객 글·이름은 서버가 넣지 않는다. 모르는 코드는 줄을 늘리지 않는다 */
export const FALLBACK_DETAIL: Record<string, string> = {
  scene_count: '장면 수가 3~6개가 아니어서',
  prompt_rule: '장면 프롬프트가 규칙에 걸려서',
  'prompt_rule:text_request': '프롬프트에 글자를 그리라는 말이 있어서',
  'prompt_rule:real_person': '프롬프트에 실존 인물 낱말이 있어서',
  'prompt_rule:real_brand': '프롬프트에 실제 브랜드 낱말이 있어서',
  'prompt_rule:url_or_number': '프롬프트에 주소·긴 숫자가 있어서',
  'prompt_rule:injection': '프롬프트에 규칙을 바꾸라는 말이 있어서',
  real_name_in_scene: '장면에 주제 후보 이름이 들어가서',
  marketing_word: '마케팅이 막는 말(참여 유도·과장)이 있어서',
  invented_offer_number: '혜택 문구에 근거 없는 숫자가 있어서',
  concept_starts_with_product: '콘셉트가 상품 이름으로 시작해서',
  report_title_as_topic: '보고서 이름을 소재로 써서',
}
/** 규칙 초안이고 이유가 있을 때만 그 한 줄, 아니면 '' (지금 화면 그대로). 자세한 이유가 있으면 뒤에 붙인다 */
export const videoFallbackLine = (v: Pick<VideoPlanView, 'writtenBy' | 'fallbackReason' | 'fallbackDetail'>) => {
  if (v.writtenBy !== 'rule' || !v.fallbackReason) return ''
  const detail = v.fallbackDetail ? FALLBACK_DETAIL[v.fallbackDetail] : undefined
  return detail ? `${VIDEO_FALLBACK_LINE[v.fallbackReason]} · ${detail}` : VIDEO_FALLBACK_LINE[v.fallbackReason]
}

export type CharacterSourceReason = 'no_record_id' | 'read_failed' | 'gate_held' | 'no_look'
/** 회사 캐릭터를 못 써 회사 브랜드로 쓴 이유 (계약 VideoPlan.character_source_reason, 11/26 U).
 *  no_record_id는 고르지 않은 것이라 줄을 늘리지 않는다 */
export const CHARACTER_REASON: Record<CharacterSourceReason, string> = {
  no_record_id: '',
  read_failed: '고른 회사 캐릭터를 읽지 못했어요',
  gate_held: '회사 캐릭터 읽기가 승인 대기예요',
  no_look: '회사 캐릭터 시트에 외형 글이 없어요',
}
const CHARACTER_REASONS = new Set<string>(Object.keys(CHARACTER_REASON))

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
  const beatKo = str(pick(o, 'beatKo', 'beat_ko'))
  const beat = beatKo ? { beatKo, ...(pick(o, 'beatPunch', 'beat_punch') === true ? { beatPunch: true } : {}) } : {}
  const placeKo = str(pick(o, 'placeKo', 'place_ko'))
  return { n, startS, endS, visual, caption: str(o.caption), prompt, ...kept, ...beat, ...(placeKo ? { placeKo } : {}) }
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
  const fallbackDetail = str(pick(o, 'fallbackDetail', 'fallback_detail'))
  const svgDetail = str(pick(o, 'svgDetail', 'svg_detail'))
  const sourceWhy = str(pick(o, 'characterSourceReason', 'character_source_reason'))
  const source = str(pick(o, 'characterSource', 'character_source'))
  const characterKo = str(pick(o, 'characterKo', 'character_ko'))
  const look = str(pick(o, 'characterLook', 'character_look'))
  const rules = toRules(pick(o, 'requestRules', 'request_rules'))
  const checkLog = toCheckLog(pick(o, 'checkLog', 'check_log'))
  const adjustments = toAdjustments(o.adjustments)
  const formatId = str(pick(o, 'formatId', 'format_id'))
  const formatName = str(pick(o, 'formatName', 'format_name'))
  const situationKo = str(pick(o, 'situationKo', 'situation_ko'))
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
    // 11/26 U: 코드만 받는다 (모양이 다르면 버린다)
    ...(FALLBACKS.has(fallback) && fallbackDetail in FALLBACK_DETAIL ? { fallbackDetail } : {}),
    ...(FALLBACKS.has(svgWhy) && /^[A-Za-z][A-Za-z0-9_]{0,39}$/.test(svgDetail) ? { svgDetail } : {}),
    ...(source === 'brand' && CHARACTER_REASONS.has(sourceWhy) ? { characterSourceReason: sourceWhy as CharacterSourceReason } : {}),
    ...(source === 'company_character' || source === 'brand' ? { characterSource: source } : {}),
    ...(source === 'company_character' && characterKo ? { characterKo } : {}),
    ...(source === 'company_character' && (look === 'photo' || look === 'illustration') ? { characterLook: look } : {}),
    ...(rules.length ? { requestRules: rules } : {}),
    ...((int(o.version) ?? 1) >= 2 ? { version: int(o.version) as number } : {}),
    ...(formatId ? { formatId, ...(formatName ? { formatName } : {}), ...(situationKo ? { situationKo } : {}) } : {}),
    ...(checkLog.length ? { checkLog } : {}),
    ...(adjustments.length ? { adjustments } : {}),
  }
}

export const VIDEO_WRITTEN_BY: Record<VideoPlanView['writtenBy'], string> = { llm: 'AI 초안', rule: '규칙 초안', human: '사람' }
/** 장면 시간 "0~2초" */
export const sceneTime = (s: Pick<VideoSceneView, 'startS' | 'endS'>) => `${s.startS}~${s.endS}초`
/** 섹션 머리 칩 "AI 초안 · 15초 · 9:16" */
export const videoPlanChip = (v: Pick<VideoPlanView, 'writtenBy' | 'durationS' | 'aspect'>) => `${VIDEO_WRITTEN_BY[v.writtenBy]} · ${v.durationS}초 · ${v.aspect}`
/** 요약 한 줄 "영상 제작안 · 15초 · 9:16 · 장면 4" */
export const videoPlanSummary = (v: Pick<VideoPlanView, 'durationS' | 'aspect' | 'scenes'>) => `영상 제작안 · ${v.durationS}초 · ${v.aspect} · 장면 ${v.scenes.length}`
