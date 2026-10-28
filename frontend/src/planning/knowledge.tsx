// 규정·자료: 회사 규정·업무 매뉴얼 같은 파일을 올려 두면 AI가 근거로 학습할 자료가 된다.
// RAG 연동(텍스트 추출·색인·검색)은 나중에 붙인다. 지금은 올리기·분류·미리보기·삭제만 하고, 상태는 "학습 대기"로 둔다.
// 목록(파일 이름·분류·크기·시각)은 이 브라우저에만 남는다 (연동 전 시연용). 파일 내용은 저장하지 않는다.
import { useEffect, useRef, useState, type DragEvent } from 'react'
import {Button, Card, Notice, PageHeader, Select, useToast } from '@dejafix/common-frontend'
import { Tag } from './parts'

const KINDS = ['회사 규정', '업무 매뉴얼', '기획 가이드', '용어·정책', '기타'] as const
type Kind = (typeof KINDS)[number]
// 지금 미리보기로 읽을 수 있는 형식. PDF·DOCX는 텍스트 추출이 RAG 연동 때 들어온다
const TEXT_EXT = ['md', 'txt', 'csv', 'json']
const ACCEPT = '.pdf,.docx,.md,.txt,.csv,.json,.hwp,.xlsx'

interface KFile { id: string; name: string; size: number; kind: Kind; at: string; by: string; preview?: string }

const STORE = 'planning-web.knowledge'
const load = (): KFile[] => {
  try { return JSON.parse(localStorage.getItem(STORE) ?? '[]') } catch { return [] }
}
const save = (x: KFile[]) => {
  try { localStorage.setItem(STORE, JSON.stringify(x)) } catch { /* 저장 못 하면 이 화면에서만 유지 */ }
}

const ext = (name: string) => name.split('.').pop()?.toLowerCase() ?? ''
const size = (n: number) => (n < 1024 ? `${n}B` : n < 1024 * 1024 ? `${(n / 1024).toFixed(1)}KB` : `${(n / 1024 / 1024).toFixed(1)}MB`)
const stamp = () => {
  const d = new Date()
  const p = (x: number) => String(x).padStart(2, '0')
  return `${p(d.getMonth() + 1)}-${p(d.getDate())} ${p(d.getHours())}:${p(d.getMinutes())}`
}
// 파일 이름으로 분류를 먼저 고른다 (사람이 바꿀 수 있다)
const guess = (name: string): Kind =>
  /규정|규칙|정책|취업|보안|윤리/.test(name) ? '회사 규정' : /매뉴얼|가이드|안내|절차/.test(name) ? '업무 매뉴얼' : /기획|템플릿|요구사항/.test(name) ? '기획 가이드' : /용어|정의/.test(name) ? '용어·정책' : '기타'

