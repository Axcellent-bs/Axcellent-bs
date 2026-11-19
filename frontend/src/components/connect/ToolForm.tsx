import { useCallback, useEffect, useReducer, useState, type FormEvent, type ReactNode } from 'react'
import { StatusChip } from './parts'
import { ToolCheckError, checkTool, deleteTool, listTools, saveTool, testTool, type ToolCheck, type ToolConnection, type ToolKind, type ToolList } from '@/lib/toolConnections'
import { appChoices, bodyOf, formActions, formReducer, initForm, missingFields, statusOf, when } from '@/lib/toolForms'

// 도구 연결(GitHub 앱·배포 도구·DejaFix)이 같이 쓰는 부품.
// - ToolForm: 새 연결(또는 고치기) 하나. 칸은 서버 칸 정의(fields[kind])대로 → 연결 확인 → 저장
// - ConnectedTool: 저장된 연결 한 줄. 상태·마지막 확인 + 다시 확인·고치기·끊기(확인 단계). 관리자만 버튼
// 비밀 값은 보내기만 한다. 저장 뒤에는 서버가 준 가린 값(•••• · 개인 키는 설정됨)만 보여 준다.

/** 도구 연결 목록 받기. 화면 여러 곳(배포·GitHub 앱·DejaFix 카드)이 같은 목록을 다시 받는다 */
export function useToolList() {
  const [data, setData] = useState<ToolList | null>(null)
  const [error, setError] = useState<string | null>(null)
  const reload = useCallback(() => {
    listTools().then((d) => { setData(d); setError(null) }).catch((e: Error) => setError(e.message || '도구 연결을 불러오지 못했어요'))
  }, [])
  useEffect(reload, [reload])
  return { data, error, reload }
}

/** 연결 확인 결과 목록: 항목마다 통과·이유 */
export function CheckList({ checks, passed }: { checks: ToolCheck[]; passed: boolean }) {
  return (
    <div className={`border-l-2 px-4 py-2.5 text-sm ${passed ? 'border-emerald-700 bg-emerald-50/60' : 'border-red-700 bg-red-50/60'}`} role="status">
      <p className={`font-semibold ${passed ? 'text-emerald-700' : 'text-red-700'}`}>{passed ? '확인됐어요' : '확인되지 않았어요. 아래 내용을 고쳐 주세요'}</p>
      <ul className="mt-1 space-y-0.5" aria-label="연결 확인 결과">
        {checks.map((c) => (
          <li key={c.key} className="flex gap-2">
            <span aria-hidden="true" className={c.ok ? 'text-emerald-700' : 'text-red-700'}>{c.ok ? '✓' : '✕'}</span>
            <span><span className="text-ink-700">{c.label}</span>{c.detail && <span className="text-ink-500"> · {c.detail}</span>}</span>
          </li>
        ))}
      </ul>
    </div>
  )
}

const MONO = new Set(['url', 'base_url', 'repo', 'workflow', 'job', 'app_id', 'installation_id', 'username', 'version_input', 'version_param'])

