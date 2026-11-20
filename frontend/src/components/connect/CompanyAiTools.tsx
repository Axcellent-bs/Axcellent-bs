import { useCallback, useEffect, useState, type FormEvent, type ReactNode } from 'react'
import { Notice, SectionHead, StatusChip } from './parts'
import { PreparingChip } from '@/components/Preparing'
import {
  KEY_LABEL, budgetKo, canApprove, limitLineKo, parseLimitPercent, historyLineKo, keyHintKo, keyResultKo, parseBudget, percentKo, providerToSave, providerView,
  saveBlockReason, settingsBody, stateView, tokensKo, videoView, whenKo,
  type AiStatus, type CompanyAiAdmin, type CoderMode, type KeyRequest,
} from '@/lib/companyAi'
import {
  approveKeyRequest, deleteCompanyAiKey, errText, getAiStatus, getCompanyAi, putCompanyAiKey, rejectKeyRequest, saveCompanyAiSettings,
} from '@/lib/companyAiApi'

// 시스템 관리 › 회사 연결 › 공통 AI 도구 연결 (관리자만, 회사 AI 연결 설계 1·3·8장).
// 회사당 모델 하나·키 하나. 예외는 개발 AI 코딩 에이전트("따로 설정")와 마케팅 영상 생성뿐.
// 키는 저장 뒤 끝 4자리만 보인다. 교체·삭제는 다른 관리자 승인(관리자가 한 명이면 바로 + 회원 알림).
// 값은 모두 dashboard-api(`/company-ai`, `/ai-connection/status`)에서 받는다. 실패하면 서버가 준 이유를 그대로 보인다.

const OP_KO: Record<string, string> = { add: '등록', replace: '교체', delete: '삭제' }

/** 버튼 하나의 바쁨·결과·오류 */
function useAct(onDone: () => void) {
  const [busy, setBusy] = useState(false)
  const [msg, setMsg] = useState<string | null>(null)
  const [error, setError] = useState<string | null>(null)
  const run = async (f: () => Promise<string | null>) => {
    setBusy(true)
    setMsg(null)
    setError(null)
    try { setMsg(await f()) } catch (e) { setError(errText(e)) } finally { setBusy(false); onDone() }
  }
  return { busy, msg, error, setError, run }
}

function Result({ msg, error }: { msg: string | null; error: string | null }) {
  if (error) return <p role="alert" className="text-sm text-red-700">{error}</p>
  return msg ? <p role="status" className="text-sm text-emerald-700">{msg}</p> : null
}

/** 사용 막대. `cap`(상한 %)이 있으면 그 자리에 세로 선을 긋고 `capLabel`을 막대 위에 적는다 */
function Meter({ percent, label, cap, capLabel }: { percent: number | null; label: string; cap?: number; capLabel?: string | null }) {
  const w = Math.max(0, Math.min(100, percent ?? 0))
  const stop = cap ?? 100
  return (
    <div className="space-y-1">
      {capLabel && <p className="text-xs text-ink-700">{capLabel}</p>}
      <div className="relative h-2 overflow-hidden rounded-full bg-ink-100" role="meter" aria-valuemin={0} aria-valuemax={100} aria-valuenow={Math.round(w)} aria-label={label}>
        <div className={`h-full ${w >= stop ? 'bg-red-700' : w >= stop * 0.8 ? 'bg-amber-600' : 'bg-brand-600'}`} style={{ width: `${w}%` }} />
        {cap != null && cap < 100 && <span aria-hidden="true" className="absolute inset-y-0 w-0.5 bg-ink-900" style={{ left: `${cap}%` }} title={capLabel ?? undefined} />}
      </div>
      <p className="text-xs text-ink-500">{label}</p>
    </div>
  )
}

function Field({ label, note, children }: { label: string; note?: ReactNode; children: ReactNode }) {
  return (
    <label className="block space-y-1 text-sm">
      <span className="text-ink-700">{label}</span>
      {children}
      {note && <span className="block text-xs text-ink-500">{note}</span>}
    </label>
  )
}

