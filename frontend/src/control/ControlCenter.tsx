// AXcellent 관제 센터 (AXcellent 운영팀 전용, 고객사 계정은 못 연다). 고객사에 설치된 AXcellent가 보내는 상태 신호만 모아 본다.
// 탭: 전체 현황 · 고객사 · 장애·사건 · AI 품질 · 비용·사용량 · 연결 키 발급. 숫자는 data.ts의 데모 예시다.
import { useState, type ReactNode } from 'react'
import { useLocation } from 'react-router'
import { COMPANIES, COST_BY_SERVICE, COST_TREND, INCIDENTS, LATEST_VERSION, QUALITY, won, type Health, type Origin } from './data'
import KeyIssue from './KeyIssue'
import { useCompany, useKeys } from '@/demo/company'

const HEALTH: Record<Health, { label: string; tone: string; dot: string }> = {
  ok: { label: '정상', tone: 'bg-emerald-50 text-emerald-700', dot: 'bg-emerald-700' },
  warn: { label: '경고', tone: 'bg-amber-50 text-amber-800', dot: 'bg-amber-700' },
  down: { label: '연결 끊김', tone: 'bg-red-50 text-red-700', dot: 'bg-red-700' },
}
const ORIGIN_TONE: Record<Origin, string> = { 우리: 'bg-brand-50 text-brand-700', 고객사: 'bg-ink-100 text-ink-700', 외부: 'bg-amber-50 text-amber-800' }
const SEV_TONE = { 높음: 'bg-red-50 text-red-700', 보통: 'bg-amber-50 text-amber-800', 낮음: 'bg-ink-100 text-ink-700' } as const
const STATUS_TONE = { '대응 중': 'text-red-700', '안내 완료': 'text-amber-800', 해결: 'text-emerald-700' } as const

const ago = (m: number) => (m === 0 ? '방금' : m < 60 ? `${m}분 전` : `${Math.floor(m / 60)}시간 전`)

function Page({ title, desc, children }: { title: string; desc: string; children: ReactNode }) {
  return (
    <div className="mx-auto max-w-6xl space-y-6 px-8 pb-12">
      <header className="flex flex-wrap items-end justify-between gap-3">
        <div>
          <p className="text-sm text-ink-500">관제 센터</p>
          <h1 className="text-2xl font-semibold">{title}</h1>
          <p className="mt-1 text-sm text-ink-500">{desc}</p>
        </div>
        <span className="chip bg-ink-100 text-ink-700" title="업무 데이터(주문·문의·인사 기록)는 받지 않아요">받는 범위: 상태 신호만</span>
      </header>
      {children}
    </div>
  )
}

function Tile({ label, value, note, tone = 'text-ink-900' }: { label: string; value: ReactNode; note?: string; tone?: string }) {
  return (
    <div className="card px-5 py-4">
      <p className="text-xs text-ink-500">{label}</p>
      <p className={`mt-1 text-3xl font-bold tabular-nums ${tone}`}>{value}</p>
      {note && <p className="mt-1 text-xs text-ink-500">{note}</p>}
    </div>
  )
}

function HealthChip({ h }: { h: Health }) {
  return (
    <span className={`chip ${HEALTH[h].tone}`}>
      <span className={`h-1.5 w-1.5 ${HEALTH[h].dot}`} aria-hidden="true" />
      {HEALTH[h].label}
    </span>
  )
}

/** 한 값짜리 막대 (0~max). 텍스트는 막대 색이 아니라 글자 색으로 */
function Meter({ value, max, label }: { value: number; max: number; label: string }) {
  return (
    <div className="flex items-center gap-3" title={label}>
      <div className="h-2 flex-1 bg-ink-100" aria-hidden="true">
        <div className="h-2 bg-brand-600" style={{ width: `${Math.max(2, (value / max) * 100)}%` }} />
      </div>
      <span className="w-20 shrink-0 text-right text-sm tabular-nums text-ink-700">{label}</span>
    </div>
  )
}

