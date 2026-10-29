// 변경 파일 화면 (GitHub "Files changed"를 기준으로): 파일 목록·찾기, 확인함 체크와 진행, 한 줄/나란히 보기,
// 줄 코멘트, 파일 직접 편집과 내 수정 diff, 리뷰 끝내기(코멘트·승인·수정 요청).
// 줄 코멘트·직접 편집·리뷰는 아직 레포에 보내는 서버 기능이 없어 이 브라우저에만 저장하고 "레포 반영 전"으로 표시한다.
import { useMemo, useRef, useState } from 'react'
import { Button, useToast } from '@dejafix/common-frontend'
import type { RepoFile } from '@/lib/planning'

export const FILE_STATUS: Record<string, string> = { added: '추가', removed: '삭제', modified: '수정', renamed: '이름 바꿈', copied: '복사', changed: '수정' }

export interface DiffRow { kind: 'hunk' | 'add' | 'del' | 'ctx' | 'note'; old?: number; new?: number; text: string }
const HUNK = /^@@ -(\d+)(?:,\d+)? \+(\d+)(?:,\d+)? @@/
export function parsePatch(patch: string): DiffRow[] {
  const out: DiffRow[] = []
  let o = 0
  let n = 0
  for (const line of patch.split('\n')) {
    const m = HUNK.exec(line)
    if (m) { o = Number(m[1]); n = Number(m[2]); out.push({ kind: 'hunk', text: line }); continue }
    if (line.startsWith('+')) out.push({ kind: 'add', new: n++, text: line.slice(1) })
    else if (line.startsWith('-')) out.push({ kind: 'del', old: o++, text: line.slice(1) })
    else if (line.startsWith('\\')) out.push({ kind: 'note', text: line })
    else if (line !== '' || out.length) out.push({ kind: 'ctx', old: o++, new: n++, text: line.slice(1) })
  }
  // 끝의 빈 줄(마지막 줄바꿈)은 버린다
  while (out.length && out[out.length - 1].kind === 'ctx' && out[out.length - 1].text === '') out.pop()
  return out
}

// 두 글의 줄 단위 차이 (내 수정 보기). 바뀐 부분 주변이라 길지 않다
function lineDiff(a: string[], b: string[]): DiffRow[] {
  const n = a.length
  const m = b.length
  const L: number[][] = Array.from({ length: n + 1 }, () => new Array<number>(m + 1).fill(0))
  for (let i = n - 1; i >= 0; i--) for (let j = m - 1; j >= 0; j--) L[i][j] = a[i] === b[j] ? L[i + 1][j + 1] + 1 : Math.max(L[i + 1][j], L[i][j + 1])
  const out: DiffRow[] = []
  let i = 0
  let j = 0
  while (i < n || j < m) {
    if (i < n && j < m && a[i] === b[j]) { out.push({ kind: 'ctx', old: i + 1, new: j + 1, text: a[i] }); i++; j++ }
    else if (j < m && (i >= n || L[i][j + 1] >= L[i + 1][j])) { out.push({ kind: 'add', new: j + 1, text: b[j] }); j++ }
    else { out.push({ kind: 'del', old: i + 1, text: a[i] }); i++ }
  }
  return out
}

// ---- 이 브라우저에만 두는 리뷰 초안 (PR마다)
export interface LineComment { id: string; path: string; side: 'old' | 'new'; line: number; body: string; at: string }
type ReviewKind = 'comment' | 'approve' | 'changes'
interface Draft {
  viewed: string[]
  edits: Record<string, { text: string; message: string; at: string }>
  comments: LineComment[]
  review?: { kind: ReviewKind; body: string; at: string }
}
const EMPTY: Draft = { viewed: [], edits: {}, comments: [] }
const KEY = (k: string) => `axcellent-pr-draft:${k}`
function useDraft(key: string): [Draft, (f: (d: Draft) => Draft) => void] {
  const [d, setD] = useState<Draft>(() => {
    try { return { ...EMPTY, ...(JSON.parse(localStorage.getItem(KEY(key)) ?? 'null') as Draft | null) } } catch { return EMPTY }
  })
  const update = (f: (d: Draft) => Draft) => setD((cur) => {
    const next = f(cur)
    try { localStorage.setItem(KEY(key), JSON.stringify(next)) } catch { /* 저장이 막혀도 이번 화면에서는 남는다 */ }
    return next
  })
  return [d, update]
}
const stamp = () => new Date().toLocaleString('ko-KR', { month: '2-digit', day: '2-digit', hour: '2-digit', minute: '2-digit', hour12: false })
const REVIEW_LABEL: Record<ReviewKind, string> = { comment: '코멘트', approve: '승인', changes: '수정 요청' }
const NOT_SENT = <span className="tag tag-amber" title="아직 레포에 보내는 서버 기능이 없어 이 브라우저에만 저장돼요">레포 반영 전</span>