/** 모델 이름 + (목록에 없으면) 주소 칸. 공급자는 서버가 정하고 화면은 짐작만 보인다 */
function ModelFields({ model, baseUrl, provider, onModel, onUrl, disabled, placeholder }: {
  model: string; baseUrl: string; provider: string; onModel: (v: string) => void; onUrl: (v: string) => void; disabled: boolean; placeholder?: string
}) {
  const view = providerView(model, providerToSave(model, baseUrl, provider), baseUrl)
  return (
    <>
      <Field label="모델" note={view.label}>
        <input className="field font-mono" value={model} onChange={(e) => onModel(e.target.value)} disabled={disabled} autoComplete="off" placeholder={placeholder ?? '공급자 계정의 모델 이름'} maxLength={200} />
      </Field>
      {view.needsUrl && (
        <Field label="주소" note="목록에 없는 모델 · OpenAI 호환 서버 주소 (비우면 모델 이름으로 공급자를 찾아요)">
          <input className="field font-mono" value={baseUrl} onChange={(e) => onUrl(e.target.value)} disabled={disabled} autoComplete="off" placeholder="https://…/v1" maxLength={500} />
        </Field>
      )}
    </>
  )
}

function KeyInput({ value, onChange, hasKey, hint, disabled, emptyNote, placeholder }: {
  value: string; onChange: (v: string) => void; hasKey: boolean; hint: string; disabled: boolean; emptyNote: string; placeholder: string
}) {
  return (
    <Field label="API 키" note={hasKey ? `저장된 키 ${keyHintKo(hint)} · 새로 넣으면 교체해요` : emptyNote}>
      <input className="field font-mono" type="password" autoComplete="off" value={value} onChange={(e) => onChange(e.target.value)} disabled={disabled}
        placeholder={hasKey ? '비우면 지금 키 그대로' : placeholder} />
    </Field>
  )
}

const urlNeeded = (model: string, baseUrl: string, provider: string) =>
  providerView(model, providerToSave(model, baseUrl, provider), baseUrl).needsUrl && !baseUrl.trim()

