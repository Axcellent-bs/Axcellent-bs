// 여러 화면이 같이 쓰는 작은 조각. 목록 줄·카드는 통째로 눌러 다음 화면으로 간다 (rowNav, LinkTable)
import type { Key, KeyboardEvent, MouseEvent, ReactNode } from 'react'
import { type ChipTone, type Column } from '@dejafix/common-frontend'
import { DEPT_NAME, type Activity, type Dept, type State as S } from './data'
import { Linked, convoHref, deptHref, go } from './links'

// 부서·연결 지점은 글자로만 보인다. 링크가 필요한 곳에서만 link를 켠다
export function Dep({ d, link }: { d: Dept; link?: boolean }) {
  if (!link) return <span className="dep">{DEPT_NAME[d]}</span>
  return <a href={deptHref(d)} className="dep dep-link" title={`${DEPT_NAME[d]}와 주고받은 활동 보기`}>{DEPT_NAME[d]}</a>
}

export function Point({ children, link }: { children: string; link?: boolean }) {
  if (!link) return <code className="point">{children}</code>
  return <a href="#/metrics/links" className="point-link" title="부서 연동 표에서 보기"><code className="point">{children}</code></a>
}

// 색 배경 칩 대신 글자로만 표시한다. 색은 정말 봐야 하는 상태(초록=끝남, 주황=사람이 볼 것)에만
export function Tag({ tone, title, children }: { tone?: ChipTone; title?: string; children: ReactNode }) {
  return <span className={`tag${tone && tone !== 'neutral' ? ` tag-${tone}` : ''}`} title={title}>{children}</span>
}

export function Grade({ g }: { g?: string }) {
  if (!g || g === '-') return null
  return <span className="grade" title={g === 'read' ? '상태를 바꾸지 않는 읽기' : `${g} 등급`}>{g === 'read' ? '읽기' : `${g}등급`}</span>
}

// 상태 = 점 하나 + 글자
export function State({ v }: { v: string }) {
  const tone = ['완료', '확정', '통과', '승인', '구현됨', '정식'].includes(v) ? 'green' : ['접수', '초안', '머지 대기', '불일치', 'C 보류', '중복 연결', '거절', '걸림', '해제됨', '교차검증 대기', '사람 확인 대기', '교차검증 거절', '학습 거부', '기준 해제', '장애 연결', '승인 대기'].includes(v) ? 'amber' : 'neutral'
  return <span className={`st st-${tone}`}>{v}</span>
}

// 줄·카드 전체를 눌러 이동한다. 안에 있는 링크·버튼·입력칸을 누르면 그쪽이 먼저다
const inner = (e: { target: EventTarget }) => !!(e.target as HTMLElement).closest('a, button, input, select, textarea, label')
export function rowNav(href: string | undefined, title?: string) {
  if (!href) return {}
  return {
    className: 'row-link',
    role: 'link',
    tabIndex: 0,
    title,
    onClick: (e: MouseEvent) => { if (!inner(e) && !window.getSelection()?.toString()) go(href) },
    onKeyDown: (e: KeyboardEvent) => { if (e.key === 'Enter' && !inner(e)) go(href) },
  }
}

// DataTable과 같은 모양에 줄 전체 링크를 더한 표
export function LinkTable<T>({ columns, rows, rowKey, rowHref, rowTitle, highlight }: { columns: Column<T>[]; rows: T[]; rowKey: (r: T) => Key; rowHref: (r: T) => string | undefined; rowTitle?: string; highlight?: (r: T) => boolean }) {
  return (
    <div className="table-wrap">
      <table className="data">
        <thead><tr>{columns.map((c) => <th key={c.key} className={c.numeric ? 'num' : undefined}>{c.header}</th>)}</tr></thead>
        <tbody>
          {rows.map((r) => {
            const nav = rowNav(rowHref(r), rowTitle)
            return (
              <tr key={rowKey(r)} {...nav} className={`${nav.className ?? ''}${highlight?.(r) ? ' hl' : ''}`}>
                {columns.map((c) => <td key={c.key} className={c.numeric ? 'num' : undefined}>{c.render ? c.render(r) : ((r as Record<string, unknown>)[c.key] as ReactNode)}</td>)}
              </tr>
            )
          })}
        </tbody>
      </table>
    </div>
  )
}

// 활동 한 줄 = 그 사건·요청의 에이전트 대화로 이동
export function ActivityList({ s, items, compact }: { s: S; items: Activity[]; compact?: boolean }) {
  return (
    <ol className={`act${compact ? ' compact' : ''}`}>
      {items.map((a, i) => (
        <li key={i} {...rowNav(convoHref(a.ctx), '이 사건·요청의 에이전트 대화 보기')}>
          <span className="act-t mono">{a.t}</span>
          <div className="act-main">
            <div className="act-route">
              <Dep d={a.from} /> <span className="arrow">→</span> <Dep d={a.to} /> <Point>{a.point}</Point> <span className="kind">{a.kind}</span> <Grade g={a.grade} />
            </div>
            <div className="act-text"><Linked s={s} text={a.text} /></div>
            {!compact && <div className="act-ctx mono">{a.ctx}</div>}
          </div>
        </li>
      ))}
    </ol>
  )
}

// 가로탭 아래 한 줄 설명 (탭 이름이 제목이라 큰 제목을 다시 쓰지 않는다). 오른쪽에 버튼을 둘 수 있다
export function Lead({ children, actions }: { children: ReactNode; actions?: ReactNode }) {
  return (
    <div className="lead">
      <p>{children}</p>
      {actions && <div className="lead-actions">{actions}</div>}
    </div>
  )
}

// 옆 칸의 가벼운 묶음 (카드 테두리 없이 제목 + 내용)
export function Sec({ title, sub, children }: { title: ReactNode; sub?: ReactNode; children?: ReactNode }) {
  return (
    <section className="sec-side">
      <h3>{title}{sub && <span className="sec-side-sub">{sub}</span>}</h3>
      {children}
    </section>
  )
}

// 숫자 몇 개를 한 줄로. 큰 숫자 카드 대신 쓴다
export function Stats({ items }: { items: { label: string; value: ReactNode; hint?: string; href?: string }[] }) {
  return (
    <div className="stats">
      {items.map((x) => {
        const inner = <><span className="stats-label">{x.label}</span><b>{x.value}</b>{x.hint && <span className="stats-hint">{x.hint}</span>}</>
        return x.href ? <a key={x.label} href={x.href} className="stats-item">{inner}</a> : <div key={x.label} className="stats-item">{inner}</div>
      })}
    </div>
  )
}
