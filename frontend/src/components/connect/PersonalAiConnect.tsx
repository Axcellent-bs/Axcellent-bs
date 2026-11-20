import { useCallback, useEffect, useState, type FormEvent, type ReactNode } from 'react'
import { Notice, SectionHead, StatusChip } from './parts'
import { providerToSave, providerView } from '@/lib/companyAi'
import { errText } from '@/lib/companyAiApi'
import {
  PERSONAL_AGENTS, mineBlockReason, mineStateView, personalBody, personalUsageKo, toggleAgent,
  type PersonalAgentId, type PersonalAi,
} from '@/lib/personalAi'
import { deleteMyAiKey, getMyAi, putMyAiKey, saveMyAiSettings } from '@/lib/personalAiApi'

// 시스템 관리 › 도구 연결 › 내 연결 › "내 AI 도구로 AXcellent 쓰기" (예진 결정 11/20, 회사 AI 연결 설계 9장).
// 입력은 API 키·모델·주소(목록에 없는 모델·OpenAI 호환일 때만)·월 한도(토큰), 그리고 내 키로 부를 AI 직원.
// 고른 AI 직원에게 **내가** 일을 시킬 때 회사 키 대신 내 키로 불러요. 나만 보이고 나만 바꿔요(관리자도 못 봐요).
// 값은 dashboard-api `/me/ai-connection`에서만 받는다. 키는 저장 뒤 끝 4자리만.

function Field({ label, note, children }: { label: string; note?: ReactNode; children: ReactNode }) {
  return (
    <label className="block space-y-1 text-sm">
      <span className="text-ink-700">{label}</span>
      {children}
      {note && <span className="block text-xs text-ink-500">{note}</span>}
    </label>
  )
}

function Meter({ percent, label }: { percent: number | null; label: string }) {
  const w = Math.max(0, Math.min(100, percent ?? 0))
  return (
    <div className="space-y-1">
      <div className="h-2 overflow-hidden rounded-full bg-ink-100" role="meter" aria-valuemin={0} aria-valuemax={100} aria-valuenow={Math.round(w)} aria-label="이번 달 내 키 사용">
        <div className={`h-full ${w >= 100 ? 'bg-red-700' : w >= 80 ? 'bg-amber-600' : 'bg-brand-600'}`} style={{ width: `${w}%` }} />
      </div>
      <p className="text-xs text-ink-500">{label}</p>
    </div>
  )
}