/** 회사 AI 모델: 모델·주소·키·월 예산(토큰) */
function CompanyModelCard({ a, status, blocked, onDone }: { a: CompanyAiAdmin; status: AiStatus | null; blocked: boolean; onDone: () => void }) {
  const s = a.settings
  const key = a.keys.LLM_API_KEY
  const [model, setModel] = useState(s.model)
  const [baseUrl, setBaseUrl] = useState(s.base_url)
  const [value, setValue] = useState('')
  const [budget, setBudget] = useState(s.monthly_budget_tokens == null ? '' : String(s.monthly_budget_tokens))
  const [cap, setCap] = useState(s.monthly_limit_percent == null ? '' : String(s.monthly_limit_percent))
  const act = useAct(onDone)
  const st = status ? stateView(status.state) : null
  const trial = status?.trial
  const onTrial = !!trial?.available && (status?.state === 'trial' || status?.state === 'trial_exhausted')

  const save = (e: FormEvent) => {
    e.preventDefault()
    const n = parseBudget(budget)
    if (n === 'invalid') return act.setError('월 예산은 0 이상 정수(토큰)로 넣어 주세요. 비우면 예산 없음이에요.')
    const limit = parseLimitPercent(cap)
    if (limit === 'invalid') return act.setError('상한 %는 1~100 사이 정수로 넣어 주세요. 비우면 100%예요.')
    if (urlNeeded(model, baseUrl, s.provider)) return act.setError('목록에 없는 모델이라 OpenAI 호환 주소가 필요해요.')
    const v = value.trim()
    void act.run(async () => {
      const r = await saveCompanyAiSettings(settingsBody(s, { model, base_url: baseUrl, monthly_budget_tokens: n, monthly_limit_percent: limit }))
      const parts = [r.changed.length ? '설정을 저장했어요. 회원 모두에게 알렸어요.' : '설정은 그대로예요.']
      if (v) {
        parts.push(keyResultKo(await putCompanyAiKey('LLM_API_KEY', v), a.approval.admins))
        setValue('')
      }
      return parts.join(' ')
    })
  }
  const clear = () => {
    if (!window.confirm(a.approval.needed ? '회사 AI 키 삭제를 요청할까요? 다른 관리자가 승인하면 지워져요.' : '회사 AI 키를 지울까요? 관리자가 한 명뿐이라 바로 지워져요.')) return
    void act.run(async () => keyResultKo(await deleteCompanyAiKey('LLM_API_KEY'), a.approval.admins))
  }

  return (
    <form onSubmit={save} className="card space-y-4 p-6">
      <SectionHead as="h3" title="회사 AI 모델" desc="모든 AI 직원이 기본으로 써요. 키가 없으면 체험 토큰(설치 운영자가 넣었을 때만)으로 돌아요."
        right={st && <StatusChip tone={st.tone}>{st.label}{key.has_key ? <> · <span className="font-mono">{keyHintKo(key.hint)}</span></> : null}</StatusChip>} />
      {onTrial && trial && (
        <Meter percent={trial.percent} label={`체험 토큰 ${tokensKo(trial.used_tokens)} / ${tokensKo(trial.limit_tokens)} 사용 (${percentKo(trial.percent)})`} />
      )}
      <div className="grid gap-4 md:grid-cols-2">
        <ModelFields model={model} baseUrl={baseUrl} provider={s.provider} onModel={setModel} onUrl={setBaseUrl} disabled={blocked || act.busy} />
        <KeyInput value={value} onChange={setValue} hasKey={key.has_key} hint={key.hint} disabled={blocked || act.busy}
          emptyNote="저장하면 관리자도 다시 볼 수 없어요. 끝 4자리만 보여요" placeholder="회사 공급자 계정의 키" />
        <div className="grid grid-cols-[1fr_7rem] gap-3">
          <Field label="월 예산 (토큰)" note={status ? budgetKo(status.usage) : '이번 달 사용량을 불러오는 중…'}>
            <input className="field font-mono" inputMode="numeric" value={budget} onChange={(e) => setBudget(e.target.value)} disabled={blocked || act.busy} placeholder="비우면 예산 없음 · 예: 5000000" />
          </Field>
          <Field label="상한 %" note="여기 이르면 멈춰요">
            <input className="field font-mono" inputMode="numeric" value={cap} onChange={(e) => setCap(e.target.value)} disabled={blocked || act.busy} placeholder="100" />
          </Field>
        </div>
      </div>
      {status?.usage?.recording && status.usage.budget_tokens != null && (
        <Meter percent={status.usage.percent} label={budgetKo(status.usage)} cap={status.usage.limit_percent} capLabel={limitLineKo(status.usage)} />
      )}
      <div className="flex flex-wrap items-center gap-2">
        <button className="btn-primary px-5" disabled={blocked || act.busy}>{act.busy ? '저장 중…' : '저장'}</button>
        <button type="button" className="btn-ghost" onClick={clear} disabled={blocked || act.busy || !key.has_key}>키 지우기</button>
        <span className="flex items-center gap-1.5 text-xs text-ink-500"><PreparingChip /> 저장 전 연결 시험(1토큰 호출)은 서버 준비 중이에요</span>
      </div>
      <Result msg={act.msg} error={act.error} />
    </form>
  )
}

