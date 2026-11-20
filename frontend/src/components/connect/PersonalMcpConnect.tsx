import { useCallback, useEffect, useState, type FormEvent, type ReactNode } from 'react'
import { Notice, SectionHead, StatusChip } from './parts'
import { DEV_API } from '@/lib/planning'
import { errText } from '@/lib/companyAiApi'
import {
  MCP_AGENTS, MCP_CLIENTS, mcpSetup, mineBlockReason, mineStateView, shareBody, shareUsageKo, toggleAgent,
  type McpAgentId, type McpClient, type PersonalMcp,
} from '@/lib/personalMcp'
import { disconnectMcp, getMyMcp, issueMcpToken, reissueMcpToken, saveMyMcpSettings } from '@/lib/personalMcpApi'

// 시스템 관리 › 도구 연결 › 내 연결 (예진 결정 11/20, 회사 AI 연결 설계 9장).
// 내 AI 도구(Claude Code·Codex·Cursor)가 MCP로 AXcellent에 붙어 AI 직원에게 일을 시킨다. 모델·비용은 내 도구 몫이라
// 키·모델·주소는 받지 않는다. 순서: ① 연결 토큰 ② 쓸 AI 직원 ③ 한도(회사 월 예산 중 내 몫 %) ④ 연결 방법 ⑤ 저장.
// 값은 dashboard-api `/me/ai-connection`에서만 받는다. 토큰 원문은 발급 순간 한 번만 보이고 그 뒤로는 끝 4자리만.
// MCP 주소는 예전 개발자 PC 연결 안내와 같은 개발 서비스 MCP 서버(`/api/planning/mcp`).

// 개발 서버 프록시 주소(/dev-api)처럼 앞이 빠진 주소면 지금 화면 주소를 앞에 붙인다 (명령에 그대로 붙여 넣을 수 있게)
const MCP_URL = new URL(`${DEV_API.replace(/\/$/, '')}/api/planning/mcp`, window.location.href).href
const ASK = 'axcellent-dev에서 작업 목록 보여 줘. 하나 가져가서 명세대로 구현하고 PR 올려 줘.'

function CopyBox({ text, dashed }: { text: string; dashed?: boolean }) {
  const [done, setDone] = useState(false)
  const copy = () => {
    navigator.clipboard?.writeText(text).then(() => { setDone(true); setTimeout(() => setDone(false), 1500) }).catch(() => {})
  }
  return (
    <div className="flex items-start gap-2">
      <code className={`mono min-w-0 flex-1 whitespace-pre-wrap break-all rounded-md px-3 py-2 text-xs ${dashed ? 'border border-dashed border-brand-600 bg-surface' : 'bg-ink-50'}`}>{text}</code>
      <button type="button" className="btn-ghost shrink-0 px-2 py-1 text-xs" onClick={copy}>{done ? '복사됨' : '복사'}</button>
    </div>
  )
}

function Step({ n, title, children }: { n: number; title: string; children: ReactNode }) {
  return (
    <section className="space-y-3">
      <h3 className="flex items-center gap-2 text-[15px] font-semibold">
        <span aria-hidden="true" className="grid h-5 w-5 place-items-center rounded-full bg-brand-600 text-[11px] !text-white">{n}</span>
        {title}
      </h3>
      {children}
    </section>
  )
}

function Meter({ percent, label }: { percent: number | null; label: string }) {
  const w = Math.max(0, Math.min(100, percent ?? 0))
  return (
    <div className="space-y-1">
      <div className="h-2 overflow-hidden rounded-full bg-ink-100" role="meter" aria-valuemin={0} aria-valuemax={100} aria-valuenow={Math.round(w)} aria-label="이번 달 내 몫 사용">
        <div className={`h-full ${w >= 100 ? 'bg-red-700' : w >= 80 ? 'bg-amber-600' : 'bg-brand-600'}`} style={{ width: `${w}%` }} />
      </div>
      <p className="text-xs text-ink-500">{label}</p>
    </div>
  )
}