// 바뀐 뒤(새 쪽) 줄만 모은 글: 직접 편집의 시작점
const newSide = (rows: DiffRow[]) => rows.filter((r) => r.kind === 'add' || r.kind === 'ctx').map((r) => r.text).join('\n')

/**
 * `draftKey`: PR마다 다른 값(레포 PR은 "연결#번호", 내부 PR은 "PR #n")
 * `editable`: 직접 편집·줄 코멘트·리뷰를 열지 (열린 PR, 쓰기 권한)
 * `compact`: 다른 화면 안에 넣을 때 왼쪽 파일 목록 없이
 */
export function DiffFiles({ files, truncated, total, draftKey, editable, compact }: { files: RepoFile[]; truncated?: boolean; total?: number; draftKey: string; editable?: boolean; compact?: boolean }) {
  const [d, update] = useDraft(draftKey)
  const [split, setSplit] = useState(false)
  const [q, setQ] = useState('')
  const [shut, setShut] = useState<Set<string>>(() => new Set(d.viewed))
  const toggle = (p: string) => setShut((s) => { const t = new Set(s); if (t.has(p)) t.delete(p); else t.add(p); return t })
  const setViewed = (p: string, on: boolean) => {
    update((x) => ({ ...x, viewed: on ? [...new Set([...x.viewed, p])] : x.viewed.filter((v) => v !== p) }))
    setShut((s) => { const t = new Set(s); if (on) t.add(p); else t.delete(p); return t })
  }
  if (!files.length) return <div className="empty"><b>바뀐 파일 없음</b></div>
  const shown = files.filter((f) => !q || f.path.toLowerCase().includes(q.toLowerCase()))
  const viewed = files.filter((f) => d.viewed.includes(f.path)).length
  const jump = (path: string) => document.getElementById(`rp-f-${draftKey}-${path}`)?.scrollIntoView({ behavior: 'smooth', block: 'start' })

  const bar = (
    <div className="df-bar">
      <div className="seg-toggle" role="group" aria-label="diff 보기">
        <button type="button" aria-pressed={!split} onClick={() => setSplit(false)}>한 줄</button>
        <button type="button" aria-pressed={split} onClick={() => setSplit(true)}>나란히</button>
      </div>
      <span className="df-progress" title="확인함으로 표시한 파일">
        <span className="df-meter"><span style={{ width: `${(viewed / files.length) * 100}%` }} /></span>
        확인함 {viewed}/{files.length}
      </span>
      {d.comments.length > 0 && <span className="muted small">코멘트 {d.comments.length}</span>}
      {Object.keys(d.edits).length > 0 && <span className="muted small">직접 수정 {Object.keys(d.edits).length}</span>}
      {d.review && <span className="df-review-chip">내 리뷰 {REVIEW_LABEL[d.review.kind]} {NOT_SENT}</span>}
      {editable && <ReviewBox draft={d} update={update} />}
    </div>
  )

  const list = (
    <div className="rp-diffs">
      {shown.map((f) => (
        <FileBlock key={f.path} f={f} anchor={`rp-f-${draftKey}-${f.path}`} open={!shut.has(f.path)} onToggle={() => toggle(f.path)}
          viewed={d.viewed.includes(f.path)} onViewed={(on) => setViewed(f.path, on)} split={split} editable={editable} draft={d} update={update} />
      ))}
      {!shown.length && <div className="empty"><b>찾는 파일 없음</b></div>}
    </div>
  )

  if (compact) return <div className="df df-compact">{bar}{list}</div>
  return (
    <div className="df">
      {bar}
      <div className="rp-files">
        <nav className="rp-tree" aria-label="바뀐 파일">
          <input className="df-find" type="search" value={q} onChange={(e) => setQ(e.target.value)} placeholder="파일 찾기" aria-label="파일 찾기" />
          {shown.map((f) => {
            const n = d.comments.filter((c) => c.path === f.path).length
            return (
              <button key={f.path} type="button" onClick={() => jump(f.path)} title={f.path} className={d.viewed.includes(f.path) ? 'df-done' : undefined}>
                <span className="rp-tree-name">
                  {d.viewed.includes(f.path) && <span className="df-check" aria-label="확인함">✓</span>}
                  {f.path.split('/').pop()}<span className="muted small"> {f.path.split('/').slice(0, -1).join('/')}</span>
                </span>
                <span className="rp-tree-n">
                  {d.edits[f.path] && <span className="df-dot" title="직접 수정 · 레포 반영 전" />}
                  {n > 0 && <span className="muted small">💬{n} </span>}
                  <span className="rp-add">+{f.additions}</span> <span className="rp-del">−{f.deletions}</span>
                </span>
              </button>
            )
          })}
          {truncated && <span className="tag tag-amber">잘림 · {files.length}/{total ?? files.length}</span>}
        </nav>
        {list}
      </div>
    </div>
  )
}