function IncidentTable({ rows }: { rows: typeof INCIDENTS }) {
  return (
    <div className="card overflow-x-auto">
      <table className="w-full min-w-[760px] text-sm">
        <thead className="bg-app text-left text-xs text-ink-500">
          <tr>
            <th className="px-5 py-3 font-medium">사건</th>
            <th className="px-3 py-3 font-medium">고객사</th>
            <th className="px-3 py-3 font-medium">서비스</th>
            <th className="px-3 py-3 font-medium">원인 쪽</th>
            <th className="px-3 py-3 font-medium">심각도</th>
            <th className="px-3 py-3 font-medium">상태</th>
            <th className="px-5 py-3 font-medium">시작</th>
          </tr>
        </thead>
        <tbody className="divide-y divide-ink-100">
          {rows.map((r) => (
            <tr key={r.id}>
              <td className="px-5 py-3">
                <p className="font-medium">{r.title}</p>
                <p className="mt-0.5 text-xs text-ink-500">{r.id} · {r.note}</p>
              </td>
              <td className="px-3 py-3">{r.company}</td>
              <td className="px-3 py-3 text-ink-700">{r.service}</td>
              <td className="px-3 py-3"><span className={`chip ${ORIGIN_TONE[r.origin]}`}>{r.origin}</span></td>
              <td className="px-3 py-3"><span className={`chip ${SEV_TONE[r.severity]}`}>{r.severity}</span></td>
              <td className={`px-3 py-3 font-medium ${STATUS_TONE[r.status]}`}>{r.status}</td>
              <td className="px-5 py-3 tabular-nums text-ink-500">{r.startedAt}</td>
            </tr>
          ))}
        </tbody>
      </table>
    </div>
  )
}

// ---------------------------------------------------------------- 탭
function Overview() {
  const count = (h: Health) => COMPANIES.filter((c) => c.health === h).length
  const behind = COMPANIES.filter((c) => c.version !== LATEST_VERSION)
  const open = INCIDENTS.filter((i) => i.status !== '해결')
  const attention = COMPANIES.filter((c) => c.health !== 'ok')
  const versions = [...new Set(COMPANIES.map((c) => c.version))].sort().reverse()
  return (
    <Page title="전체 현황" desc="고객사에 설치된 AXcellent가 잘 돌고 있는지 한눈에 봐요.">
      <div className="grid gap-3 sm:grid-cols-2 lg:grid-cols-4">
        <Tile label="연결된 고객사" value={COMPANIES.length} note={`정상 ${count('ok')} · 경고 ${count('warn')} · 끊김 ${count('down')}`} />
        <Tile label="대응 중인 사건" value={open.length} note={`오늘 생긴 사건 ${INCIDENTS.length}건`} tone={open.length ? 'text-red-700' : 'text-ink-900'} />
        <Tile label="업데이트가 밀린 고객사" value={behind.length} note={`최신 ${LATEST_VERSION}`} tone={behind.length ? 'text-amber-800' : 'text-ink-900'} />
        <Tile label="오늘 AI 자동 처리율" value={`${Math.round(COMPANIES.reduce((a, c) => a + c.autoRate, 0) / COMPANIES.length)}%`} note="사람 승인 없이 끝난 비율, 고객사 평균" />
      </div>

      <div className="grid gap-4 lg:grid-cols-[1.4fr_1fr]">
        <section className="card p-5">
          <h2 className="card-title">살펴볼 고객사</h2>
          <ul className="mt-3 divide-y divide-ink-100">
            {attention.map((c) => (
              <li key={c.id} className="flex flex-wrap items-center gap-3 py-3">
                <HealthChip h={c.health} />
                <span className="font-medium">{c.name}</span>
                <span className="text-xs text-ink-500">{c.industry}</span>
                <span className="ml-auto text-xs text-ink-500">마지막 신호 {ago(c.lastSeenMin)} · 사건 {c.incidentsToday}건 · {c.version}</span>
              </li>
            ))}
          </ul>
        </section>
        <section className="card p-5">
          <h2 className="card-title">버전 분포</h2>
          <div className="mt-4 space-y-3">
            {versions.map((v) => {
              const n = COMPANIES.filter((c) => c.version === v).length
              return (
                <div key={v} className="grid grid-cols-[64px_1fr] items-center gap-3 text-sm">
                  <span className="font-mono text-xs text-ink-700">{v}{v === LATEST_VERSION ? ' 최신' : ''}</span>
                  <Meter value={n} max={COMPANIES.length} label={`${n}곳`} />
                </div>
              )
            })}
          </div>
        </section>
      </div>

      <section className="space-y-3">
        <h2 className="card-title">대응 중인 사건</h2>
        <IncidentTable rows={open} />
      </section>
    </Page>
  )
}

