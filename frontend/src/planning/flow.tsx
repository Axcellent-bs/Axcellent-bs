// 프로젝트 탭의 레이아웃(왼쪽 목록 / 가운데 세로 흐름 / 오른쪽 곁 블록)을 다른 탭에서도 쓰는 조각.
// 단계 머리는 짧은 라벨 + 값. 줄·단계·블록은 통째로 눌러 이동한다 (rowNav).
import type { ReactNode } from 'react'
import type { PR, Spec, State as S } from './data'
import { reviewOf } from './links'
import { rowNav } from './parts'

export const CONFIRMED_BY = { auto: '자동 확정', review: '교차검증 후 확정', human: '사람 확인 후 확정' } as const
export const mergedLabel = (p: PR) => !p.merged ? '머지 전' : p.mergedBy === 'rule' ? `자동 머지 · ${p.rule}` : p.mergedBy === 'review' ? '교차검증 후 자동 머지' : '사람 머지'

export function specStatus(s: S, sp: Spec): string {
  const xr = reviewOf(s, sp.id)
  if (xr) return xr.lane === 'human' ? '사람 확인 대기' : '교차검증 대기'
  if (sp.status === '확정') return sp.confirmedBy ? CONFIRMED_BY[sp.confirmedBy] : '확정'
  return sp.question ? '초안 · 질문 대기' : '초안'
}
export function prStatus(s: S, prs: PR[]): string {
  const n = (f: (p: PR) => boolean) => prs.filter(f).length
  const parts = [
    n((p) => p.mergedBy === 'rule') && `자동 머지 ${n((p) => p.mergedBy === 'rule')}`,
    n((p) => p.mergedBy === 'review') && `교차검증 후 머지 ${n((p) => p.mergedBy === 'review')}`,
    n((p) => p.merged && (!p.mergedBy || p.mergedBy === 'human')) && `사람 머지 ${n((p) => p.merged && (!p.mergedBy || p.mergedBy === 'human'))}`,
    n((p) => !p.merged && !!reviewOf(s, p.id)) && `교차검증 대기 ${n((p) => !p.merged && !!reviewOf(s, p.id))}`,
    n((p) => !p.merged && !reviewOf(s, p.id)) && `머지 전 ${n((p) => !p.merged && !reviewOf(s, p.id))}`,
  ].filter(Boolean)
  return `PR ${prs.length} · ${parts.join(' · ')}`
}

// 전체 틀: 왼쪽 목록 + 가운데(머리·흐름) + 오른쪽 곁 블록
export function Layout({ list, head, side, children, below }: { list: ReactNode; head?: ReactNode; side?: ReactNode; children: ReactNode; below?: ReactNode }) {
  return (
    <div className="proj">
      <aside className="col-left proj-list">{list}</aside>
      <section className="proj-main">
        {head}
        <div className={side ? 'proj-body' : undefined}>
          <div className="flow-col">{children}</div>
          {side && <aside className="pside">{side}</aside>}
        </div>
        {below}
      </section>
    </div>
  )
}

export function Head({ kicker, title, side }: { kicker: ReactNode; title: ReactNode; side?: ReactNode }) {
  return (
    <header className="proj-head">
      <div>
        <p className="proj-kicker">{kicker}</p>
        <h1 className="proj-title">{title}</h1>
      </div>
      {side && <div className="proj-head-side">{side}</div>}
    </header>
  )
}

// 왼쪽 목록. groups마다 머리(이름·건수) + 줄
export interface ListRow { key: string; href: string; title: ReactNode; meta?: ReactNode; on?: boolean }
export function ListPane({ groups, empty }: { groups: { label: string; rows: ListRow[] }[]; empty?: string }) {
  const total = groups.reduce((n, g) => n + g.rows.length, 0)
  if (!total) return <div className="ph">{empty ?? '없음'}</div>
  return (
    <>
      {groups.filter((g) => g.rows.length).map((g) => (
        <div key={g.label} className="list-group">
          <div className="ph">{g.label} {g.rows.length}</div>
          {g.rows.map((r) => (
            <a key={r.key} href={r.href} className={`doc-li${r.on ? ' on' : ''}`}>
              <span className="li-title">{r.title}</span>
              {r.meta && <span className="proj-li-meta">{r.meta}</span>}
            </a>
          ))}
        </div>
      ))}
    </>
  )
}

export function Flow({ children, label }: { children: ReactNode; label?: string }) {
  return <ol className="pflow" aria-label={label ?? '진행 흐름'}>{children}</ol>
}

// 흐름 한 단계. name = 짧은 라벨, title = 값
export function Step({ name, on, href, title, empty, children }: { name: string; on: boolean; href?: string; title: ReactNode; empty?: ReactNode; children?: ReactNode }) {
  const nav = rowNav(href, `${name} 보기`)
  return (
    <li {...nav} className={`pstage${on ? ' on' : ''}${nav.className ? ` ${nav.className}` : ''}`}>
      <span className="pstage-dot" aria-hidden />
      <div>
        <div className="pstage-head">
          <span className="pstage-name">{name}</span>
          <span className="pstage-title">{title}</span>
          {href && <span className="pstage-go" aria-hidden>›</span>}
        </div>
        {empty ? <p className="pstage-empty">{empty}</p> : children ? <div className="pstage-detail">{children}</div> : null}
      </div>
    </li>
  )
}

export function Side({ title, sum, href, children }: { title: string; sum?: ReactNode; href?: string; children?: ReactNode }) {
  const nav = rowNav(href, `${title} 열기`)
  return (
    <section {...nav} className={`pside-block${nav.className ? ` ${nav.className}` : ''}`}>
      <div className="pstage-head"><h2>{title}</h2>{href && <span className="pstage-go" aria-hidden>›</span>}</div>
      {sum && <div className="pside-sum">{sum}</div>}
      {children}
    </section>
  )
}

// 라벨 · 값 줄 (설명 문장 대신)
export function Pairs({ items }: { items: [string, ReactNode][] }) {
  return (
    <dl className="pairs">
      {items.map(([k, v]) => <div key={k}><dt>{k}</dt><dd>{v}</dd></div>)}
    </dl>
  )
}