function FileBlock({ f, anchor, open, onToggle, viewed, onViewed, split, editable, draft, update }: {
  f: RepoFile; anchor: string; open: boolean; onToggle: () => void; viewed: boolean; onViewed: (on: boolean) => void
  split: boolean; editable?: boolean; draft: Draft; update: (f: (d: Draft) => Draft) => void
}) {
  const toast = useToast()
  const rows = useMemo(() => (f.patch ? parsePatch(f.patch) : []), [f.patch])
  const base = useMemo(() => newSide(rows), [rows])
  const edit = draft.edits[f.path]
  const [editing, setEditing] = useState(false)
  const [text, setText] = useState(edit?.text ?? base)
  const [message, setMessage] = useState(edit?.message ?? `Update ${f.path.split('/').pop()}`)
  const mine = useMemo(() => (edit ? lineDiff(base.split('\n'), edit.text.split('\n')) : []), [edit, base])
  const comments = draft.comments.filter((c) => c.path === f.path)
  const save = () => {
    if (text === base) { update((x) => { const e = { ...x.edits }; delete e[f.path]; return { ...x, edits: e } }); setEditing(false); return }
    update((x) => ({ ...x, edits: { ...x.edits, [f.path]: { text, message: message.trim() || `Update ${f.path}`, at: stamp() } } }))
    setEditing(false)
    toast.show('수정을 저장했어요 · 레포 반영 전')
  }
  const undo = () => {
    update((x) => { const e = { ...x.edits }; delete e[f.path]; return { ...x, edits: e } })
    setText(base)
    toast.show('직접 수정을 지웠어요')
  }
  return (
    <section className={`rp-file${viewed ? ' df-viewed' : ''}`} id={anchor}>
      <div className="rp-file-h df-file-h">
        <button type="button" className="df-file-toggle" onClick={onToggle} aria-expanded={open}>
          <span aria-hidden className="rp-caret">{open ? '▾' : '▸'}</span>
          <code className="rp-file-path">{f.previous_path && f.previous_path !== f.path ? `${f.previous_path} → ` : ''}{f.path}</code>
        </button>
        <span className="tag">{FILE_STATUS[f.status] ?? f.status}</span>
        {f.truncated && <span className="tag tag-amber">잘림</span>}
        {edit && <span className="tag tag-amber" title={`${edit.message} · ${edit.at}`}>직접 수정 · 레포 반영 전</span>}
        <span className="rp-tree-n"><span className="rp-add">+{f.additions}</span> <span className="rp-del">−{f.deletions}</span></span>
        {editable && f.status !== 'removed' && !editing && (
          <button type="button" className="df-icon" title="이 파일 직접 편집" aria-label={`${f.path} 직접 편집`} onClick={() => { setEditing(true); setText(edit?.text ?? base) }}>
            <svg width="14" height="14" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="1.8" aria-hidden><path d="M4 20h4L19 9l-4-4L4 16z" /><path d="M14 6l4 4" /></svg>
          </button>
        )}
        <label className="df-viewed-box">
          <input type="checkbox" checked={viewed} onChange={(e) => onViewed(e.target.checked)} /> 확인함
        </label>
      </div>
      {open && editing && (
        <div className="df-editor">
          <div className="df-editor-h"><b>편집</b> <span className="muted small">바뀐 부분 주변 줄</span> {NOT_SENT}</div>
          <textarea value={text} onChange={(e) => setText(e.target.value)} spellCheck={false} rows={Math.min(24, Math.max(6, text.split('\n').length + 1))} aria-label={`${f.path} 내용`} />
          <div className="df-commit">
            <input value={message} onChange={(e) => setMessage(e.target.value)} placeholder="커밋 메시지" aria-label="커밋 메시지" />
            <span className="muted small">브랜치에 바로 커밋</span>
            <Button variant="primary" size="small" onClick={save}>변경 커밋</Button>
            <Button size="small" onClick={() => { setEditing(false); setText(edit?.text ?? base) }}>취소</Button>
          </div>
        </div>
      )}
      {open && !editing && (rows.length
        ? <Rows rows={rows} split={split} path={f.path} comments={comments} editable={editable} update={update} truncated={f.truncated} />
        : <div className="rp-nodiff muted small">{f.truncated ? '잘림' : 'diff 없음 (바이너리·큰 파일)'}</div>)}
      {open && !editing && edit && (
        <div className="df-mine">
          <div className="df-mine-h">
            <b>내 수정</b> <span className="mono small">{edit.message}</span> <span className="muted small">{edit.at}</span> {NOT_SENT}
            <span className="df-mine-act">
              <Button size="small" onClick={() => setEditing(true)}>다시 편집</Button>
              <Button size="small" onClick={undo}>수정 지우기</Button>
            </span>
          </div>
          <Rows rows={mine} split={split} path={f.path} comments={[]} />
        </div>
      )}
    </section>
  )
}

