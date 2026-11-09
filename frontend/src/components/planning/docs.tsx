// 정리 문서: 요청 하나에 흩어진 기획서·작업·PR·대화·결정·장애를 한 장으로 모으고, 사람이 고친다.
// 수정은 칸에서 벗어날 때(blur) 저장한다. 연동 버스에 붙어 있으면 다른 화면에도 바로 보인다.
import { useState, type Dispatch } from 'react'
import {Button, PageHeader, Select, useToast } from '@dejafix/common-frontend'
import { DEPT_NAME, type Action, type Doc, type DocBlock, type State } from '@/lib/planningData'
import { Linked, Ref, go, idText, toPath } from '@/components/planning-common/links'
import { Sec, Tag, rowNav } from '@/components/planning-common/parts'
import { Pager, usePage } from '@/components/planning-common/pager'
import { MdEditable } from '@/components/planning-common/markdown'

// 문서 탭 첫 화면 = 문서 목록 (최근 것이 위). 줄을 누르면 그 문서를 열어 고친다
export function Docs({ s, d, item }: { s: State; d: Dispatch<Action>; item?: string }) {
  const toast = useToast()
  const setSel = (id: string) => go(toPath(`docs/${id}`))
  const [about, setAbout] = useState(s.requests[0]?.id ?? '')
  const doc = item ? s.docs.find((x) => x.id === item) : undefined
  const lastId = `DOC-${s.docs.length + 1}`
  const make = (
    <div className="dl-make">
      <Button size="small" onClick={() => { d({ type: 'newDoc', title: '' }); setSel(lastId) }}>+ 새 문서</Button>
      <Select value={about} onChange={(e) => setAbout(e.target.value)} options={s.requests.map((r) => ({ value: r.id, label: `${r.id} ${r.title}` }))} />
      <Button size="small" variant="primary" icon="sparkle" onClick={() => { d({ type: 'gatherDoc', about }); setSel(lastId); toast.show(`${about}에 걸린 것을 모아 ${lastId}로 정리`) }}>모아서 문서 만들기</Button>
    </div>
  )

  const pg = usePage(s.docs.slice().reverse(), 10)
  if (!item) {
    const rows = pg.rows
    return (
      <>
        <PageHeader title="문서" sub="요청에 걸린 기획서·작업·PR·대화·결정을 한 장으로 모으고 사람이 고친다." />
        {make}
        {!rows.length ? <div className="empty"><b>문서 없음</b></div> : (
          <div className="sl">
            <div className="sl-head dl-grid" aria-hidden><span>문서</span><span>요청</span><span>할 일</span><span>마지막 수정</span></div>
            <ol className="sl-list">
              {rows.map((x) => {
                const r = x.about ? s.requests.find((q) => q.id === x.about) : undefined
                const todos = x.blocks.filter((b) => b.kind === 'todo')
                const nav = rowNav(toPath(`docs/${x.id}`), `${x.id} 열기`)
                return (
                  <li key={x.id} {...nav} className={`sl-item dl-grid ${nav.className ?? ''}`}>
                    <span className="sl-title">
                      <span className="mono idc sl-id" title={x.id}>{idText(x.id)}</span>
                      <b title={x.title}>{x.title || '제목 없는 문서'}</b>
                      {x.by === 'ai' && <Tag tone="green">AI 정리</Tag>}
                    </span>
                    <span className="sl-title">{r ? <><span className={`rq-dept d-${r.from}`}>{DEPT_NAME[r.from]}</span><span className="mono idc sl-id" title={r.id}>{idText(r.id)}</span></> : <span className="muted small">직접 쓴 문서</span>}</span>
                    <span className="muted small">{todos.length ? `${todos.filter((b) => b.done).length}/${todos.length}` : '—'}</span>
                    <span className="muted small">{x.updated}</span>
                  </li>
                )
              })}
            </ol>
            <Pager {...pg} />
          </div>
        )}
      </>
    )
  }

  return (
    <>
    <div className="three">
      <aside className="col-left">
        <a href={toPath('work/specs')} className="plain-link small">‹ 문서·기획서 목록</a>
        {doc && <DocOutline doc={doc} />}
      </aside>

      <section className="col-doc">{doc ? <Editor key={doc.id} doc={doc} d={d} s={s} /> : <span className="muted">{item} 문서가 없습니다.</span>}</section>

      <aside className="col-right">
        {doc && <Related doc={doc} s={s} />}
      </aside>
    </div>
    </>
  )
}

// 문서 개요 = 목차: 소제목마다 누르면 그 자리로, 할 일은 끝난 수/전체
function DocOutline({ doc }: { doc: Doc }) {
  const heads = doc.blocks.filter((b) => b.kind === 'h2' && b.text.trim())
  const todos = doc.blocks.filter((b) => b.kind === 'todo')
  const jump = (id: string) => document.getElementById(`blk-${id}`)?.scrollIntoView({ behavior: 'smooth', block: 'center' })
  return (
    <>
      <div className="ph" style={{ marginTop: 10 }}>{doc.id} 개요</div>
      <ul className="outline">
        {heads.length ? heads.map((b) => <li key={b.id}><button type="button" onClick={() => jump(b.id)}>{b.text}</button></li>) : <li className="muted">소제목 없음</li>}
        {todos.length > 0 && <li><button type="button" onClick={() => jump(todos[0].id)}>할 일 {todos.filter((b) => b.done).length}/{todos.length}</button></li>}
      </ul>
    </>
  )
}

