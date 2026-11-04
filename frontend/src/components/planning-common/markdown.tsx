// 마크다운 보기 (기획 문서·기획서 자유 작성·제안 설명). 외부 라이브러리 없이 GFM 일부만 그린다.
// 지원: 제목(#~###), 문단, 굵게·기울임·취소선·인라인 코드, 링크(http·https만), 글머리·번호 목록(들여쓰기 2단),
//       할 일(- [ ]), 인용(>), 코드 블록(```), 표(| a | b |), 구분선(---).
// HTML은 그리지 않고 글자로 둔다 (React가 이스케이프) → 노션·다른 부서에서 온 글을 그대로 넣어도 안전하다.
import { Fragment, useState, type ReactNode } from 'react'

const SAFE_URL = /^(https?:\/\/|mailto:)/i

/** 줄 안 서식: **굵게** *기울임* ~~취소~~ `코드` [글](주소) 맨 주소 */
function inline(text: string, key = 'i'): ReactNode[] {
  const out: ReactNode[] = []
  const re = /(`[^`]+`)|(\*\*[^*]+\*\*)|(~~[^~]+~~)|(\*[^*\s][^*]*\*)|(\[[^\]]+\]\([^)\s]+\))|(https?:\/\/[^\s)]+)/g
  let last = 0
  let m: RegExpExecArray | null
  let n = 0
  while ((m = re.exec(text))) {
    if (m.index > last) out.push(text.slice(last, m.index))
    const t = m[0]
    const k = `${key}-${n++}`
    if (m[1]) out.push(<code key={k} className="md-code">{t.slice(1, -1)}</code>)
    else if (m[2]) out.push(<strong key={k}>{inline(t.slice(2, -2), k)}</strong>)
    else if (m[3]) out.push(<del key={k}>{inline(t.slice(2, -2), k)}</del>)
    else if (m[4]) out.push(<em key={k}>{inline(t.slice(1, -1), k)}</em>)
    else if (m[5]) {
      const label = t.slice(1, t.indexOf(']('))
      const url = t.slice(t.indexOf('](') + 2, -1)
      out.push(SAFE_URL.test(url) ? <a key={k} href={url} target="_blank" rel="noreferrer noopener">{inline(label, k)}</a> : label)
    } else if (m[6]) out.push(<a key={k} href={t} target="_blank" rel="noreferrer noopener">{t}</a>)
    last = m.index + t.length
  }
  if (last < text.length) out.push(text.slice(last))
  return out
}

const isTableRow = (l: string) => /^\s*\|.*\|\s*$/.test(l)
const isSep = (l: string) => /^\s*\|?\s*:?-{3,}:?\s*(\|\s*:?-{3,}:?\s*)*\|?\s*$/.test(l)
const cells = (l: string) => l.trim().replace(/^\|/, '').replace(/\|$/, '').split('|').map((c) => c.trim())
const LIST = /^(\s*)([-*+]|\d+[.)])\s+(\[[ xX]\]\s+)?(.*)$/

type Item = { depth: number; ordered: boolean; todo?: boolean; done?: boolean; text: string }

function List({ items, k }: { items: Item[]; k: string }) {
  // 들여쓰기(깊이)로 2단까지 묶는다
  const groups: { head: Item; kids: Item[] }[] = []
  for (const it of items) {
    if (it.depth > 0 && groups.length) groups[groups.length - 1].kids.push(it)
    else groups.push({ head: it, kids: [] })
  }
  const Tag = groups[0]?.head.ordered ? 'ol' : 'ul'
  const li = (it: Item, key: string, kids?: Item[]) => (
    <li key={key} className={it.todo ? 'md-todo' : undefined}>
      {it.todo && <input type="checkbox" checked={!!it.done} readOnly aria-label={it.done ? '완료' : '할 일'} />}
      {inline(it.text, key)}
      {kids && kids.length > 0 && <List items={kids.map((x) => ({ ...x, depth: 0 }))} k={`${key}-c`} />}
    </li>
  )
  return <Tag className="md-list">{groups.map((g, i) => li(g.head, `${k}-${i}`, g.kids))}</Tag>
}

export function Markdown({ text, className = '' }: { text: string; className?: string }) {
  const lines = (text ?? '').replace(/\r\n?/g, '\n').split('\n')
  const blocks: ReactNode[] = []
  let i = 0
  let b = 0
  const key = () => `b${b++}`
  while (i < lines.length) {
    const line = lines[i]
    if (!line.trim()) { i++; continue }
    // 코드 블록
    const fence = line.match(/^\s*```(\S*)/)
    if (fence) {
      const body: string[] = []
      i++
      while (i < lines.length && !/^\s*```/.test(lines[i])) body.push(lines[i++])
      i++
      blocks.push(<pre key={key()} className="md-pre"><code>{body.join('\n')}</code></pre>)
      continue
    }
    // 표
    if (isTableRow(line) && i + 1 < lines.length && isSep(lines[i + 1])) {
      const head = cells(line)
      const rows: string[][] = []
      i += 2
      while (i < lines.length && isTableRow(lines[i])) rows.push(cells(lines[i++]))
      const k = key()
      blocks.push(
        <div key={k} className="md-table-wrap">
          <table className="md-table">
            <thead><tr>{head.map((c, j) => <th key={j}>{inline(c, `${k}h${j}`)}</th>)}</tr></thead>
            <tbody>{rows.map((r, ri) => <tr key={ri}>{head.map((_, j) => <td key={j}>{inline(r[j] ?? '', `${k}r${ri}c${j}`)}</td>)}</tr>)}</tbody>
          </table>
        </div>,
      )
      continue
    }
    // 제목
    const h = line.match(/^(#{1,6})\s+(.*)$/)
    if (h) {
      const k = key()
      const level = Math.min(h[1].length, 3)
      const H = (`h${level + 1}`) as 'h2' | 'h3' | 'h4'
      blocks.push(<H key={k} className={`md-h md-h${level}`}>{inline(h[2], k)}</H>)
      i++
      continue
    }
    // 구분선
    if (/^\s*(-{3,}|\*{3,}|_{3,})\s*$/.test(line)) { blocks.push(<hr key={key()} className="md-hr" />); i++; continue }
    // 인용
    if (/^\s*>/.test(line)) {
      const body: string[] = []
      while (i < lines.length && /^\s*>/.test(lines[i])) body.push(lines[i++].replace(/^\s*>\s?/, ''))
      const k = key()
      blocks.push(<blockquote key={k} className="md-quote">{body.map((x, j) => <Fragment key={j}>{j > 0 && <br />}{inline(x, `${k}${j}`)}</Fragment>)}</blockquote>)
      continue
    }
    // 목록
    if (LIST.test(line)) {
      const items: Item[] = []
      while (i < lines.length && LIST.test(lines[i])) {
        const m = lines[i].match(LIST)!
        items.push({ depth: m[1].length >= 2 ? 1 : 0, ordered: /\d/.test(m[2]), todo: !!m[3], done: !!m[3] && /x/i.test(m[3]), text: m[4] })
        i++
      }
      blocks.push(<List key={key()} items={items} k={`l${b}`} />)
      continue
    }
    // 문단 (빈 줄·다른 블록이 나올 때까지)
    const body: string[] = []
    while (i < lines.length && lines[i].trim() && !/^\s*(```|#{1,6}\s|>|(-{3,}|\*{3,})\s*$)/.test(lines[i]) && !LIST.test(lines[i]) && !(isTableRow(lines[i]) && isSep(lines[i + 1] ?? ''))) body.push(lines[i++])
    const k = key()
    blocks.push(<p key={k} className="md-p">{body.map((x, j) => <Fragment key={j}>{j > 0 && <br />}{inline(x, `${k}${j}`)}</Fragment>)}</p>)
  }
  return <div className={`md ${className}`}>{blocks}</div>
}

