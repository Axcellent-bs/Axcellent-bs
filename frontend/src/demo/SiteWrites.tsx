import { useCallback, useEffect, useState } from 'react'
import { More, Notice, SectionHead, StatusChip } from '@/components/connect/parts'
import { ApiError } from '@/lib/apiError'
import { ago } from '@/lib/format'
import { siteAdapter, siteWrites, type SiteSample } from '@/lib/siteAdapter'
import {
  ACTION_LABEL, CLAIM_FIELDS, WRITE_METHODS, claimBlanks, cleanInbox, parseCodes, parseList, startWrite, syncParams, writeChip, writeMapProblems,
  type InboxMap, type RecentWrite, type SiteWritesState, type WriteActionState, type WriteMap, type WriteMethod, type WritePath,
} from '@/lib/siteWrites'

// 회사 연결 › 다른 형식 API › 사이트 쓰기 (사이트 변환기 ③-쓰기 W4).
// AI가 판매 서버로 알고 보내는 쓰기(문의 답변·환불 등)를 사이트에 직접 쓸지 동작마다 정한다.
// 순서: 쓰기 경로 찾기(openapi.json) → 동작마다 경로·메서드·본문 짝 고르기 → 확인하고 저장 → 직접 쓰기 켜기(기본 꺼짐).
// 켜지 않은 동작, 짝이 없는 동작, 키에 쓰기 권한이 없는 동작은 사이트에 쓰지 않고 사람 할 일로 넘어간다.
// 환불·쿠폰은 돈이 걸린 동작이라 안전 관문에서 사람이 승인한 쓰기만 사이트로 간다(켤 때 확인 문구).
const message = (e: unknown) => (e instanceof ApiError || e instanceof Error ? e.message : '요청에 실패했습니다')
const FIRST = 2 // 먼저 보이는 동작 수 (문의 답변·환불)
const CLAIM_STATUS = [
  { value: '', label: '정하지 않음' },
  { value: 'requested', label: '결정 전' },
  { value: 'closed', label: '끝남' },
]

interface Props {
  baseUrl: string
  authHeader: string
  authScheme: string
  canWrite: boolean
}

export default function SiteWrites({ baseUrl, authHeader, authScheme, canWrite }: Props) {
  const [state, setState] = useState<SiteWritesState | null>(null)
  const [recent, setRecent] = useState<RecentWrite[] | null>(null)
  const [loadError, setLoadError] = useState<string | null>(null)
  const [writePaths, setWritePaths] = useState<WritePath[] | null>(null)
  const [readPaths, setReadPaths] = useState<string[]>([])
  const [looking, setLooking] = useState(false)
  const [lookError, setLookError] = useState<string | null>(null)

  const reload = useCallback(() => {
    siteWrites.get().then(setState).catch((e) => setLoadError(message(e)))
    siteWrites.recent().then((r) => setRecent(r.items)).catch(() => setRecent(null))
  }, [])
  useEffect(() => { reload() }, [reload])

  const look = async () => {
    setLooking(true); setLookError(null)
    try {
      const found = await siteAdapter.propose({ base_url: baseUrl, auth_header: authHeader, auth_scheme: authScheme })
      setWritePaths(found.write_paths ?? []); setReadPaths(found.paths)
    } catch (e) { setLookError(message(e)) } finally { setLooking(false) }
  }

  const first = state?.actions.slice(0, FIRST) ?? []
  const rest = state?.actions.slice(FIRST) ?? []
  const card = (a: WriteActionState) => (
    <ActionCard key={a.action} item={a} candidates={writePaths ?? []} canWrite={canWrite} onState={setState} />
  )

  return (
    <section className="space-y-4 border-t border-ink-100 pt-4" aria-label="사이트 쓰기">
      <SectionHead as="h3" title="사이트 쓰기"
        desc="AI가 보내는 쓰기(문의 답변·환불 등)를 사이트에 바로 쓸지 동작마다 정해요. 켜지 않은 동작은 사이트에 쓰지 않고 사람 할 일로 넘어가요." />
      {loadError && <Notice tone="bad">사이트 쓰기 상태를 읽지 못했어요: {loadError}</Notice>}
      {state?.broken && <Notice tone="bad">짝 파일이 깨져 쓰기 짝을 읽지 못했어요. 위에서 주문·상품 짝을 다시 연결해 주세요.</Notice>}
      <p className="text-xs leading-5 text-ink-500">사이트 키는 쓰기 권한(write)이 있어야 해요. 사이트 감사 기록에 키 이름이 남으니 키 이름을 <span className="font-mono">AXcellent</span>로 발급해 주세요.</p>

      <div className="flex flex-wrap items-center gap-2">
        <button type="button" className="btn-ghost" onClick={() => void look()} disabled={looking || !canWrite || !baseUrl}>
          {looking ? '찾는 중…' : '사이트 쓰기 경로 찾기'}
        </button>
        {writePaths && (
          <span role="status" className="text-xs text-ink-500">
            {writePaths.length ? `쓰기 경로 ${writePaths.length}개를 찾았어요. 동작마다 후보에서 골라요.` : '사이트가 쓰기 경로 목록(openapi.json)을 알려 주지 않아요. 경로를 직접 넣어 주세요.'}
          </span>
        )}
        {lookError && <span role="alert" className="text-xs text-red-700">{lookError}</span>}
      </div>

      {state && (
        <div className="space-y-3">
          {first.map(card)}
          {rest.length > 0 && <More summary={`다른 동작 ${rest.length}개 (쿠폰·공지·신청 결과·결제 조치)`}><div className="space-y-3">{rest.map(card)}</div></More>}
        </div>
      )}

      {state && (
        <ClaimsInbox saved={state.inboxes.claims} readPaths={readPaths} baseUrl={baseUrl} authHeader={authHeader} authScheme={authScheme}
          canWrite={canWrite} onState={setState} />
      )}

      <RecentWrites rows={recent} onReload={reload} />
    </section>
  )
}