// diff 줄들. 한 줄 보기는 옛 번호·새 번호·부호·내용, 나란히 보기는 왼쪽(옛)·오른쪽(새)
function Rows({ rows, split, path, comments, editable, update, truncated }: {
  rows: DiffRow[]; split: boolean; path: string; comments: LineComment[]; editable?: boolean; update?: (f: (d: Draft) => Draft) => void; truncated?: boolean
}) {
  const [at, setAt] = useState<{ side: 'old' | 'new'; line: number } | null>(null)
  const add = (side: 'old' | 'new', line: number, body: string) => {
    update?.((x) => ({ ...x, comments: [...x.comments, { id: `${Date.now()}`, path, side, line, body, at: stamp() }] }))
    setAt(null)
  }
  const del = (id: string) => update?.((x) => ({ ...x, comments: x.comments.filter((c) => c.id !== id) }))
  const plus = (side: 'old' | 'new', line?: number) => editable && line != null
    ? <button type="button" className="df-plus" aria-label={`${line}번 줄 코멘트`} onClick={() => setAt({ side, line })}>+</button>
    : null
  const after = (side: 'old' | 'new', line?: number) => {
    if (line == null) return null
    const here = comments.filter((c) => c.side === side && c.line === line)
    const open = at?.side === side && at.line === line
    if (!here.length && !open) return null
    return (
      <div className="df-thread">
        {here.map((c) => (
          <div key={c.id} className="df-cmt">
            <div className="df-cmt-h"><b>나</b> <span className="muted small">{c.at}</span> {NOT_SENT} <button type="button" className="df-x" onClick={() => del(c.id)} aria-label="코멘트 지우기">×</button></div>
            <p>{c.body}</p>
          </div>
        ))}
        {open && <CommentForm onCancel={() => setAt(null)} onAdd={(b) => add(side, line, b)} />}
      </div>
    )
  }

  if (!split) {
    return (
      <div className="rp-code" role="table" aria-label={`${path} diff`}>
        {rows.map((r, k) => {
          const side = r.kind === 'del' ? 'old' : 'new'
          const line = r.kind === 'del' ? r.old : r.new
          return (
            <div key={k}>
              <div className={`rp-ln rp-ln-${r.kind}`} role="row">
                <span className="rp-no" role="cell">{r.old ?? ''}</span>
                <span className="rp-no" role="cell">{r.new ?? ''}</span>
                <span className="rp-sign" role="cell">{r.kind === 'hunk' || r.kind === 'note' ? '' : plus(side, line) ?? (r.kind === 'add' ? '+' : r.kind === 'del' ? '−' : '')}</span>
                <span className="rp-txt" role="cell">{r.text}</span>
              </div>
              {r.kind !== 'hunk' && r.kind !== 'note' && after(side, line)}
            </div>
          )
        })}
        {truncated && <div className="rp-ln rp-ln-note"><span className="rp-no" /><span className="rp-no" /><span className="rp-sign" /><span className="rp-txt">… 잘림</span></div>}
      </div>
    )
  }

  // 나란히: 지운 줄 묶음과 더한 줄 묶음을 짝지어 한 줄에
  const pairs: { l?: DiffRow; r?: DiffRow; full?: DiffRow }[] = []
  for (let i = 0; i < rows.length;) {
    const r = rows[i]
    if (r.kind === 'hunk' || r.kind === 'note') { pairs.push({ full: r }); i++; continue }
    if (r.kind === 'ctx') { pairs.push({ l: r, r }); i++; continue }
    const dels: DiffRow[] = []
    const adds: DiffRow[] = []
    while (i < rows.length && rows[i].kind === 'del') dels.push(rows[i++])
    while (i < rows.length && rows[i].kind === 'add') adds.push(rows[i++])
    for (let k = 0; k < Math.max(dels.length, adds.length); k++) pairs.push({ l: dels[k], r: adds[k] })
  }
  const cell = (x: DiffRow | undefined, side: 'old' | 'new') => {
    const n = side === 'old' ? x?.old : x?.new
    const kind = !x ? 'empty' : x.kind
    // 같은 줄(ctx)의 코멘트는 새 쪽에 단다 (한 줄 보기와 같게)
    const mark = !x ? null : x.kind === 'ctx' ? (side === 'new' ? plus('new', x.new) : null) : plus(x.kind === 'del' ? 'old' : 'new', n)
    return (
      <>
        <span className={`rp-no df-${kind}`} role="cell">{n ?? ''}</span>
        <span className={`rp-sign df-${kind}`} role="cell">{mark ?? (x?.kind === 'add' ? '+' : x?.kind === 'del' ? '−' : '')}</span>
        <span className={`rp-txt df-${kind}`} role="cell">{x?.text ?? ''}</span>
      </>
    )
  }
  return (
    <div className="rp-code df-split" role="table" aria-label={`${path} diff 나란히`}>
      {pairs.map((p, k) => p.full ? (
        <div key={k} className={`df-split-full rp-ln-${p.full.kind}`} role="row"><span className="rp-txt">{p.full.text}</span></div>
      ) : (
        <div key={k}>
          <div className="df-split-row" role="row">{cell(p.l, 'old')}{cell(p.r, 'new')}</div>
          {p.l?.kind === 'del' && after('old', p.l.old)}
          {p.r && after('new', p.r.new)}
        </div>
      ))}
    </div>
  )
}