/** 개발 AI 코딩 에이전트: 회사 AI 모델 / 따로 설정 */
function CoderCard({ a, blocked, onDone }: { a: CompanyAiAdmin; blocked: boolean; onDone: () => void }) {
  const s = a.settings
  const key = a.keys.DEV_CODER_LLM_API_KEY
  const [mode, setMode] = useState<CoderMode>(s.coder.mode)
  const [model, setModel] = useState(s.coder.model)
  const [baseUrl, setBaseUrl] = useState(s.coder.base_url)
  const [value, setValue] = useState('')
  const act = useAct(onDone)
  const save = (e: FormEvent) => {
    e.preventDefault()
    if (mode === 'separate' && !model.trim()) return act.setError('따로 설정하면 모델 이름이 필요해요.')
    if (mode === 'separate' && urlNeeded(model, baseUrl, 'auto')) return act.setError('목록에 없는 모델이라 OpenAI 호환 주소가 필요해요.')
    const v = mode === 'separate' ? value.trim() : ''
    void act.run(async () => {
      const r = await saveCompanyAiSettings(settingsBody(s, { coder: { mode, model, base_url: baseUrl } }))
      const parts = [r.changed.length ? '코딩 에이전트 설정을 저장했어요.' : '설정은 그대로예요.']
      if (v) {
        parts.push(keyResultKo(await putCompanyAiKey('DEV_CODER_LLM_API_KEY', v), a.approval.admins))
        setValue('')
      }
      return parts.join(' ')
    })
  }
  const clear = () => {
    if (!window.confirm('코딩 에이전트 키를 지울까요?')) return
    void act.run(async () => keyResultKo(await deleteCompanyAiKey('DEV_CODER_LLM_API_KEY'), a.approval.admins))
  }
  return (
    <form onSubmit={save} className="card space-y-4 p-6">
      <SectionHead as="h3" title="개발 AI · 코딩 에이전트" desc="코드를 읽고 고치는 모델이에요. 회사 AI 모델로도 돌아가요. 사용량은 어느 쪽이든 회사 예산에 세요."
        right={
          <div className="flex overflow-hidden border border-ink-100" role="radiogroup" aria-label="코딩 에이전트 모델">
            {(['company', 'separate'] as const).map((m) => (
              <button key={m} type="button" role="radio" aria-checked={mode === m} disabled={blocked || act.busy} onClick={() => setMode(m)}
                className={`px-3 py-1.5 text-sm ${mode === m ? 'bg-brand-600 text-white' : 'bg-surface text-ink-700 hover:bg-ink-50'}`}>
                {m === 'company' ? '회사 AI 모델' : '따로 설정'}
              </button>
            ))}
          </div>
        } />
      {mode === 'separate' && (
        <div className="grid gap-4 md:grid-cols-2">
          <ModelFields model={model} baseUrl={baseUrl} provider="auto" onModel={setModel} onUrl={setBaseUrl} disabled={blocked || act.busy} />
          <KeyInput value={value} onChange={setValue} hasKey={key.has_key} hint={key.hint} disabled={blocked || act.busy}
            emptyNote="코딩 에이전트 전용 키예요" placeholder="코딩 공급자 계정의 키" />
        </div>
      )}
      <div className="flex flex-wrap items-center gap-2">
        <button className="btn-primary px-5" disabled={blocked || act.busy}>{act.busy ? '저장 중…' : '저장'}</button>
        {key.has_key && <button type="button" className="btn-ghost" onClick={clear} disabled={blocked || act.busy}>키 지우기</button>}
      </div>
      <Result msg={act.msg} error={act.error} />
    </form>
  )
}