// ---- 동작 하나: 짝·켜기·키 권한 ----

interface Row { name: string; expr: string }
const rowsOf = (o: Record<string, string>): Row[] => Object.entries(o).map(([name, expr]) => ({ name, expr }))

function ActionCard({ item, candidates, canWrite, onState }: {
  item: WriteActionState; candidates: WritePath[]; canWrite: boolean; onState: (s: SiteWritesState) => void
}) {
  const [editing, setEditing] = useState(false)
  const [confirming, setConfirming] = useState(false)
  const [busy, setBusy] = useState(false)
  const [error, setError] = useState<string | null>(null)
  const label = ACTION_LABEL[item.action]

  const act = async (work: () => Promise<SiteWritesState>) => {
    setBusy(true); setError(null)
    try { onState(await work()); return true } catch (e) { setError(message(e)); return false } finally { setBusy(false) }
  }
  const toggle = () => {
    if (item.enabled) { void act(() => siteWrites.switch(item.action, false)); return }
    if (item.money) { setConfirming(true); return }
    void act(() => siteWrites.switch(item.action, true))
  }

  return (
    <div className="card space-y-3 p-4" aria-label={`${label} 쓰기`}>
      <div className="flex flex-wrap items-start justify-between gap-2">
        <div>
          <p className="text-sm font-medium text-ink-900">{label}</p>
          <p className="mt-0.5 text-xs text-ink-500">{item.title} · 보내는 AI {item.service}</p>
        </div>
        <div className="flex flex-wrap items-center gap-1.5">
          {item.money && <StatusChip tone="wait">돈이 걸린 동작</StatusChip>}
          {item.denied_at && <StatusChip tone="bad">키에 쓰기 권한 없음</StatusChip>}
          {item.write ? <StatusChip tone="ok">짝 있음</StatusChip> : <StatusChip tone="off">짝 없음</StatusChip>}
          {item.enabled ? <StatusChip tone="ok">직접 쓰기 켜짐</StatusChip> : <StatusChip tone="off">직접 쓰기 꺼짐</StatusChip>}
        </div>
      </div>

      {item.write && !editing && (
        <p className="font-mono text-xs text-ink-700">{item.write.method} {item.write.path}</p>
      )}

      {item.denied_at && (
        <Notice tone="bad">
          {ago(item.denied_at)} 사이트가 이 쓰기를 키 권한 없음(401·403)으로 막았어요. 그 뒤로 이 동작은 사람 할 일로 가요.
          사이트에서 쓰기 권한(write)이 있는 키로 바꿔 위에서 다시 연결한 뒤 표시를 풀어 주세요. 다음 쓰기가 다시 확인해요.
          <span className="mt-2 block">
            <button type="button" className="btn-ghost" disabled={busy || !canWrite} onClick={() => void act(() => siteWrites.clearDenied(item.action))}>키를 바꿨어요 · 표시 풀기</button>
          </span>
        </Notice>
      )}

      <div className="flex flex-wrap items-center gap-2">
        <label className="flex items-center gap-2 text-sm text-ink-700">
          <input type="checkbox" checked={item.enabled} disabled={busy || !canWrite || (!item.enabled && !item.write)} onChange={toggle} />
          사이트에 직접 쓰기
        </label>
        {!item.write && !item.enabled && <span className="text-xs text-ink-500">짝을 저장하면 켤 수 있어요</span>}
        <button type="button" className="btn-ghost" onClick={() => setEditing((v) => !v)} disabled={!canWrite}>{editing ? '짝 닫기' : item.write ? '짝 고치기' : '짝 정하기'}</button>
      </div>

      {confirming && (
        <div role="alertdialog" aria-label={`${label} 직접 쓰기 켜기 확인`} className="space-y-2 border-l-2 border-amber-700 bg-amber-50 px-4 py-3 text-sm leading-6 text-amber-800">
          <p>{label}는 돈이 걸린 동작이에요. 켜면 안전 관문에서 담당자가 승인한 금액이 사람 손을 거치지 않고 사이트에 바로 반영돼요(결제사 환불까지 이어질 수 있어요).</p>
          <p>관문 기록과 금액이 맞지 않는 쓰기는 보내지 않고, 사이트가 답을 주지 않으면 다시 보내지 않고 사람 할 일로 넘겨요.</p>
          <div className="flex flex-wrap gap-2">
            <button type="button" className="btn-primary" disabled={busy}
              onClick={() => void act(() => siteWrites.switch(item.action, true, true)).then((ok) => { if (ok) setConfirming(false) })}>
              확인했어요 · 켜기
            </button>
            <button type="button" className="btn-ghost" onClick={() => setConfirming(false)}>취소</button>
          </div>
        </div>
      )}

      {editing && (
        <WriteEditor action={label} saved={item.write} candidates={candidates}
          onSave={(w) => act(() => siteWrites.saveMap(item.action, w)).then((ok) => { if (ok) setEditing(false) })} busy={busy} />
      )}
      {error && <p role="alert" className="text-sm text-red-700">{error}</p>}
    </div>
  )
}