function Companies() {
  const current = useCompany()
  const keys = useKeys()
  // 키로 새로 연결한 회사(예시 목록에 없는 회사)는 맨 위에 붙인다
  const fresh = keys
    .filter((k) => k.status === 'connected' && !COMPANIES.some((c) => c.id === k.company.id))
    .map((k) => ({ ...COMPANIES[4], ...k.company, version: LATEST_VERSION, lastSeenMin: 0, health: 'ok' as Health, pcs: 1, services: 4, incidentsToday: 0 }))
  const rows = [...fresh, ...COMPANIES]
  return (
    <Page title="고객사" desc="회사별 설치 버전, 켠 서비스, 마지막 연결 시각이에요.">
      <p className="bg-brand-50 px-4 py-3 text-sm text-brand-700">
        연결은 고객사 시스템 관리자가 발급받은 키를 넣어서 해요(연결 키 발급 탭). 이 데모에 지금 연결된 회사: <b>{current?.name ?? '없음'}</b>
      </p>
      <div className="card overflow-x-auto">
        <table className="w-full min-w-[920px] text-sm">
          <thead className="bg-app text-left text-xs text-ink-500">
            <tr>
              <th className="px-5 py-3 font-medium">고객사</th>
              <th className="px-3 py-3 font-medium">상태</th>
              <th className="px-3 py-3 font-medium">버전</th>
              <th className="px-3 py-3 font-medium">마지막 신호</th>
              <th className="px-3 py-3 text-right font-medium">PC</th>
              <th className="px-3 py-3 text-right font-medium">켠 서비스</th>
              <th className="px-3 py-3 text-right font-medium">오늘 사건</th>
            </tr>
          </thead>
          <tbody className="divide-y divide-ink-100">
            {rows.map((c) => (
              <tr key={c.id} className={current?.id === c.id ? 'bg-brand-50' : ''}>
                <td className="px-5 py-3">
                  <p className="font-medium">{c.name}</p>
                  <p className="text-xs text-ink-500">{c.industry} · <span className="font-mono">{c.network}</span></p>
                </td>
                <td className="px-3 py-3"><HealthChip h={c.health} /></td>
                <td className="px-3 py-3">
                  <span className="font-mono text-xs">{c.version}</span>
                  {c.version !== LATEST_VERSION && <span className="chip ml-2 bg-amber-50 text-amber-800">업데이트 필요</span>}
                </td>
                <td className={`px-3 py-3 ${c.lastSeenMin > 30 ? 'font-medium text-red-700' : 'text-ink-700'}`}>{ago(c.lastSeenMin)}</td>
                <td className="px-3 py-3 text-right tabular-nums">{c.pcs}</td>
                <td className="px-3 py-3 text-right tabular-nums">{c.services} / 13</td>
                <td className="px-3 py-3 text-right tabular-nums">{c.incidentsToday}</td>
              </tr>
            ))}
          </tbody>
        </table>
      </div>
    </Page>
  )
}

function Incidents() {
  const [origin, setOrigin] = useState<Origin | '전체'>('전체')
  const rows = INCIDENTS.filter((i) => origin === '전체' || i.origin === origin)
  return (
    <Page title="장애·사건" desc="고객사에서 올라온 사건을 원인 쪽으로 나눠요. 우리 쪽이면 고치고, 고객사·외부 쪽이면 안내해요.">
      <div className="flex flex-wrap gap-2" role="group" aria-label="원인 쪽">
        {(['전체', '우리', '고객사', '외부'] as const).map((o) => (
          <button key={o} type="button" aria-pressed={origin === o} onClick={() => setOrigin(o)}
            className={`px-3 py-1.5 text-sm ${origin === o ? 'bg-ink-900 text-white' : 'bg-surface text-ink-700 ring-1 ring-ink-100 hover:bg-ink-100'}`}>
            {o}{o !== '전체' && ` ${INCIDENTS.filter((i) => i.origin === o).length}`}
          </button>
        ))}
      </div>
      <IncidentTable rows={rows} />
      <p className="text-xs text-ink-500">상세는 그 고객사의 DejaFix 장애 화면에서 봐요. 관제 센터는 여러 고객사를 모아 보는 곳이에요.</p>
    </Page>
  )
}

function Quality() {
  return (
    <Page title="AI 품질" desc="AI가 실수하고 있지 않은지 봐요. 자동 처리율이 낮거나 막힌 건이 늘면 기준을 다시 봐요.">
      <div className="grid gap-3 sm:grid-cols-2 lg:grid-cols-4">
        <Tile label="사람 승인" value={COMPANIES.reduce((a, c) => a + c.approvals, 0)} note="오늘, 전체 고객사" />
        <Tile label="안전 관문에서 막힘" value={COMPANIES.reduce((a, c) => a + c.blocked, 0)} note="규칙에 걸려 실행 안 됨" />
        <Tile label="되돌림(롤백)" value={COMPANIES.reduce((a, c) => a + c.rollbacks, 0)} note="실행 뒤 되돌린 건" />
        <Tile label="AI 실수 신고" value={QUALITY.reduce((a, q) => a + q.reported, 0)} note="직원이 직접 신고" />
      </div>
      <section className="card overflow-x-auto p-5">
        <h2 className="card-title">서비스별 자동 처리율</h2>
        <table className="mt-3 w-full min-w-[640px] text-sm">
          <thead className="text-left text-xs text-ink-500">
            <tr>
              <th className="py-2 font-medium">서비스</th>
              <th className="py-2 font-medium">자동 처리율</th>
              <th className="py-2 text-right font-medium">사람 승인</th>
              <th className="py-2 text-right font-medium">막힘</th>
              <th className="py-2 text-right font-medium">되돌림</th>
              <th className="py-2 text-right font-medium">실수 신고</th>
            </tr>
          </thead>
          <tbody className="divide-y divide-ink-100">
            {QUALITY.map((q) => (
              <tr key={q.name}>
                <td className="py-2.5 pr-4 font-medium">{q.name}</td>
                <td className="w-[40%] py-2.5 pr-6"><Meter value={q.autoRate} max={100} label={`${q.autoRate}%`} /></td>
                <td className="py-2.5 text-right tabular-nums">{q.approvals}</td>
                <td className="py-2.5 text-right tabular-nums">{q.blocked}</td>
                <td className="py-2.5 text-right tabular-nums">{q.rollbacks}</td>
                <td className="py-2.5 text-right tabular-nums">{q.reported}</td>
              </tr>
            ))}
          </tbody>
        </table>
      </section>
    </Page>
  )
}