/** 마케팅 AI 영상 생성: 키가 있어야 켜진다. 영상 키는 여기 한 곳에서만 넣는다 */
function VideoCard({ a, blocked, onDone }: { a: CompanyAiAdmin; blocked: boolean; onDone: () => void }) {
  const s = a.settings
  const key = a.keys.MARKETING_VIDEO_API_KEY
  const [provider, setProvider] = useState(s.video.provider)
  const [enabled, setEnabled] = useState(s.video.enabled)
  const [value, setValue] = useState('')
  const act = useAct(onDone)
  const view = videoView(key.has_key, s.video.enabled)
  const save = (e: FormEvent) => {
    e.preventDefault()
    const v = value.trim()
    void act.run(async () => {
      const r = await saveCompanyAiSettings(settingsBody(s, { video: { provider, enabled } }))
      const parts = [r.changed.length ? '영상 생성 설정을 저장했어요.' : '설정은 그대로예요.']
      if (v) {
        parts.push(keyResultKo(await putCompanyAiKey('MARKETING_VIDEO_API_KEY', v), a.approval.admins))
        setValue('')
      }
      return parts.join(' ')
    })
  }
  const clear = () => {
    if (!window.confirm('영상 생성 키를 지울까요? 지우면 영상 생성이 꺼져요.')) return
    void act.run(async () => keyResultKo(await deleteCompanyAiKey('MARKETING_VIDEO_API_KEY'), a.approval.admins))
  }
  return (
    <form onSubmit={save} className="card space-y-4 p-6">
      <SectionHead as="h3" title="마케팅 AI · 영상 생성" desc="영상 키는 여기 한 곳에서만 넣어요. 키가 있어야 켜져요."
        right={<StatusChip tone={view.tone}>영상 생성 {view.label}</StatusChip>} />
      <div className="grid gap-4 md:grid-cols-2">
        <Field label="영상 공급자" note="공급자 이름 (예: gemini · seedance · custom)">
          <input className="field font-mono" value={provider} onChange={(e) => setProvider(e.target.value)} disabled={blocked || act.busy} autoComplete="off" maxLength={40} />
        </Field>
        <KeyInput value={value} onChange={setValue} hasKey={key.has_key} hint={key.hint} disabled={blocked || act.busy}
          emptyNote="영상 공급자 계정의 키" placeholder="영상 공급자 키" />
      </div>
      <label className="flex items-center gap-2 text-sm">
        <input type="checkbox" checked={enabled} onChange={(e) => setEnabled(e.target.checked)} disabled={blocked || act.busy} />
        영상 생성 켜기 <span className="text-xs text-ink-500">(키가 없으면 켜도 돌지 않아요)</span>
      </label>
      <div className="flex flex-wrap items-center gap-2">
        <button className="btn-primary px-5" disabled={blocked || act.busy}>{act.busy ? '저장 중…' : '저장'}</button>
        <button type="button" className="btn-ghost" onClick={clear} disabled={blocked || act.busy || !key.has_key}>키 지우기</button>
      </div>
      <Result msg={act.msg} error={act.error} />
    </form>
  )
}

type Act = ReturnType<typeof useAct>

function RequestRow({ r, me, blocked, act }: { r: KeyRequest; me: string; blocked: boolean; act: Act }) {
  const mine = !canApprove(r, me)
  return (
    <li className="flex flex-wrap items-center gap-2 py-3 text-sm">
      <StatusChip tone="wait">승인 대기</StatusChip>
      <span className="font-medium">{KEY_LABEL[r.name]} {OP_KO[r.op] ?? r.op}</span>
      {r.hint && <span className="font-mono text-ink-700">{keyHintKo(r.hint)}</span>}
      <span className="text-xs text-ink-500">{r.requested_by || '—'} · {whenKo(r.requested_at)}{r.reason ? ` · ${r.reason}` : ''}</span>
      <span className="ml-auto flex flex-wrap items-center gap-1.5">
        {mine ? (
          <>
            <span className="text-xs text-ink-500">내 요청 · 다른 관리자 승인을 기다려요</span>
            <button type="button" className="btn-quiet" disabled={blocked || act.busy} onClick={() => void act.run(async () => { await rejectKeyRequest(r.id); return '요청을 거뒀어요.' })}>요청 취소</button>
          </>
        ) : (
          <>
            <button type="button" className="btn-primary px-4" disabled={blocked || act.busy} onClick={() => void act.run(async () => { await approveKeyRequest(r.id); return `${KEY_LABEL[r.name]} ${OP_KO[r.op] ?? r.op}을 승인해서 적용했어요.` })}>승인</button>
            <button type="button" className="btn-ghost" disabled={blocked || act.busy} onClick={() => void act.run(async () => { await rejectKeyRequest(r.id); return '요청을 거절했어요.' })}>거절</button>
          </>
        )}
      </span>
    </li>
  )
}