/** 새 연결 하나 (existing이 있으면 그 연결 고치기). 관리자가 아니면 그리지 않는다 */
export function ToolForm({ kind, data, admin, existing, choices, onSaved, onCancel, title }: {
  kind: ToolKind
  data: ToolList
  admin: boolean
  existing?: ToolConnection
  /** 도구 고르기 select (배포: GitHub Actions · Jenkins) */
  choices?: readonly { id: ToolKind; label: string }[]
  onSaved: (c: ToolConnection) => void
  onCancel?: () => void
  title?: string
}) {
  const fieldsOf = (k: ToolKind) => data.fields[k] ?? []
  const [s, dispatch] = useReducer(formReducer, undefined, () => initForm(kind, fieldsOf(kind), existing))
  const [keyName, setKeyName] = useState('')
  const fields = fieldsOf(s.kind)
  const actions = formActions(admin, s)
  if (!admin) return null

  const run = async (what: 'test' | 'save') => {
    const missing = missingFields(fields, s, existing)
    if (missing.length) return dispatch({ type: 'failed', message: `입력해 주세요: ${missing.join(', ')}` })
    dispatch({ type: 'busy', busy: what })
    try {
      if (what === 'test') {
        const r = await testTool(bodyOf(s))
        dispatch({ type: 'tested', ok: r.ok, checks: r.checks })
      } else {
        const r = await saveTool(bodyOf(s), s.id)
        dispatch({ type: 'saved' })
        setKeyName('')
        onSaved(r.connection)
      }
    } catch (e) {
      dispatch({ type: 'failed', message: (e as Error).message, checks: e instanceof ToolCheckError ? e.checks : undefined })
    }
  }
  const submit = (e: FormEvent) => { e.preventDefault(); void run(s.passed ? 'save' : 'test') }

  // 개인 키는 파일에서 바로 읽어 보낸다. 화면에는 파일 이름만 남긴다
  const pickKey = (key: string, file?: File) => {
    if (!file) return
    if (file.size > 16_000) return dispatch({ type: 'failed', message: '개인 키 파일이 너무 커요. GitHub 앱에서 받은 .pem 파일을 골라 주세요.' })
    void file.text().then((text) => {
      if (!text.includes('PRIVATE KEY')) return dispatch({ type: 'failed', message: 'GitHub 앱 개인 키(.pem) 파일이 아니에요.' })
      setKeyName(file.name)
      dispatch({ type: 'secret', key, value: text })
    }).catch(() => dispatch({ type: 'failed', message: '키 파일을 읽지 못했어요. 파일을 다시 골라 주세요.' }))
  }

  const apps = appChoices(data.connections)
  const input = (f: (typeof fields)[number]) => {
    const id = `tool-${s.kind}-${f.key}`
    const saved = existing?.secrets_set[f.key]
    if (f.secret && f.key === 'private_key') {
      return (
        <label htmlFor={id} className="field flex cursor-pointer items-center gap-2 text-sm">
          <input id={id} type="file" accept=".pem,application/x-pem-file" className="sr-only" onChange={(e) => pickKey(f.key, e.target.files?.[0])} />
          <span className="chip bg-ink-100 text-ink-700">.pem 파일 고르기</span>
          <span className="truncate text-ink-500">{keyName || (saved ? `${saved} · 바꾸려면 다시 고르기` : '고른 파일 없음')}</span>
        </label>
      )
    }
    if (f.secret) {
      return <input id={id} className="field mono w-full" type="password" autoComplete="new-password" value={s.secrets[f.key] ?? ''}
        placeholder={saved ? `${saved} · 비우면 그대로` : ''} onChange={(e) => dispatch({ type: 'secret', key: f.key, value: e.target.value })} />
    }
    if (s.kind === 'github_actions' && f.key === 'app') {
      return (
        <select id={id} className="field w-full" value={s.config.app ?? ''} disabled={!apps.length} onChange={(e) => dispatch({ type: 'config', key: 'app', value: e.target.value })}>
          <option value="">{apps.length ? '고르기' : '연결된 GitHub 앱 없음'}</option>
          {apps.map((a) => <option key={a.id} value={a.id}>{a.label}</option>)}
        </select>
      )
    }
    return <input id={id} className={`field w-full ${MONO.has(f.key) ? 'mono' : ''}`} value={s.config[f.key] ?? ''}
      onChange={(e) => dispatch({ type: 'config', key: f.key, value: e.target.value })} />
  }

  return (
    <form onSubmit={submit} className="space-y-4" aria-label={title ?? '새 연결'}>
      {title && <h3 className="text-[15px] font-semibold">{title}</h3>}
      {choices && !existing && (
        <label className="block max-w-xs space-y-1 text-sm">
          <span className="text-ink-700">도구</span>
          <select className="field w-full" value={s.kind} onChange={(e) => { setKeyName(''); dispatch({ type: 'kind', kind: e.target.value as ToolKind, fields: fieldsOf(e.target.value as ToolKind) }) }}>
            {choices.map((c) => <option key={c.id} value={c.id}>{c.label}</option>)}
          </select>
        </label>
      )}
      {!fields.length ? (
        <p className="text-sm text-ink-500">이 도구의 입력 칸을 서버에서 받지 못했어요.</p>
      ) : (
        <div className="grid gap-3 md:grid-cols-2">
          {fields.map((f) => (
            <div key={f.key} className="space-y-1 text-sm">
              <label htmlFor={`tool-${s.kind}-${f.key}`} className="text-ink-700">{f.label}{!f.required && <span className="text-ink-500"> (선택)</span>}</label>
              {input(f)}
              {s.kind === 'github_actions' && f.key === 'app' && !apps.length
                ? <p className="text-xs text-amber-800">먼저 형상관리 › 레포 연결 맨 위의 GitHub 앱을 연결해 주세요.</p>
                : f.help && <p className="text-xs text-ink-500">{f.help}</p>}
            </div>
          ))}
        </div>
      )}
      {s.checks && <CheckList checks={s.checks} passed={!!s.passed} />}
      {s.error && !(s.checks && s.passed === false) && <p className="text-sm text-red-700" role="alert">{s.error}</p>}
      <div className="flex flex-wrap items-center gap-2">
        {actions.map((a) => a.id === 'test' ? (
          <button key={a.id} type="button" className="btn-ghost" disabled={a.disabled || !fields.length} onClick={() => void run('test')}>{s.busy === 'test' ? '확인 중…' : '연결 확인'}</button>
        ) : (
          <button key={a.id} type="button" className="btn-primary px-5" disabled={a.disabled} title={s.passed ? undefined : '연결 확인이 통과하면 저장할 수 있어요'} onClick={() => void run('save')}>{s.busy === 'save' ? '저장 중…' : '저장'}</button>
        ))}
        {onCancel && <button type="button" className="btn-quiet" onClick={onCancel}>취소</button>}
        {!s.passed && !s.checks && <span className="text-xs text-ink-500">연결 확인이 통과하면 저장할 수 있어요.</span>}
      </div>
    </form>
  )
}