function Form({ mine, onDone }: { mine: PersonalMcp; onDone: () => void }) {
  const s = mine.settings
  const [agents, setAgents] = useState<McpAgentId[]>(s.agents)
  const [share, setShare] = useState(s.monthly_share_percent == null ? '' : String(s.monthly_share_percent))
  const [client, setClient] = useState<McpClient>('claude')
  const [fresh, setFresh] = useState<string | null>(null) // 방금 발급한 원문. 다 넣었어요를 누르면 버린다
  const [busy, setBusy] = useState(false)
  const [msg, setMsg] = useState<string | null>(null)
  const [error, setError] = useState<string | null>(null)
  const blocked = mineBlockReason(mine)
  const off = !!blocked || busy
  const st = mineStateView(mine)
  const u = mine.usage

  const run = async (f: () => Promise<string>) => {
    setBusy(true)
    setMsg(null)
    setError(null)
    try { setMsg(await f()) } catch (e) { setError(errText(e)) } finally { setBusy(false); onDone() }
  }
  const issue = (again: boolean) => {
    if (again && !window.confirm('다시 발급할까요? 지금 토큰은 바로 쓸 수 없게 돼요. 내 AI 도구 설정의 토큰도 바꿔 넣어야 해요.')) return
    void run(async () => {
      const got = again ? await reissueMcpToken() : await issueMcpToken()
      setFresh(got.token)
      return again ? `다시 발급했어요 (••••${got.hint}). 옛 토큰은 이제 안 돼요.` : `발급했어요 (••••${got.hint}).`
    })
  }
  const disconnect = () => {
    if (!window.confirm('연결을 끊을까요? 내 AI 도구의 MCP 연결이 바로 막혀요.')) return
    void run(async () => { await disconnectMcp(); setFresh(null); return '연결을 끊었어요.' })
  }
  const save = (e: FormEvent) => {
    e.preventDefault()
    const body = shareBody({ share, agents })
    if (body === 'invalid') return setError('내 몫은 1~100 사이 정수(%)로 넣어 주세요. 비우면 제한 없음이에요.')
    void run(async () => {
      const r = await saveMyMcpSettings(body)
      return r.changed.length ? '저장했어요.' : '바뀐 것이 없어요.'
    })
  }

  return (
    <form onSubmit={save} className="card space-y-6 p-6">
      {blocked && <Notice tone="wait">{blocked}</Notice>}

      <Step n={1} title="내 AI 도구로 AXcellent 쓰기">
        <div className="flex flex-wrap items-center gap-2">
          <StatusChip tone={st.tone}>{st.label}</StatusChip>
          {mine.token.issued ? (
            <>
              <button type="button" className="btn-ghost" onClick={() => issue(true)} disabled={off}>다시 발급</button>
              <button type="button" className="btn-ghost" onClick={disconnect} disabled={off}>끊기</button>
            </>
          ) : (
            <button type="button" className="btn-primary px-4" onClick={() => issue(false)} disabled={off}>연결 토큰 발급</button>
          )}
        </div>
        {fresh && (
          <div className="space-y-2">
            <p className="text-sm font-medium text-ink-900">지금 한 번만 보여요. 복사해서 아래 연결 방법의 <code className="mono">axm_…</code> 자리에 넣어 주세요.</p>
            <CopyBox text={fresh} dashed />
            <button type="button" className="btn-ghost" onClick={() => setFresh(null)}>다 넣었어요</button>
          </div>
        )}
        <p className="text-xs text-ink-500">로그인한 나만 발급하고 끊을 수 있어요. 서버에는 토큰 대신 해시만 남아요. 잃어버리면 다시 발급해 주세요.</p>
      </Step>

      <Step n={2} title="쓸 AI 직원">
        <p className="text-xs text-ink-500">고른 AI 직원만 이 토큰으로 부를 수 있어요. 고르지 않은 직원은 막혀요.</p>
        <div className="flex flex-wrap gap-2">
          {MCP_AGENTS.map((a) => {
            const on = agents.includes(a.id)
            return (
              <label key={a.id} className={`chip cursor-pointer ${on ? 'bg-brand-600 !text-white' : 'bg-ink-100 text-ink-700'} ${off ? 'opacity-60' : ''}`}>
                <input type="checkbox" className="sr-only" checked={on} disabled={off} onChange={() => setAgents((cur) => toggleAgent(cur, a.id))} />
                {a.name}
              </label>
            )
          })}
        </div>
        <p className="text-xs text-ink-500">지금 MCP 서버가 여는 도구는 개발 AI 작업 대기열(작업 보기·가져가기·진행·PR 알리기)이에요. 다른 AI 직원 도구는 준비 중이에요.</p>
      </Step>

      <Step n={3} title="한도">
        <label className="block max-w-xs space-y-1 text-sm">
          <span className="text-ink-700">회사 월 예산 중 내 몫 (%)</span>
          <input className="field font-mono" inputMode="numeric" value={share} onChange={(e) => setShare(e.target.value)} disabled={off} placeholder="비우면 제한 없음 · 예: 20" />
        </label>
        {u?.recording && u.percent_of_limit != null
          ? <Meter percent={u.percent_of_limit} label={shareUsageKo(u)} />
          : <p className="text-xs text-ink-500">{shareUsageKo(u)}</p>}
        <p className="text-xs text-ink-500">MCP로 시킨 일이 쓴 토큰만 세요(실제 기록). 넘으면 MCP 요청이 막히고 이유가 내 AI 도구에 돌아가요. 회사 상한에 이르러도 막혀요.</p>
      </Step>

      <Step n={4} title="연결 방법">
        <div className="inline-flex overflow-hidden rounded-lg border border-ink-100" role="group" aria-label="AI 도구">
          {MCP_CLIENTS.map((c) => (
            <button key={c.id} type="button" aria-pressed={client === c.id} onClick={() => setClient(c.id)}
              className={`px-3 py-1 text-xs ${client === c.id ? 'bg-brand-600 !text-white' : 'text-ink-700 hover:bg-ink-50'}`}>
              {c.label}
            </button>
          ))}
        </div>
        <ol className="list-decimal space-y-3 pl-5 text-sm">
          {mcpSetup(client, MCP_URL).map((s) => (
            <li key={s.label} className="space-y-1">
              <span className="flex flex-wrap items-center gap-2">{s.label}{s.note && <span className="chip bg-ink-100 text-ink-500">{s.note}</span>}</span>
              <CopyBox text={s.text} />
            </li>
          ))}
          <li className="space-y-1"><span>레포 폴더에서 써 보기</span><CopyBox text={ASK} /></li>
        </ol>
        <p className="text-xs text-ink-500">토큰은 내 PC 설정에만 넣어요. 토큰이 든 설정 파일(.mcp.json 등)을 레포에 커밋하지 않아요.</p>
      </Step>

      <div className="flex flex-wrap items-center gap-2">
        <button className="btn-primary px-5" disabled={off}>{busy ? '저장 중…' : '저장'}</button>
        {error ? <p role="alert" className="text-sm text-red-700">{error}</p> : msg ? <p role="status" className="text-sm text-emerald-700">{msg}</p> : null}
      </div>
    </form>
  )
}

/** 도구 연결 › 내 연결 › 개인 MCP 연결 */
export default function PersonalMcpConnect() {
  const [mine, setMine] = useState<PersonalMcp | null>(null)
  const [error, setError] = useState<string | null>(null)
  const load = useCallback(() => {
    getMyMcp().then((m) => { setMine(m); setError(null) }).catch((e) => setError(errText(e)))
  }, [])
  useEffect(load, [load])
  return (
    <section className="space-y-3">
      <SectionHead title="내 연결"
        desc="내 AI 도구(Claude Code·Codex·Cursor)가 MCP로 AXcellent에 붙어 AI 직원에게 일을 시켜요. 모델·비용은 내 도구 몫이에요. 나만 보이고 나만 바꿀 수 있어요." />
      {mine ? <Form mine={mine} onDone={load} />
        : error ? <Notice tone="bad">내 연결을 받지 못했어요: {error}</Notice>
        : <p className="text-sm text-ink-500">불러오는 중…</p>}
    </section>
  )
}