/** 노션처럼: 평소엔 서식대로 보이고, 누르면 원문(md) 편집, 칸에서 벗어나면 저장하고 다시 보기 */
export function MdEditable({ value, onSave, placeholder = '내용을 쓰세요 (마크다운)', minRows = 2, className = '', label }: {
  value: string
  onSave: (text: string) => void
  placeholder?: string
  minRows?: number
  className?: string
  label?: string
}) {
  const [editing, setEditing] = useState(false)
  if (editing || !value.trim()) {
    return (
      <textarea
        className={`blk-p md-edit ${className}`}
        autoFocus={editing}
        defaultValue={value}
        placeholder={placeholder}
        aria-label={label}
        rows={Math.max(minRows, value.split('\n').length + 1)}
        onBlur={(e) => { if (e.target.value !== value) onSave(e.target.value); setEditing(false) }}
        onKeyDown={(e) => { if (e.key === 'Escape') (e.target as HTMLTextAreaElement).blur() }}
      />
    )
  }
  return (
    <div className={`md-view ${className}`} role="button" tabIndex={0} title="눌러서 고치기" aria-label={label ? `${label} 고치기` : '고치기'}
      onClick={(e) => { if (!(e.target as HTMLElement).closest('a')) setEditing(true) }}
      onKeyDown={(e) => { if (e.key === 'Enter') setEditing(true) }}>
      <Markdown text={value} />
    </div>
  )
}
