// 대상 레포: 연결이 2개 이상이면 AI가 PR마다 레포를 제안하고, 사람이 승인해야 실제 PR이 열린다
import { useEffect, useState, type Dispatch } from 'react'
import { Button } from '@dejafix/common-frontend'
import type { Action, PR, RepoTarget, State as S } from '@/lib/planningData'
import { DEV_API, kst, vcsApi, type VcsConnection } from '@/lib/planning'
import { Tag } from '@/components/planning-common/parts'

const CONF: Record<RepoTarget['confidence'], { label: string; tone: 'green' | 'amber' | 'neutral' }> = {
  high: { label: '확신 높음', tone: 'green' },
  mid: { label: '확신 보통', tone: 'neutral' },
  low: { label: '확신 낮음', tone: 'amber' },
}

/** 레포 승인 대기 PR 수 (헤더 칩) */
export const repoPending = (s: S) => s.prs.filter((p) => p.target?.status === 'proposed').length

// 연결 목록은 30초에 한 번만 읽는다. 실패·목이면 빈 목록 → target.candidates로 채운다
let cache: { at: number; list: Promise<VcsConnection[]> } | null = null
function useConnections(): VcsConnection[] {
  const [list, setList] = useState<VcsConnection[]>([])
  useEffect(() => {
    let alive = true
    if (!cache || Date.now() - cache.at > 30_000) cache = { at: Date.now(), list: vcsApi(DEV_API).list().catch(() => []) }
    void cache.list.then((x) => alive && setList(x))
    return () => { alive = false }
  }, [])
  return list
}

/** 승인 select에 넣을 레포: 후보(점수순) + 나머지 연결 */
function options(t: RepoTarget, conns: VcsConnection[], skip?: string) {
  const seen = new Set<string>()
  const out: { id: string; repo: string }[] = []
  for (const c of [...(t.candidates ?? [])].sort((a, b) => b.score - a.score)) out.push({ id: c.connectionId, repo: c.repo })
  for (const c of conns) out.push({ id: c.id, repo: c.repo })
  return out.filter((o) => o.id !== skip && !seen.has(o.id) && !!seen.add(o.id))
}

function Pick({ pr, t, conns, skip, d, label }: { pr: string; t: RepoTarget; conns: VcsConnection[]; skip?: string; d: Dispatch<Action>; label: string }) {
  const opts = options(t, conns, skip)
  const [pick, setPick] = useState('')
  const sel = opts.find((o) => o.id === pick) ? pick : opts[0]?.id ?? ''
  if (!opts.length) return null
  return (
    <span className="repo-pick">
      <select className="select mono" value={sel} onChange={(e) => setPick(e.target.value)} aria-label="다른 레포">
        {opts.map((o) => <option key={o.id} value={o.id}>{o.repo}</option>)}
      </select>
      <Button size="small" onClick={() => d({ type: 'approveRepo', pr, connection: sel })}>{label}</Button>
    </span>
  )
}

/** PR 상세의 "대상 레포" 내용. target이 없으면 아무것도 그리지 않는다 */
export function RepoTargetBlock({ p, d }: { p: PR; d: Dispatch<Action> }) {
  const conns = useConnections()
  const t = p.target
  if (!t) return null
  if (t.status === 'approved') {
    return (
      <span className="tags">
        <Tag><span className="mono">{t.repo}</span></Tag>
        <Tag tone="green">승인 · {t.decidedBy ?? '-'}</Tag>
        {t.decidedAt && <span className="muted small mono">{kst(t.decidedAt)}</span>}
      </span>
    )
  }
  if (t.status === 'rejected') {
    return (
      <div className="repo-block">
        <span className="tags">
          <Tag><span className="mono">{t.repo}</span></Tag>
          <Tag tone="amber">거절됨</Tag>
          {t.decidedBy && <span className="muted small">{t.decidedBy}{t.decidedAt ? ` · ${kst(t.decidedAt)}` : ''}</span>}
        </span>
        <Pick pr={p.id} t={t} conns={conns} skip={t.connectionId} d={d} label="이 레포로 승인" />
      </div>
    )
  }
  const conf = CONF[t.confidence] ?? CONF.mid
  return (
    <div className="repo-block">
      <span className="tags">
        <Tag><span className="mono">{t.repo}</span></Tag>
        <Tag tone={conf.tone}>{conf.label}</Tag>
        <Tag tone="amber">승인 대기</Tag>
      </span>
      {!!t.reasons?.length && (
        <ul className="repo-reasons">
          {t.reasons.map((r, i) => {
            const ai = r.startsWith('AI: ')
            return <li key={i}>{ai && <span className="grade">AI</span>} {ai ? r.slice(4) : r}</li>
          })}
        </ul>
      )}
      <div className="repo-actions">
        <Button size="small" variant="primary" onClick={() => d({ type: 'approveRepo', pr: p.id })}>승인</Button>
        <Pick pr={p.id} t={t} conns={conns} skip={t.connectionId} d={d} label="이 레포로 승인" />
        <Button size="small" onClick={() => d({ type: 'rejectRepo', pr: p.id })}>거절</Button>
      </div>
    </div>
  )
}

/** PR 한 줄용 짧은 표시 */
export function RepoTag({ p }: { p: PR }) {
  const t = p.target
  if (!t) return null
  return t.status === 'proposed' ? <Tag tone="amber">레포 승인 대기 · <span className="mono">{t.repo}</span></Tag>
    : t.status === 'rejected' ? <Tag tone="amber">레포 거절됨</Tag>
    : <Tag><span className="mono">{t.repo}</span></Tag>
}