function WriteEditor({ action, saved, candidates, onSave, busy }: {
  action: string; saved: WriteMap | null; candidates: WritePath[]; onSave: (w: WriteMap | null) => void; busy: boolean
}) {
  const [method, setMethod] = useState<WriteMethod>(saved?.method ?? 'POST')
  const [path, setPath] = useState(saved?.path ?? '')
  const [params, setParams] = useState<Record<string, string>>(saved?.path_params ?? {})
  const [body, setBody] = useState<Row[]>(rowsOf(saved?.body ?? {}))
  const [okText, setOkText] = useState((saved?.ok ?? [200, 201]).join(', '))
  const [partialText, setPartialText] = useState((saved?.partial_codes ?? []).join(', '))

  const changePath = (next: string) => { setPath(next); setParams((p) => syncParams(next, p)) }
  const pickCandidate = (value: string) => {
    const found = candidates[Number(value)]
    if (!found) return
    const w = startWrite(found.method, found.path)
    setMethod(w.method); setPath(w.path); setParams((p) => syncParams(w.path, p))
  }
  const codes = parseCodes(okText)
  const draft: WriteMap = {
    method, path, path_params: params,
    body: Object.fromEntries(body.map((r) => [r.name.trim(), r.expr.trim()])),
    ok: codes ?? [],
    ...(saved?.result && Object.keys(saved.result).length ? { result: saved.result } : {}),
    ...(parseList(partialText).length ? { partial_codes: parseList(partialText) } : {}),
  }
  const problems = [...writeMapProblems(draft), ...(codes ? [] : ['성공 코드는 200, 201처럼 세 자리 숫자로 적어 주세요'])]
  const names = body.map((r) => r.name.trim()).filter(Boolean)
  if (new Set(names).size !== names.length) problems.push('본문 칸 이름이 겹쳐요')

  return (
    <div className="space-y-3 border-t border-ink-100 pt-3">
      {candidates.length > 0 && (
        <label className="block space-y-1 text-sm">
          <span className="text-ink-700">사이트 쓰기 경로 후보</span>
          <select className="field w-full font-mono" aria-label={`${action} 쓰기 경로 후보`} value="" onChange={(e) => pickCandidate(e.target.value)}>
            <option value="">후보에서 고르기</option>
            {candidates.map((c, i) => <option key={`${c.method} ${c.path}`} value={i}>{c.method} {c.path}</option>)}
          </select>
        </label>
      )}
      <div className="grid gap-2 sm:grid-cols-[8rem_1fr]">
        <label className="space-y-1 text-sm">
          <span className="text-ink-700">메서드</span>
          <select className="field w-full font-mono" value={method} onChange={(e) => setMethod(e.target.value as WriteMethod)}>
            {WRITE_METHODS.map((m) => <option key={m} value={m}>{m}</option>)}
          </select>
        </label>
        <label className="space-y-1 text-sm">
          <span className="text-ink-700">사이트 경로</span>
          <input className="field w-full font-mono" value={path} placeholder="/api/v1/inquiries/{site_id}/answer" onChange={(e) => changePath(e.target.value)} />
        </label>
      </div>

      {Object.keys(params).length > 0 && (
        <table className="w-full text-sm" aria-label={`${action} 경로 칸 짝`}>
          <caption className="pb-1 text-left text-xs text-ink-500">경로 칸 · 사이트 쪽 번호는 보통 <span className="font-mono">link:</span>로 찾아요</caption>
          <tbody>
            {Object.entries(params).map(([name, expr]) => (
              <tr key={name} className="border-t border-ink-100">
                <td className="py-1.5 pr-2 font-mono">{`{${name}}`}</td>
                <td className="py-1.5">
                  <input aria-label={`경로 칸 ${name} 짝 식`} className={`field w-full font-mono ${expr.trim() ? '' : 'border-amber-700'}`} value={expr}
                    placeholder="link:inquiries.seq" onChange={(e) => setParams((p) => ({ ...p, [name]: e.target.value }))} />
                </td>
              </tr>
            ))}
          </tbody>
        </table>
      )}

      <table className="w-full text-sm" aria-label={`${action} 본문 짝`}>
        <caption className="pb-1 text-left text-xs text-ink-500">본문 · 왼쪽이 사이트가 받는 칸, 오른쪽이 AI 요청에서 꺼낼 값</caption>
        <tbody>
          {body.map((r, i) => (
            <tr key={i} className="border-t border-ink-100">
              <td className="py-1.5 pr-2">
                <input aria-label={`본문 칸 ${i + 1} 이름`} className="field w-full font-mono" value={r.name} placeholder="answer"
                  onChange={(e) => setBody((b) => b.map((x, j) => (j === i ? { ...x, name: e.target.value } : x)))} />
              </td>
              <td className="py-1.5 pr-2">
                <input aria-label={`본문 칸 ${i + 1} 짝 식`} className={`field w-full font-mono ${r.expr.trim() ? '' : 'border-amber-700'}`} value={r.expr} placeholder="view.answer"
                  onChange={(e) => setBody((b) => b.map((x, j) => (j === i ? { ...x, expr: e.target.value } : x)))} />
              </td>
              <td className="py-1.5 text-right">
                <button type="button" className="btn-ghost" aria-label={`본문 칸 ${i + 1} 빼기`} onClick={() => setBody((b) => b.filter((_, j) => j !== i))}>빼기</button>
              </td>
            </tr>
          ))}
        </tbody>
      </table>
      <button type="button" className="btn-ghost" onClick={() => setBody((b) => [...b, { name: '', expr: '' }])}>본문 칸 더하기</button>

      <div className="grid gap-2 sm:grid-cols-2">
        <label className="space-y-1 text-sm">
          <span className="text-ink-700">성공 응답 코드</span>
          <input className="field w-full font-mono" value={okText} onChange={(e) => setOkText(e.target.value)} />
        </label>
        <label className="space-y-1 text-sm">
          <span className="text-ink-700">반영은 됐고 뒤처리만 실패한 오류 코드 (있으면)</span>
          <input className="field w-full font-mono" value={partialText} placeholder="REFUND_FAILED" onChange={(e) => setPartialText(e.target.value)} />
        </label>
      </div>

      <More summary="짝 식 쓰는 법">
        <p><span className="font-mono">view.answer</span> 점 경로: AI 요청 본문의 그 칸 · <span className="font-mono">const:approve</span> 그 글자 그대로 · <span className="font-mono">json:true</span> 참거짓·숫자·목록</p>
        <p><span className="font-mono">link:payments.payment_id</span> AXcellent 번호에 맞는 사이트 id(사이트에서 읽을 때 적어 둔 것) · <span className="font-mono">path:seq</span> 판매 서버 경로 값</p>
        <p><span className="font-mono">key_code:AX</span> 같은 쓰기면 같은 코드 · <span className="font-mono">now_plus:7</span> 지금부터 7일 뒤 · <span className="font-mono">first:a,b</span> 값이 있는 첫 칸</p>
      </More>

      {problems.length > 0 && <ul className="list-disc pl-5 text-xs text-amber-800">{problems.map((p) => <li key={p}>{p}</li>)}</ul>}
      <div className="flex flex-wrap gap-2">
        <button type="button" className="btn-primary" disabled={busy || problems.length > 0} onClick={() => onSave(draft)}>{busy ? '확인하는 중…' : '확인하고 저장'}</button>
        {saved && <button type="button" className="btn-ghost" disabled={busy} onClick={() => onSave(null)}>짝 지우기</button>}
      </div>
      <p className="text-xs leading-5 text-ink-500">저장할 때 서버가 짝 모양을 다시 확인해요. 저장해도 직접 쓰기는 꺼진 채로 남아요.</p>
    </div>
  )
}

