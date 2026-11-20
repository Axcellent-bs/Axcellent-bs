import { useEffect, useState } from 'react'
import { DEV_API, codingSettingsApi, type CodingSettings, type CodingStep } from '@/lib/planning'
import { UsageBar } from '@/components/dev/coding'
import { Facts, Notice, SectionHead } from './parts'

// 도구 연결 › AI 연결 › 개발 AI 코딩 (회원 모두, 보기만).
// 예전 "내 연결"에 있던 개발 AI의 회사 단위 설정(요청 작업 코딩 방식·서버 AI 키·월 예산·단계별 모델)을 보기로 옮겼다(11/20).
// 값은 개발 AI(dev 서비스 `/api/planning/coding-settings`)가 준 것만. 키는 끝 4자리만.
// 바꾸는 곳: 코딩 방식은 내 연결 › 고급 · 개발자 PC 연결, 키·모델·예산은 회사 연결 › 공통 AI 도구 연결(관리자).

const MODE_KO: Record<string, string> = { auto: '자동', agent: '서버 AI', subscription: '내 Claude' }
const STEPS: { id: CodingStep; label: string }[] = [
  { id: 'plan', label: '파일 고르기' },
  { id: 'write', label: '코드 쓰기' },
  { id: 'review', label: '리뷰' },
]

export default function DevCodingSummary() {
  const [cfg, setCfg] = useState<CodingSettings | null>(null)
  const [error, setError] = useState<string | null>(null)
  useEffect(() => { codingSettingsApi(DEV_API).get().then(setCfg).catch(() => setError('개발 AI에 연결할 수 없어요')) }, [])
  const key = cfg?.company_key
  const b = cfg?.budget
  return (
    <section className="card space-y-4 p-6">
      <SectionHead as="h3" title="개발 AI 코딩" desc="기획에서 넘어온 요청 작업을 누가 코딩하는지, 서버 AI가 코딩에 쓰는 키·예산·모델이에요. 여기서는 보기만 해요." />
      {!cfg ? (error ? <Notice tone="bad">{error}</Notice> : <p className="text-sm text-ink-500">불러오는 중…</p>) : (
        <>
          <Facts cols={3} items={[
            ['코딩 방식', `${MODE_KO[cfg.mode] ?? cfg.mode} · 지금 ${cfg.resolved === 'agent' ? '서버 AI' : '내 Claude'}가 코딩`],
            ['서버 AI 키', key?.key_set ? <span className="font-mono">••••{key.key_hint}</span> : '서버 기본 키 (체험)'],
            ['최근 7일 본인 Claude 연결', cfg.agents_connected.length ? cfg.agents_connected.join(', ') : '없음'],
          ]} />
          {cfg.why && <p className="text-sm text-ink-700">판단: {cfg.why}</p>}
          {!key?.key_set && <UsageBar used={cfg.trial_used} limit={cfg.trial_limit} wide title="서버 AI(기본 키)가 코딩에 쓴 토큰 / 체험 한도" />}
          {b && (b.limit != null
            ? <UsageBar used={b.used} limit={b.limit} label={b.month} title="이번 달 서버 AI 코딩이 쓴 토큰 / 월 예산" />
            : <p className="text-xs text-ink-500">{b.month} 사용 <span className="font-mono">{b.used.toLocaleString()}</span> 토큰 · 예산 없음</p>)}
          <p className="text-xs text-ink-500">
            단계별 모델: {STEPS.map((s) => `${s.label} ${cfg.models?.[s.id] || cfg.model_default || '기본 모델'}`).join(' · ')}
          </p>
          <Notice tone="info">코딩 방식은 내 연결 › 고급 · 개발자 PC 연결에서, 키·모델·예산은 관리자가 회사 연결 › 공통 AI 도구 연결에서 바꿔요.</Notice>
        </>
      )}
    </section>
  )
}