function Cost() {
  const today = COST_TREND[COST_TREND.length - 1].costKrw
  const before = COST_TREND[0].costKrw
  const maxDay = Math.max(...COST_TREND.map((d) => d.costKrw))
  const maxSvc = Math.max(...COST_BY_SERVICE.map((s) => s.costKrw))
  const calls = COMPANIES.reduce((a, c) => a + c.llmCalls, 0)
  return (
    <Page title="비용·사용량" desc="LLM 호출 수와 비용이에요. 서비스마다 맞는 모델을 써서 비용을 낮춰요.">
      <div className="grid gap-3 sm:grid-cols-3">
        <Tile label="오늘 비용" value={won(today)} note="전체 고객사 합계" />
        <Tile label="7일 전보다" value={`−${Math.round((1 - today / before) * 100)}%`} note={`${won(before)} → ${won(today)}`} tone="text-emerald-700" />
        <Tile label="오늘 LLM 호출" value={calls.toLocaleString('ko-KR')} note="전체 고객사 합계" />
      </div>

      <section className="card p-5">
        <div className="flex flex-wrap items-baseline justify-between gap-2">
          <h2 className="card-title">최근 7일 비용</h2>
          <p className="text-xs text-ink-500">10/25에 고객 문의를 작은 모델로 바꿈 (어려운 건만 큰 모델)</p>
        </div>
        <div className="mt-5 grid h-48 grid-cols-7 items-end gap-3" role="img" aria-label={COST_TREND.map((d) => `${d.day} ${won(d.costKrw)}`).join(', ')}>
          {COST_TREND.map((d, i) => (
            <div key={d.day} className="flex h-full flex-col items-center justify-end gap-1.5" title={`${d.day} · ${won(d.costKrw)}`}>
              {(i === 0 || i === COST_TREND.length - 1 || d.day === '10/25') && (
                <span className="text-[11px] tabular-nums text-ink-700">{Math.round(d.costKrw / 1000)}천</span>
              )}
              <div className={`w-full max-w-[44px] ${d.day === '10/25' ? 'bg-brand-700' : 'bg-brand-600'}`} style={{ height: `${(d.costKrw / maxDay) * 100}%` }} />
              <span className="text-[11px] tabular-nums text-ink-500">{d.day}</span>
            </div>
          ))}
        </div>
      </section>

      <section className="card overflow-x-auto p-5">
        <h2 className="card-title">서비스별 오늘 비용</h2>
        <table className="mt-3 w-full min-w-[680px] text-sm">
          <thead className="text-left text-xs text-ink-500">
            <tr>
              <th className="py-2 font-medium">서비스</th>
              <th className="py-2 font-medium">비용</th>
              <th className="py-2 text-right font-medium">호출</th>
              <th className="py-2 pl-6 font-medium">쓰는 모델</th>
            </tr>
          </thead>
          <tbody className="divide-y divide-ink-100">
            {[...COST_BY_SERVICE].sort((a, b) => b.costKrw - a.costKrw).map((s) => (
              <tr key={s.name}>
                <td className="py-2.5 pr-4 font-medium">{s.name}</td>
                <td className="w-[40%] py-2.5 pr-6"><Meter value={s.costKrw} max={maxSvc} label={won(s.costKrw)} /></td>
                <td className="py-2.5 text-right tabular-nums">{s.calls.toLocaleString('ko-KR')}</td>
                <td className="py-2.5 pl-6 text-ink-700">{s.model}</td>
              </tr>
            ))}
          </tbody>
        </table>
      </section>
    </Page>
  )
}

export default function ControlCenter() {
  const { pathname } = useLocation()
  const tab = pathname.replace(/^\/control\/?/, '').split('/')[0]
  if (tab === 'companies') return <Companies />
  if (tab === 'incidents') return <Incidents />
  if (tab === 'quality') return <Quality />
  if (tab === 'cost') return <Cost />
  if (tab === 'keys') return <KeyIssue />
  return <Overview />
}