// ---- 신청 받은함 짝 (취소·환불 신청) ----

const emptyClaims = (): InboxMap => ({ path: '', fields: {}, values: { status: {} } })

function ClaimsInbox({ saved, readPaths, baseUrl, authHeader, authScheme, canWrite, onState }: {
  saved: InboxMap | null; readPaths: string[]; baseUrl: string; authHeader: string; authScheme: string; canWrite: boolean; onState: (s: SiteWritesState) => void
}) {
  const [open, setOpen] = useState(false)
  const [draft, setDraft] = useState<InboxMap>(saved ?? emptyClaims())
  const [sample, setSample] = useState<SiteSample | null>(null)
  const [busy, setBusy] = useState<'look' | 'save' | null>(null)
  const [error, setError] = useState<string | null>(null)
  const [done, setDone] = useState<string | null>(null)
  const [newRaw, setNewRaw] = useState('')

  const run = async (kind: 'look' | 'save', work: () => Promise<void>) => {
    setBusy(kind); setError(null); setDone(null)
    try { await work() } catch (e) { setError(message(e)) } finally { setBusy(null) }
  }
  const look = () => void run('look', async () => {
    const found = await siteAdapter.propose({ base_url: baseUrl, auth_header: authHeader, auth_scheme: authScheme, claims_path: draft.path.trim() })
    setSample(found.claims_sample ?? null)
  })
  const save = (map: InboxMap | null) => void run('save', async () => {
    const next = await siteWrites.saveInbox(map)
    onState(next); setDraft(next.inboxes.claims ?? emptyClaims())
    setDone(map ? `저장했어요. 첫 쪽 신청 ${next.count ?? 0}건을 바꿔 읽었어요.` : '받은함 짝을 지웠어요.')
  })
  const setField = (name: string, expr: string) => setDraft((d) => ({ ...d, fields: { ...d.fields, [name]: expr } }))
  const statuses = draft.values?.status ?? {}
  const setStatus = (raw: string, to: string) => setDraft((d) => ({ ...d, values: { ...d.values, status: { ...d.values?.status, [raw]: to } } }))
  const dropStatus = (raw: string) => setDraft((d) => {
    const rest = { ...d.values?.status }
    delete rest[raw]
    return { ...d, values: { ...d.values, status: rest } }
  })
  const blanks = [...claimBlanks(draft), ...Object.entries(statuses).filter(([, to]) => !to).map(([raw]) => `상태 ${raw}`)]
  const preview = (expr: string) => {
    const row = sample?.first
    if (!row || !expr || expr.includes(':')) return '—'
    let v: unknown = row
    for (const k of expr.split('.')) {
      if (v === null || typeof v !== 'object' || !(k in v)) return '없는 칸'
      v = (v as Record<string, unknown>)[k]
    }
    const t = typeof v === 'object' ? JSON.stringify(v) : String(v)
    return t.length > 30 ? `${t.slice(0, 30)}…` : t
  }

  return (
    <div className="card space-y-3 p-4" aria-label="신청 받은함 짝">
      <div className="flex flex-wrap items-start justify-between gap-2">
        <div>
          <p className="text-sm font-medium text-ink-900">취소·환불 신청 받은함</p>
          <p className="mt-0.5 text-xs text-ink-500">고객지원 AI가 사이트의 취소·환불 신청을 읽는 짝이에요. 환불 쓰기가 신청 번호를 여기서 찾아요.</p>
        </div>
        {saved ? <StatusChip tone="ok">짝 있음</StatusChip> : <StatusChip tone="off">짝 없음</StatusChip>}
      </div>
      {saved && !open && <p className="font-mono text-xs text-ink-700">GET {saved.path}</p>}
      <button type="button" className="btn-ghost" disabled={!canWrite} onClick={() => setOpen((v) => !v)}>{open ? '짝 닫기' : saved ? '짝 고치기' : '짝 정하기'}</button>

      {open && (
        <div className="space-y-3 border-t border-ink-100 pt-3">
          <div className="flex flex-wrap items-end gap-2">
            <label className="flex-1 space-y-1 text-sm">
              <span className="text-ink-700">신청 목록 경로</span>
              <input className="field w-full font-mono" list="site-claim-paths" value={draft.path} placeholder="/api/v1/cancellations"
                onChange={(e) => setDraft((d) => ({ ...d, path: e.target.value }))} />
            </label>
            <datalist id="site-claim-paths">{readPaths.map((p) => <option key={p} value={p} />)}</datalist>
            <button type="button" className="btn-ghost" disabled={busy !== null || !draft.path.trim()} onClick={look}>{busy === 'look' ? '읽는 중…' : '첫 쪽 읽어 보기'}</button>
          </div>
          <datalist id="site-claim-keys">{sample?.keys.map((k) => <option key={k} value={k} />)}</datalist>
          {sample && <p role="status" className="text-xs text-ink-500">첫 쪽 {sample.count}줄 · 칸 {sample.keys.join(', ') || '없음'}</p>}

          <table className="w-full text-sm" aria-label="신청 칸 짝">
            <thead><tr className="text-left text-xs text-ink-500"><th className="py-1 font-normal">AXcellent 칸</th><th className="py-1 font-normal">사이트 칸</th><th className="py-1 font-normal">첫 줄 값</th></tr></thead>
            <tbody>
              {CLAIM_FIELDS.map((f) => {
                const expr = draft.fields[f.name] ?? ''
                return (
                  <tr key={f.name} className="border-t border-ink-100">
                    <td className="py-1.5 pr-2">{f.label}{f.required ? '' : <span className="ml-1 text-xs text-ink-500">(있으면)</span>}</td>
                    <td className="py-1.5 pr-2">
                      <input aria-label={`신청 ${f.label} 사이트 칸`} list="site-claim-keys" className={`field w-full font-mono ${expr.trim() || !f.required ? '' : 'border-amber-700'}`}
                        value={expr} placeholder={f.required ? '정해 주세요' : '비워 두면 짝 없음'} onChange={(e) => setField(f.name, e.target.value)} />
                    </td>
                    <td className="py-1.5 font-mono text-xs text-ink-500">{preview(expr)}</td>
                  </tr>
                )
              })}
            </tbody>
          </table>
          <p className="text-xs leading-5 text-ink-500">짝 식 안의 <span className="font-mono">order.&lt;칸&gt;</span>은 그 신청의 주문(예약) 원본이에요(구매자·이름을 주문에서 꺼낼 때).</p>

          <table className="w-full text-sm" aria-label="신청 상태 짝">
            <caption className="pb-1 text-left text-xs text-ink-500">신청 상태 · 사이트 값을 결정 전·끝남으로 바꿔요</caption>
            <tbody>
              {Object.entries(statuses).map(([raw, to]) => (
                <tr key={raw} className="border-t border-ink-100">
                  <td className="py-1.5 pr-2 font-mono">{raw}</td>
                  <td className="py-1.5 pr-2">
                    <select aria-label={`신청 상태 ${raw}`} className={`field ${to ? '' : 'border-amber-700'}`} value={to} onChange={(e) => setStatus(raw, e.target.value)}>
                      {CLAIM_STATUS.map((s) => <option key={s.value} value={s.value}>{s.label}</option>)}
                    </select>
                  </td>
                  <td className="py-1.5 text-right"><button type="button" className="btn-ghost" onClick={() => dropStatus(raw)}>빼기</button></td>
                </tr>
              ))}
            </tbody>
          </table>
          <div className="flex flex-wrap gap-2">
            <input aria-label="더할 사이트 상태 값" className="field font-mono" value={newRaw} placeholder="requested" onChange={(e) => setNewRaw(e.target.value)} />
            <button type="button" className="btn-ghost" disabled={!newRaw.trim() || newRaw.trim() in statuses}
              onClick={() => { setStatus(newRaw.trim(), ''); setNewRaw('') }}>상태 값 더하기</button>
          </div>

          {blanks.length > 0 && <p className="text-xs text-amber-800">아직 정하지 않은 칸: {blanks.join(', ')}</p>}
          <div className="flex flex-wrap gap-2">
            <button type="button" className="btn-primary" disabled={busy !== null || blanks.length > 0 || !draft.path.trim()} onClick={() => save(cleanInbox(draft))}>
              {busy === 'save' ? '확인하고 저장하는 중…' : '확인하고 저장'}
            </button>
            {saved && <button type="button" className="btn-ghost" disabled={busy !== null} onClick={() => save(null)}>짝 지우기</button>}
          </div>
          <p className="text-xs leading-5 text-ink-500">저장할 때 사이트에서 첫 쪽을 다시 읽어 바꿔 보고, 통과해야 저장해요.</p>
        </div>
      )}
      {done && <p role="status" className="text-sm text-emerald-800">{done}</p>}
      {error && <p role="alert" className="text-sm text-red-700">{error}</p>}
    </div>
  )
}