export function Knowledge() {
  const toast = useToast()
  const [files, setFiles] = useState<KFile[]>(load)
  const [filter, setFilter] = useState<Kind | '전체'>('전체')
  const [open, setOpen] = useState<string>()
  const [over, setOver] = useState(false)
  const input = useRef<HTMLInputElement>(null)
  useEffect(() => save(files), [files])

  const add = async (list: FileList | null) => {
    if (!list?.length) return
    const added: KFile[] = []
    for (const f of Array.from(list)) {
      const text = TEXT_EXT.includes(ext(f.name)) ? (await f.text()).slice(0, 1200) : undefined
      added.push({ id: `KB-${Date.now()}-${added.length}`, name: f.name, size: f.size, kind: guess(f.name), at: stamp(), by: '노예진', preview: text })
    }
    setFiles((x) => [...added, ...x])
    toast.show(`${added.length}개 올림 · RAG 연동 뒤 자동으로 학습`)
  }
  const onDrop = (e: DragEvent) => {
    e.preventDefault()
    setOver(false)
    add(e.dataTransfer.files)
  }

  const rows = files.filter((f) => filter === '전체' || f.kind === filter)
  const cur = files.find((f) => f.id === open)

  return (
    <>
      <PageHeader title="규정·자료" sub="회사 규정·업무 매뉴얼·기획 가이드를 올려 두면 AI가 기획서를 쓰고 검토할 때 근거로 인용한다. 규정에 없는 내용은 지어내지 않고 '확인할 수 없음'으로 답한다." />
      <Notice icon="bell">RAG 연동(텍스트 추출·색인·검색)은 다음 단계입니다. 지금은 파일을 올리고 분류만 하며, 모든 파일은 <b>학습 대기</b> 상태입니다. 목록은 이 브라우저에만 남습니다.</Notice>

      <div className="kb-stats">
        <span>올린 자료 <b>{files.length}</b></span>
        <span>회사 규정 <b>{files.filter((f) => f.kind === '회사 규정').length}</b></span>
        <span>학습 완료 <b>0</b> <span className="muted">(RAG 연동 뒤 집계)</span></span>
        <span>학습 대기 <b>{files.length}</b></span>
      </div>

      <div
        className={`kb-drop${over ? ' over' : ''}`}
        onDragOver={(e) => { e.preventDefault(); setOver(true) }}
        onDragLeave={() => setOver(false)}
        onDrop={onDrop}
      >
        <b>파일을 여기에 끌어 놓거나</b>
        <Button variant="primary" onClick={() => input.current?.click()}>파일 고르기</Button>
        <span className="muted small">PDF·DOCX·HWP·XLSX·MD·TXT·CSV·JSON · 텍스트 형식은 바로 미리보기</span>
        <input ref={input} type="file" multiple accept={ACCEPT} hidden onChange={(e) => { add(e.target.files); e.target.value = '' }} />
      </div>

      <div className="chips" style={{ margin: '14px 0' }}>
        {(['전체', ...KINDS] as const).map((k) => (
          <button key={k} type="button" className={`q-chip${filter === k ? ' on' : ''}`} onClick={() => setFilter(k)}>
            {k} {k === '전체' ? files.length : files.filter((f) => f.kind === k).length}
          </button>
        ))}
      </div>

      <div className="split">
        <Card as="section" title="자료 목록" sub={`${rows.length}개`}>
          {rows.length ? (
            <table className="auto-table">
              <thead><tr><th>파일</th><th>분류</th><th>크기</th><th>올린 시각</th><th>상태</th><th /></tr></thead>
              <tbody>
                {rows.map((f) => (
                  <tr key={f.id} className={f.id === open ? 'on' : ''}>
                    <td><button type="button" className="linkish" onClick={() => setOpen(f.id)}>{f.name}</button></td>
                    <td>
                      <Select value={f.kind} aria-label={`${f.name} 분류`} onChange={(e) => { const kind = e.target.value as Kind; setFiles((x) => x.map((y) => (y.id === f.id ? { ...y, kind } : y))) }} options={KINDS.map((k) => ({ value: k, label: k }))} />
                    </td>
                    <td className="mono small">{size(f.size)}</td>
                    <td className="mono small">{f.at}</td>
                    <td><Tag tone="amber">학습 대기</Tag></td>
                    <td><button type="button" className="blk-x" aria-label={`${f.name} 삭제`} onClick={() => { setFiles((x) => x.filter((y) => y.id !== f.id)); if (open === f.id) setOpen(undefined) }}>×</button></td>
                  </tr>
                ))}
              </tbody>
            </table>
          ) : <span className="muted">아직 올린 자료가 없습니다. 회사 규정이나 업무 매뉴얼부터 올려 보세요.</span>}
        </Card>

        <Card as="section" title={cur ? cur.name : '자료 상세'} sub={cur ? `${cur.kind} · ${size(cur.size)} · ${cur.by} · ${cur.at}` : '목록에서 파일을 고르세요'}>
          {cur ? (
            <>
              <ol className="flow small">
                <li>올림 <Tag tone="green">완료</Tag></li>
                <li>텍스트 추출 <Tag>RAG 연동 뒤</Tag></li>
                <li>문단 단위 색인 <Tag>RAG 연동 뒤</Tag></li>
                <li>기획서 작성·검토 때 근거로 인용 (문단 id + 파일 이름) <Tag>RAG 연동 뒤</Tag></li>
              </ol>
              {cur.preview ? <pre className="kb-preview">{cur.preview}{cur.preview.length >= 1200 ? '\n…' : ''}</pre> : <div className="muted small">{ext(cur.name).toUpperCase()} 파일은 텍스트 추출이 RAG 연동 때 들어와서 지금은 미리보기가 없습니다.</div>}
            </>
          ) : <span className="muted small">파일 이름을 누르면 학습 단계와 미리보기가 보입니다.</span>}
        </Card>
      </div>
    </>
  )
}