function Editor({ doc, d, s }: { doc: Doc; d: Dispatch<Action>; s: State }) {
  const toast = useToast()
  const todos = doc.blocks.filter((b) => b.kind === 'todo')
  // 글은 칸에서 벗어날 때 저장된다. "저장"은 지금 쓰던 칸을 바로 저장한다
  const saveNow = () => { (document.activeElement as HTMLElement | null)?.blur(); toast.show(`${doc.id} 저장됨`) }
  const remove = () => {
    if (!window.confirm(`${doc.id} "${doc.title}" 문서를 지울까요? 되돌릴 수 없어요.`)) return
    d({ type: 'deleteDoc', doc: doc.id })
    toast.show(`${doc.id} 삭제`)
    go(toPath('work/specs'))
  }
  return (
    <>
      <div className="doc-actions">
        <Button size="small" variant="primary" onClick={saveNow}>저장</Button>
        <Button size="small" onClick={remove}>삭제</Button>
      </div>
      <input
        key={`title-${doc.title}`}
        className="doc-title-input"
        defaultValue={doc.title}
        placeholder="제목 없는 문서"
        aria-label="문서 제목"
        onBlur={(e) => e.target.value !== doc.title && d({ type: 'renameDoc', doc: doc.id, title: e.target.value })}
      />
      <p className="muted small" style={{ margin: 0 }}>
        <span className="mono idc" title={doc.id}>{idText(doc.id)}</span> · 마지막 수정 {doc.updated}
        {todos.length > 0 && <> · 할 일 {todos.filter((b) => b.done).length}/{todos.length}</>}
        {doc.about && <> · 요청 <Ref s={s} id={doc.about} /> · <a href={toPath(`projects/${doc.about}`)}>프로젝트 보기</a></>}
        {doc.by === 'ai' && <> · AI 어시스턴트가 정리</>}
      </p>
      <div className="doc-blocks">
        {doc.blocks.map((b) => <div key={`${b.id}-${b.text}-${b.done}`} id={`blk-${b.id}`}><Block b={b} doc={doc.id} d={d} s={s} /></div>)}
      </div>
      <div className="actions">
        <Button size="small" onClick={() => d({ type: 'addBlock', doc: doc.id, kind: 'h2' })}>+ 소제목</Button>
        <Button size="small" onClick={() => d({ type: 'addBlock', doc: doc.id, kind: 'p' })}>+ 문단</Button>
        <Button size="small" onClick={() => d({ type: 'addBlock', doc: doc.id, kind: 'todo' })}>+ 할 일</Button>
      </div>
    </>
  )
}

function Block({ b, doc, d, s }: { b: DocBlock; doc: string; d: Dispatch<Action>; s: State }) {
  const save = (text: string) => text !== b.text && d({ type: 'editBlock', doc, block: b.id, text })
  const remove = <button type="button" className="blk-x" aria-label="블록 삭제" onClick={() => d({ type: 'removeBlock', doc, block: b.id })}>×</button>
  if (b.kind === 'h2') {
    return <div className="blk">{<input className="blk-h2" defaultValue={b.text} placeholder="소제목" onBlur={(e) => save(e.target.value)} />}{remove}</div>
  }
  if (b.kind === 'todo') {
    return (
      <div className="blk blk-todo">
        <input type="checkbox" checked={!!b.done} onChange={() => d({ type: 'toggleTodo', doc, block: b.id })} aria-label="완료" />
        <input className={`blk-line${b.done ? ' done' : ''}`} defaultValue={b.text} placeholder="할 일" onBlur={(e) => save(e.target.value)} />
        {remove}
      </div>
    )
  }
  if (b.kind === 'ref') {
    return <div className="blk blk-ref"><Tag>연결</Tag><span className="blk-ref-text"><Linked s={s} text={b.text} /></span>{remove}</div>
  }
  return (
    <div className="blk">
      <MdEditable value={b.text} onSave={save} placeholder="내용을 쓰세요 (마크다운: # 제목, - 목록, | 표 |)" minRows={1} label="문단" />
      {remove}
    </div>
  )
}

function Related({ doc, s }: { doc: Doc; s: State }) {
  const r = doc.about ? s.requests.find((x) => x.id === doc.about) : undefined
  const spec = r?.spec ? s.specs.find((x) => x.id === r.spec) : undefined
  return (
    <>
      <div className="ph">이 문서에 연결된 것</div>
      <Sec title="요청">{r ? <div className="msg"><Ref s={s} id={r.id} /> {r.title} <Tag>{r.state}</Tag></div> : <span className="muted">직접 쓴 문서 (요청 연결 없음)</span>}</Sec>
      <Sec title="기획 문서">{spec ? <div className="msg"><Ref s={s} id={spec.id} /> {spec.title} <Tag>{spec.status}</Tag></div> : <span className="muted">없음</span>}</Sec>
      <Sec title="내보내기" sub="노션·슬랙은 가져오기/내보내기만 한다">
        <span className="muted small">notion_export_page · slack_post_message (실제 연결은 MCP 도구가 생긴 뒤)</span>
      </Sec>
    </>
  )
}