/** 저장된 연결 한 줄: 상태 chip·마지막 확인·사실들 + 다시 확인·고치기·끊기(관리자만, 끊기는 한 번 더 묻는다) */
export function ConnectedTool({ conn, admin, title, facts, offNote, offLabel = '끊기', onChanged, onEdit, children }: {
  conn: ToolConnection
  admin: boolean
  title: ReactNode
  facts?: ReactNode
  /** 끊기 전에 보여 줄 한 줄 (끊으면 무엇이 멈추나) */
  offNote: string
  /** 끊기 단추 글자 (DejaFix: "같이 설치된 DejaFix로 돌아가기") */
  offLabel?: string
  onChanged: () => void
  onEdit?: () => void
  children?: ReactNode
}) {
  const [busy, setBusy] = useState(false)
  const [confirmOff, setConfirmOff] = useState(false)
  const [error, setError] = useState<string | null>(null)
  const [checks, setChecks] = useState<{ ok: boolean; checks: ToolCheck[] } | null>(null)
  const st = statusOf(conn)
  const act = async (f: () => Promise<void>) => {
    setBusy(true)
    setError(null)
    try { await f() } catch (e) { setError((e as Error).message) } finally { setBusy(false) }
  }
  const recheck = () => act(async () => { const r = await checkTool(conn.id); setChecks({ ok: r.ok, checks: r.checks }); onChanged() })
  const remove = () => act(async () => { await deleteTool(conn.id); setConfirmOff(false); onChanged() })

  return (
    <li className="space-y-3 border border-ink-100 p-4">
      <div className="flex flex-wrap items-center gap-2">
        <span className="font-semibold">{title}</span>
        <StatusChip tone={st.tone}>{st.label}</StatusChip>
        <span className="text-xs text-ink-500">마지막 확인 {when(conn.checked_at)}</span>
        {admin && !confirmOff && (
          <div className="ml-auto flex flex-wrap gap-1.5">
            <button type="button" className="btn-ghost" disabled={busy} onClick={() => void recheck()}>{busy ? '확인 중…' : '다시 확인'}</button>
            {onEdit && <button type="button" className="btn-ghost" disabled={busy} onClick={onEdit}>고치기</button>}
            <button type="button" className="btn-quiet text-red-700" disabled={busy} onClick={() => setConfirmOff(true)}>{offLabel}</button>
          </div>
        )}
      </div>
      {facts}
      {conn.check_ok === false && conn.check_detail && !checks && <p className="text-sm text-red-700">{conn.check_detail}</p>}
      {checks && <CheckList checks={checks.checks} passed={checks.ok} />}
      {children}
      {admin && confirmOff && (
        <div className="flex flex-wrap items-center justify-between gap-3 border-t border-ink-100 pt-3">
          <p className="text-xs text-ink-500">{offNote}</p>
          <div className="flex gap-2">
            <button type="button" className="btn-quiet" disabled={busy} onClick={() => setConfirmOff(false)}>취소</button>
            <button type="button" className="btn-primary bg-red-700 hover:bg-red-800" disabled={busy} onClick={() => void remove()}>{busy ? (offLabel === '끊기' ? '끊는 중…' : '바꾸는 중…') : offLabel}</button>
          </div>
        </div>
      )}
      {error && <p className="text-sm text-red-700" role="alert">{error}</p>}
    </li>
  )
}