function CommentForm({ onAdd, onCancel }: { onAdd: (body: string) => void; onCancel: () => void }) {
  const [body, setBody] = useState('')
  return (
    <div className="df-cmt-form">
      <textarea autoFocus value={body} onChange={(e) => setBody(e.target.value)} rows={3} placeholder="코멘트" aria-label="줄 코멘트" />
      <div className="df-cmt-act">
        <Button size="small" onClick={onCancel}>취소</Button>
        <Button variant="primary" size="small" disabled={!body.trim()} onClick={() => onAdd(body.trim())}>코멘트 추가</Button>
      </div>
    </div>
  )
}

// 리뷰 끝내기: 요약 + 코멘트 / 승인 / 수정 요청
function ReviewBox({ draft, update }: { draft: Draft; update: (f: (d: Draft) => Draft) => void }) {
  const toast = useToast()
  const [open, setOpen] = useState(false)
  const [kind, setKind] = useState<ReviewKind>(draft.review?.kind ?? 'comment')
  const [body, setBody] = useState(draft.review?.body ?? '')
  const ref = useRef<HTMLDivElement>(null)
  const send = () => {
    update((x) => ({ ...x, review: { kind, body: body.trim(), at: stamp() } }))
    setOpen(false)
    toast.show(`리뷰를 남겼어요 · ${REVIEW_LABEL[kind]} · 레포 반영 전`)
  }
  return (
    <div className="df-review" ref={ref}>
      <Button variant="primary" size="small" aria-haspopup="dialog" aria-expanded={open} onClick={() => setOpen((v) => !v)}>
        리뷰 끝내기{draft.comments.length ? ` (${draft.comments.length})` : ''} ▾
      </Button>
      {open && (
        <div className="df-review-pop" role="dialog" aria-label="리뷰 끝내기">
          <textarea value={body} onChange={(e) => setBody(e.target.value)} rows={4} placeholder="리뷰 요약" aria-label="리뷰 요약" />
          {(Object.keys(REVIEW_LABEL) as ReviewKind[]).map((k) => (
            <label key={k} className="df-radio">
              <input type="radio" name="review-kind" checked={kind === k} onChange={() => setKind(k)} /> <b>{REVIEW_LABEL[k]}</b>
            </label>
          ))}
          <div className="df-cmt-act">
            <Button size="small" onClick={() => setOpen(false)}>닫기</Button>
            <Button variant="primary" size="small" disabled={kind !== 'approve' && !body.trim() && !draft.comments.length} onClick={send}>리뷰 보내기</Button>
          </div>
        </div>
      )}
    </div>
  )
}