/** 공통 AI 도구 연결 탭 전체 */
export default function CompanyAiTools({ me }: { me: string }) {
  const [a, setA] = useState<CompanyAiAdmin | null>(null)
  const [error, setError] = useState<string | null>(null)
  const [status, setStatus] = useState<AiStatus | null>(null)
  const [statusError, setStatusError] = useState<string | null>(null)
  const reload = useCallback(() => {
    getCompanyAi().then((x) => { setA(x); setError(null) }).catch((e) => setError(errText(e)))
    getAiStatus().then((x) => { setStatus(x); setStatusError(null) }).catch((e) => setStatusError(errText(e)))
  }, [])
  useEffect(reload, [reload])
  const reqAct = useAct(reload)

  if (!a) {
    return error
      ? <Notice tone="bad">회사 AI 설정을 불러오지 못했어요: {error}</Notice>
      : <p className="text-sm text-ink-500">불러오는 중…</p>
  }
  const blocked = saveBlockReason(a)
  return (
    <div className="space-y-6">
      {error && <Notice tone="bad">다시 불러오지 못했어요: {error}</Notice>}
      {blocked && <Notice tone="bad">저장할 수 없어요: {blocked}</Notice>}
      {statusError && <Notice tone="wait">이번 달 사용량을 받지 못했어요: {statusError}</Notice>}
      {!a.usage_recording && <Notice tone="wait">사용량 기록이 꺼져 있어 사용 %가 0으로 보여요 (서버 설정 AX_LLM_USAGE_DB 필요).</Notice>}
      <Notice tone="info">
        {a.approval.needed
          ? `키 교체·삭제는 다른 관리자가 승인하면 적용돼요(관리자 ${a.approval.admins}명). 처음 등록과 모델·예산 변경은 바로 적용하고 회원 모두에게 알려요.`
          : '관리자가 한 명뿐이라 키 교체·삭제도 바로 적용되고 회원 모두에게 알려요.'}
      </Notice>

      <div className="space-y-6">
        <CompanyModelCard a={a} status={status} blocked={!!blocked} onDone={reload} />
        <CoderCard a={a} blocked={!!blocked} onDone={reload} />
        <VideoCard a={a} blocked={!!blocked} onDone={reload} />
      </div>
      <p className="text-sm text-ink-500">그 밖의 AI 직원은 모두 회사 AI 모델을 써요. 직원별 모델 선택은 원하는 고객사가 생기면 추가해요.</p>

      <section className="card p-6">
        <SectionHead as="h3" title="승인 대기 요청" desc="키 교체·삭제 요청이에요. 요청한 사람은 스스로 승인할 수 없어요." />
        {a.requests.length ? (
          <ul className="mt-2 divide-y divide-ink-100">
            {a.requests.map((r) => <RequestRow key={r.id} r={r} me={me} blocked={!!blocked} act={reqAct} />)}
          </ul>
        ) : <p className="mt-3 text-sm text-ink-500">기다리는 요청이 없어요.</p>}
        <div className="mt-2"><Result msg={reqAct.msg} error={reqAct.error} /></div>
      </section>

      <section className="card p-6">
        <SectionHead as="h3" title="최근 변경 기록" desc="누가 언제 무엇을 바꿨는지예요. 키는 끝 4자리만 남아요." />
        {a.history.length ? (
          <ul className="mt-3 space-y-1.5 text-sm">
            {a.history.map((h, i) => (
              <li key={`${h.at}-${i}`} className="flex gap-3">
                <span className="w-28 shrink-0 text-xs leading-5 text-ink-500">{whenKo(h.at)}</span>
                <span className="text-ink-900">{historyLineKo(h)}</span>
              </li>
            ))}
          </ul>
        ) : <p className="mt-3 text-sm text-ink-500">아직 바꾼 기록이 없어요.</p>}
      </section>
    </div>
  )
}
