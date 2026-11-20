import { useEffect, useState } from 'react'
import { Facts, Notice, SectionHead, StatusChip } from './parts'
import {
  budgetKo, historyLineKo, percentKo, pieSlices, ringPath, stateView, tokensKo, whenKo,
  type AiStatus, type AiUsage, type Slice,
} from '@/lib/companyAi'
import { errText, getAiStatus, getAiUsage } from '@/lib/companyAiApi'

// 시스템 관리 › 도구 연결 › AI 연결 (회원 모두, 보기만. 회사 AI 연결 설계 1·7장).
// 회사 AI 상태·이번 달 사용 %·AI 직원별 사용량·바뀐 알림. 값은 dashboard-api `/ai-connection/status`·`/usage`만.
// 키를 바꾸는 곳은 회사 연결(관리자)이라 여기서는 안내만 한다. 원그래프는 차트 라이브러리 없이 SVG로 그린다.

const isDark = () => typeof document !== 'undefined' && document.documentElement.classList.contains('dark')

function Donut({ slices, total }: { slices: Slice[]; total: number }) {
  const R = 56, r = 36, c = 60
  const gap = slices.length > 1 ? 0.03 : 0
  let a0 = -Math.PI / 2
  const paths = slices.map((s) => {
    const a1 = a0 + s.fraction * Math.PI * 2
    const d = ringPath(c, c, R, r, a0 + gap / 2, a1 - gap / 2)
    a0 = a1
    return { s, d }
  })
  const short = total >= 1_000_000 ? `${Math.round(total / 100_000) / 10}M` : total >= 1000 ? `${Math.round(total / 1000)}k` : String(total)
  return (
    <svg viewBox="0 0 120 120" width={132} height={132} role="img" aria-label="AI 직원별 토큰 사용 비율" className="shrink-0 text-ink-900">
      {paths.map(({ s, d }) => (
        <path key={s.key} d={d} fill={s.color} fillRule="evenodd">
          <title>{`${s.label} · ${percentKo(s.percent)} · ${tokensKo(s.tokens)} 토큰`}</title>
        </path>
      ))}
      <text x={60} y={58} textAnchor="middle" fontSize={13} fontWeight={700} fill="currentColor">{short}</text>
      <text x={60} y={72} textAnchor="middle" fontSize={9} fill="currentColor" opacity={0.65}>토큰</text>
    </svg>
  )
}

function UsageSection({ usage, error }: { usage: AiUsage | null; error: string | null }) {
  const [table, setTable] = useState(false)
  if (!usage) return error ? <Notice tone="bad">AI 직원별 사용량을 받지 못했어요: {error}</Notice> : <p className="text-sm text-ink-500">불러오는 중…</p>
  const slices = pieSlices(usage.departments, isDark())
  return (
    <section className="card space-y-4 p-6">
      <SectionHead as="h3" title={`${usage.month ?? '이번 달'} AI 직원별 사용량`} desc="회사 AI 토큰을 어느 AI 직원이 얼마나 썼는지예요. 비용(원)은 보이지 않아요."
        right={slices.length > 0 && <button type="button" className="btn-ghost" aria-pressed={table} onClick={() => setTable((v) => !v)}>{table ? '그래프로 보기' : '표로 보기'}</button>} />
      {!usage.recording ? (
        <Notice tone="wait">사용량 기록이 꺼져 있어 보여 줄 수 없어요 (서버 설정 AX_LLM_USAGE_DB 필요).</Notice>
      ) : !slices.length ? (
        <p className="text-sm text-ink-500">이번 달 사용 기록이 아직 없어요.</p>
      ) : table ? (
        <table className="w-full text-sm">
          <thead className="text-left text-xs text-ink-500">
            <tr><th className="py-1.5 font-normal">AI 직원</th><th className="py-1.5 text-right font-normal">토큰</th><th className="py-1.5 text-right font-normal">비율</th><th className="py-1.5 text-right font-normal">호출</th></tr>
          </thead>
          <tbody className="divide-y divide-ink-100">
            {usage.departments.filter((d) => d.total_tokens > 0).map((d) => (
              <tr key={d.department}>
                <td className="py-1.5">{slices.find((s) => s.key === d.department)?.label ?? d.department}</td>
                <td className="py-1.5 text-right font-mono">{tokensKo(d.total_tokens)}</td>
                <td className="py-1.5 text-right">{percentKo(d.share_percent)}</td>
                <td className="py-1.5 text-right">{d.calls.toLocaleString('en-US')}</td>
              </tr>
            ))}
          </tbody>
        </table>
      ) : (
        <div className="flex flex-wrap items-center gap-6">
          <Donut slices={slices} total={usage.total_tokens} />
          <ul className="grid min-w-[220px] flex-1 gap-x-6 gap-y-1.5 text-sm sm:grid-cols-2" aria-label="범례">
            {slices.map((s) => (
              <li key={s.key} className="flex items-center gap-2" title={`${tokensKo(s.tokens)} 토큰`}>
                <span aria-hidden="true" className="h-2.5 w-2.5 shrink-0 rounded-sm" style={{ background: s.color }} />
                {s.label}
                <b className="ml-auto font-medium tabular-nums">{percentKo(s.percent)}</b>
              </li>
            ))}
          </ul>
        </div>
      )}
      {usage.partial && <p className="text-xs text-ink-500">기록이 많아 일부만 셌어요.</p>}
    </section>
  )
}