// ---- 최근 사이트 쓰기 ----

function RecentWrites({ rows, onReload }: { rows: RecentWrite[] | null; onReload: () => void }) {
  return (
    <div className="space-y-2">
      <div className="flex items-center justify-between gap-2">
        <h4 className="text-sm font-semibold text-ink-900">최근 사이트 쓰기</h4>
        <button type="button" className="btn-ghost" onClick={onReload}>새로 읽기</button>
      </div>
      {rows === null && <p className="text-sm text-ink-500">쓰기 기록을 읽지 못했어요.</p>}
      {rows?.length === 0 && <p className="text-sm text-ink-500">아직 사이트 쓰기가 없어요.</p>}
      {rows && rows.length > 0 && (
        <table className="w-full text-sm" aria-label="최근 사이트 쓰기">
          <caption className="pb-1 text-left text-xs text-ink-500">최근 {rows.length}건 · 본문과 키는 남기지 않아요</caption>
          <thead>
            <tr className="text-left text-xs text-ink-500">
              <th className="py-1 font-normal">시각</th><th className="py-1 font-normal">동작</th><th className="py-1 font-normal">결과</th>
              <th className="py-1 font-normal">사이트 응답</th><th className="py-1 font-normal">사이트 경로</th>
            </tr>
          </thead>
          <tbody>
            {rows.map((r, i) => {
              const chip = writeChip(r)
              const action = r.action && r.action in ACTION_LABEL ? ACTION_LABEL[r.action as keyof typeof ACTION_LABEL] : r.action ?? '—'
              return (
                <tr key={`${r.at}-${i}`} className="border-t border-ink-100 align-top">
                  <td className="py-1.5 pr-2 text-xs text-ink-500">{r.at ? ago(r.at) : '—'}</td>
                  <td className="py-1.5 pr-2">{action}</td>
                  <td className="py-1.5 pr-2">
                    <StatusChip tone={chip.tone}>{chip.label}</StatusChip>
                    {r.reason_text && r.status !== 'done' && <span className="mt-1 block text-xs text-ink-500">{r.reason_text}</span>}
                  </td>
                  <td className="py-1.5 pr-2 font-mono text-xs">{r.site_status ?? '—'}{r.site_code ? ` ${r.site_code}` : ''}</td>
                  <td className="py-1.5 font-mono text-xs text-ink-700">{r.site_path ? `${r.site_method ?? ''} ${r.site_path}` : '—'}</td>
                </tr>
              )
            })}
          </tbody>
        </table>
      )}
    </div>
  )
}