function Form({ mine, onDone, msg, setMsg }: { mine: PersonalAi; onDone: () => void; msg: string | null; setMsg: (m: string | null) => void }) {
  const s = mine.settings
  const [value, setValue] = useState('')
  const [model, setModel] = useState(s.model)
  const [baseUrl, setBaseUrl] = useState(s.base_url)
  const [limit, setLimit] = useState(s.monthly_limit_tokens == null ? '' : String(s.monthly_limit_tokens))
  const [agents, setAgents] = useState<PersonalAgentId[]>(s.agents)
  const [busy, setBusy] = useState(false)
  const [error, setError] = useState<string | null>(null)
  const blocked = mineBlockReason(mine)
  const off = !!blocked || busy
  const view = providerView(model, providerToSave(model, baseUrl, s.provider), baseUrl)
  const st = mineStateView(mine)
  const u = mine.usage

  const run = async (f: () => Promise<string>) => {
    setBusy(true)
    setMsg(null)
    setError(null)
    try { setMsg(await f()) } catch (e) { setError(errText(e)) } finally { setBusy(false); onDone() }
  }
  const save = (e: FormEvent) => {
    e.preventDefault()
    const body = personalBody({ model, base_url: view.needsUrl ? baseUrl : '', limit, agents })
    if (body === 'invalid') return setError('월 한도는 0 이상 정수(토큰)로 넣어 주세요. 비우면 한도 없음이에요.')
    if (view.needsUrl && !body.base_url) return setError('목록에 없는 모델이라 OpenAI 호환 주소가 필요해요.')
    if (!value.trim() && !mine.key.has_key && agents.length) return setError('AI 직원을 고르려면 API 키를 넣어 주세요.')
    const v = value.trim()
    void run(async () => {
      const r = await saveMyAiSettings(body)
      const parts = [r.changed.length ? '설정을 저장했어요.' : '설정은 그대로예요.']
      if (v) {
        parts.push(`키를 저장했어요 (••••${await putMyAiKey(v)}).`)
        setValue('')
      }
      return parts.join(' ')
    })
  }
  const clear = () => {
    if (!window.confirm('내 API 키를 지울까요? 그 뒤로는 고른 AI 직원도 회사 키로 일해요.')) return
    void run(async () => { await deleteMyAiKey(); return '키를 지웠어요.' })
  }

  return (
    <form onSubmit={save} className="card space-y-5 p-6">
      <div className="flex flex-wrap items-center gap-2">
        <StatusChip tone={st.tone}>{st.label}</StatusChip>
        {mine.key.has_key && s.agents.map((id) => (
          <span key={id} className="chip bg-brand-50 text-brand-700">{PERSONAL_AGENTS.find((a) => a.id === id)?.name ?? id}</span>
        ))}
        {mine.key.has_key && !s.agents.length && <span className="chip bg-ink-100 text-ink-500">고른 AI 직원 없음</span>}
      </div>
      {mine.key.has_key && (u?.limit_tokens != null && u.recording
        ? <Meter percent={u.percent} label={personalUsageKo(u)} />
        : <p className="text-xs text-ink-500">{personalUsageKo(u)}</p>)}
      {blocked && <Notice tone="wait">{blocked}</Notice>}

      <div className="grid gap-4 md:grid-cols-2">
        <Field label="API 키" note={mine.key.has_key ? `저장된 키 ••••${mine.key.hint} · 새로 넣으면 바꿔요` : '저장하면 다시 볼 수 없어요. 끝 4자리만 보여요'}>
          <input className="field font-mono" type="password" autoComplete="off" value={value} onChange={(e) => setValue(e.target.value)} disabled={off}
            placeholder={mine.key.has_key ? '비우면 지금 키 그대로' : '내 공급자 계정의 키'} />
        </Field>
        <Field label="모델" note={view.label}>
          <input className="field font-mono" value={model} onChange={(e) => setModel(e.target.value)} disabled={off} autoComplete="off" placeholder="내 공급자 계정의 모델 이름" maxLength={200} />
        </Field>
        {view.needsUrl && (
          <Field label="주소" note="목록에 없는 모델 · OpenAI 호환 서버 주소">
            <input className="field font-mono" value={baseUrl} onChange={(e) => setBaseUrl(e.target.value)} disabled={off} autoComplete="off" placeholder="https://…/v1" maxLength={500} />
          </Field>
        )}
        <Field label="월 한도 (토큰)" note="내 키로 부른 만큼만 세요. 회사 월 예산에는 세지 않아요">
          <input className="field font-mono" inputMode="numeric" value={limit} onChange={(e) => setLimit(e.target.value)} disabled={off} placeholder="비우면 한도 없음 · 예: 1000000" />
        </Field>
      </div>

      <fieldset className="space-y-2">
        <legend className="text-sm text-ink-700">내 키로 부를 AI 직원</legend>
        <p className="text-xs text-ink-500">고른 AI 직원에게 내가 일을 시킬 때만 회사 키 대신 내 키로 불러요. 다른 사람이 시킨 일은 회사 키로 돌아요.</p>
        <div className="flex flex-wrap gap-2">
          {PERSONAL_AGENTS.map((a) => {
            const on = agents.includes(a.id)
            return (
              <label key={a.id} className={`chip cursor-pointer ${on ? 'bg-brand-600 !text-white' : 'bg-ink-100 text-ink-700'} ${off ? 'opacity-60' : ''}`}>
                <input type="checkbox" className="sr-only" checked={on} disabled={off} onChange={() => setAgents((cur) => toggleAgent(cur, a.id))} />
                {a.name}
              </label>
            )
          })}
        </div>
      </fieldset>

      <div className="flex flex-wrap items-center gap-2">
        <button className="btn-primary px-5" disabled={off}>{busy ? '저장 중…' : '저장'}</button>
        <button type="button" className="btn-ghost" onClick={clear} disabled={off || !mine.key.has_key}>키 지우기</button>
      </div>
      {error ? <p role="alert" className="text-sm text-red-700">{error}</p> : msg ? <p role="status" className="text-sm text-emerald-700">{msg}</p> : null}
      <ul className="list-disc space-y-0.5 pl-5 text-xs text-ink-500">
        <li>내 키 호출이 실패하면 회사 키로 바꾸지 않고 멈춰요. 키·한도를 확인해 주세요.</li>
        <li>실제로 내 키로 부르는 부분은 서버(공통 AI 호출부)에 붙는 중이에요. 그 전에는 저장만 되고 사용량은 0으로 보여요.</li>
      </ul>
    </form>
  )
}

/** 도구 연결 › 내 연결 › 내 AI 도구로 AXcellent 쓰기 */
export default function PersonalAiConnect() {
  const [mine, setMine] = useState<PersonalAi | null>(null)
  const [error, setError] = useState<string | null>(null)
  const [msg, setMsg] = useState<string | null>(null) // 저장 뒤 폼이 새 값으로 다시 그려져도 결과 문장은 남긴다
  const load = useCallback(() => {
    getMyAi().then((m) => { setMine(m); setError(null) }).catch((e) => setError(errText(e)))
  }, [])
  useEffect(load, [load])
  return (
    <section className="space-y-3">
      <SectionHead title="내 AI 도구로 AXcellent 쓰기"
        desc="내 API 키로 AI 직원을 불러요. 고른 AI 직원에게 내가 일을 시킬 때만 회사 키 대신 내 키를 써요. 나만 보이고 나만 바꿀 수 있어요(관리자도 못 봐요)." />
      {mine ? <Form key={`${mine.key.hint}-${mine.settings.updated_at ?? ''}`} mine={mine} onDone={load} msg={msg} setMsg={setMsg} />
        : error ? <Notice tone="bad">내 AI 연결을 받지 못했어요: {error}</Notice>
        : <p className="text-sm text-ink-500">불러오는 중…</p>}
    </section>
  )
}