function StatusSection({ status }: { status: AiStatus }) {
  const st = stateView(status.state)
  const u = status.usage
  const trial = status.trial
  return (
    <section className="card space-y-4 p-6">
      <SectionHead as="h3" title="회사 AI" desc="관리자가 회사 연결에서 넣은 키로 AI 직원이 일해요. 여기서는 상태와 사용량만 보여요."
        right={<StatusChip tone={st.tone}>{st.label}</StatusChip>} />
      {st.detail && <p className="text-sm text-ink-700">{st.detail}</p>}
      <Facts cols={4} items={[
        ['모델', status.model ? <span className="font-mono">{status.model}</span> : '—'],
        ['이번 달 사용', u ? budgetKo(u) : '—'],
        ['코딩 에이전트', status.coder.mode === 'separate' ? '따로 설정' : '회사 AI 모델'],
        ['영상 생성', status.video.enabled ? `켜짐${status.video.provider ? ` · ${status.video.provider}` : ''}` : '꺼짐'],
      ]} />
      {u?.budget_tokens != null && (
        <div className="h-2 overflow-hidden rounded-full bg-ink-100" role="meter" aria-valuemin={0} aria-valuemax={100} aria-valuenow={Math.min(100, Math.round(u.percent ?? 0))} aria-label="이번 달 예산 사용">
          <div className={`h-full ${(u.percent ?? 0) >= 100 ? 'bg-red-700' : (u.percent ?? 0) >= 80 ? 'bg-amber-600' : 'bg-brand-600'}`} style={{ width: `${Math.min(100, u.percent ?? 0)}%` }} />
        </div>
      )}
      {trial?.available && (status.state === 'trial' || status.state === 'trial_exhausted') && (
        <p className="text-sm text-ink-700">체험 토큰 {tokensKo(trial.used_tokens)} / {tokensKo(trial.limit_tokens)} 사용 ({percentKo(trial.percent)})</p>
      )}
      {u && !u.recording && <Notice tone="wait">사용량 기록이 꺼져 있어 사용량이 0으로 보여요 (서버 설정 AX_LLM_USAGE_DB 필요).</Notice>}
      <Notice tone="info">키나 모델을 바꿔야 하면 회사 관리자에게 말해 주세요. 바꾸는 곳은 시스템 관리 › 회사 연결 › 공통 AI 도구 연결이에요.</Notice>
    </section>
  )
}

/** 도구 연결 › AI 연결 */
export default function AiConnectionView() {
  const [status, setStatus] = useState<AiStatus | null>(null)
  const [statusError, setStatusError] = useState<string | null>(null)
  const [usage, setUsage] = useState<AiUsage | null>(null)
  const [usageError, setUsageError] = useState<string | null>(null)
  useEffect(() => {
    getAiStatus().then(setStatus).catch((e) => setStatusError(errText(e)))
    getAiUsage().then(setUsage).catch((e) => setUsageError(errText(e)))
  }, [])
  return (
    <div className="space-y-6">
      <SectionHead title="AI 연결" desc="회사 AI가 지금 어떤 상태인지, 이번 달 얼마나 썼는지 보여 줘요. 회원 모두 볼 수 있어요." />
      {status ? <StatusSection status={status} />
        : statusError ? <Notice tone="bad">회사 AI 상태를 받지 못했어요: {statusError}</Notice>
        : <p className="text-sm text-ink-500">불러오는 중…</p>}
      <UsageSection usage={usage} error={usageError} />
      {status && (
        <section className="card p-6">
          <SectionHead as="h3" title="알림" desc="회사 AI 키·설정이 바뀌면 여기에 남아요. 키는 끝 4자리만 보여요." />
          {status.notices.length ? (
            <ul className="mt-3 space-y-1.5 text-sm">
              {status.notices.map((n, i) => (
                <li key={`${n.at}-${i}`} className="flex gap-3">
                  <span className="w-28 shrink-0 text-xs leading-5 text-ink-500">{whenKo(n.at)}</span>
                  <span>{historyLineKo(n)}</span>
                </li>
              ))}
            </ul>
          ) : <p className="mt-3 text-sm text-ink-500">알림이 없어요.</p>}
        </section>
      )}
    </div>
  )
}
