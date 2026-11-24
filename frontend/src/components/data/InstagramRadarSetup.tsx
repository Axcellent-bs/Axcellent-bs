import { useEffect, useState } from 'react'
import { More, Notice, SectionHead, StatusChip } from '@/components/connect/parts'
import { ApiError } from '@/lib/apiError'
import { HUMAN_TOKEN_SET } from '@/lib/env'
import { instagramRadar, splitAccounts, type RadarState } from '@/lib/instagramRadar'
import { RADAR_SOURCE as SOURCE } from '@/lib/radarSource'

// 인스타 레이더 계정: 데이터 AI가 반응을 지켜볼 인스타 계정(업계·경쟁·참고 계정). 데이터분석 › 트렌드 › 인스타의 레이더 아래에서 정한다.
// 저장하면 회사 설정 파일(trends.toml)의 목록 대신 이 목록을 쓴다. "회사 설정 파일로 되돌리기"로 저장 목록을 지운다.
// 둘 다 없으면 경쟁사 찾기로 확인한 계정(자동)을 칸에 보여 주고 레이더도 그 목록을 쓴다.
const message = (e: unknown) => (e instanceof ApiError || e instanceof Error ? e.message : '요청에 실패했습니다')

export default function InstagramRadarSetup() {
  const [state, setState] = useState<RadarState | null>(null)
  const [text, setText] = useState('')
  const [busy, setBusy] = useState(false)
  const [error, setError] = useState<string | null>(null)
  const [done, setDone] = useState<string | null>(null)

  const [memeText, setMemeText] = useState('')
  const take = (s: RadarState) => { setState(s); setText(s.accounts.join('\n')); setMemeText((s.meme_accounts ?? []).join('\n')) }
  useEffect(() => {
    let live = true
    instagramRadar.get().then((s) => { if (live) take(s) }).catch((e) => { if (live) setError(message(e)) })
    return () => { live = false }
  }, [])

  const run = async (work: () => Promise<RadarState>, ok: string) => {
    setBusy(true); setError(null); setDone(null)
    try { take(await work()); setDone(ok) } catch (e) { setError(message(e)) } finally { setBusy(false) }
  }
  const typed = splitAccounts(text)
  const typedMeme = splitAccounts(memeText)
  const canWrite = HUMAN_TOKEN_SET && (state?.editable ?? false)
  const max = state?.max ?? 200
  const savedMeme = state?.meme_accounts?.length ?? 0
  const canClear = state !== null && ((state.source === 'screen' && state.tenant_count > 0) || (state.meme_source === 'screen' && (state.meme_tenant_count ?? 0) > 0))
  const status = !state ? null : state.accounts.length + savedMeme
    ? <StatusChip tone="ok">{state.accounts.length + savedMeme}곳 · {SOURCE[state.source]}</StatusChip>
    : <StatusChip tone="wait">계정 없음</StatusChip>

  return (
    <section className="card space-y-4 p-6" aria-label="인스타 레이더 계정">
      <SectionHead as="h3" title="인스타 레이더 계정" right={status}
        desc="위 레이더가 반응을 지켜볼 인스타 계정이에요(업계·경쟁·참고 계정). 한 줄에 하나씩 넣어요." />
      {!HUMAN_TOKEN_SET && <Notice>로그인이 필요해요. 로그인하면 저장할 수 있어요.</Notice>}
      {state && !state.editable && <Notice>서버에 회사 연결 파일 설정이 없어 저장할 수 없어요.</Notice>}
      <label className="block space-y-1 text-sm">
        <span className="text-ink-700">벤치마크 계정 (@는 빼도 돼요)</span>
        <textarea className="field h-48 w-full font-mono text-xs" value={text} onChange={(e) => { setText(e.target.value); setDone(null) }}
          placeholder={'catbooks_official\nbook_reviewer'} />
      </label>
      <label className="block space-y-1 text-sm">
        <span className="text-ink-700">밈·크리에이터 계정 (한 줄에 하나)</span>
        <textarea className="field h-32 w-full font-mono text-xs" value={memeText} onChange={(e) => { setMemeText(e.target.value); setDone(null) }}
          placeholder={'meme_creator_a\nmeme_creator_b'} />
      </label>
      <div className="flex flex-wrap items-center gap-2">
        <button type="button" className="btn-primary" disabled={busy || !canWrite}
          onClick={() => void run(() => instagramRadar.save(typed, typedMeme), `벤치마크 ${typed.length}곳 + 밈 ${typedMeme.length}곳을 저장했어요. 데이터 AI 레이더가 다음 회차부터 이 목록을 써요.`)}>
          {busy ? '저장 중…' : '저장'}
        </button>
        {canClear && state && (
          <button type="button" className="btn-ghost" disabled={busy || !canWrite}
            onClick={() => void run(() => instagramRadar.clear(), '회사 설정 파일 목록으로 되돌렸어요.')}>
            회사 설정 파일로 되돌리기 ({state.tenant_count}곳{(state.meme_tenant_count ?? 0) > 0 ? ` + 밈 ${state.meme_tenant_count}곳` : ''})
          </button>
        )}
        <span className="text-xs text-ink-500">벤치마크 {typed.length}곳 + 밈 {typedMeme.length}곳 / {max}</span>
      </div>
      <More summary="어떻게 쓰이나요">
        <p>밈·크리에이터 계정은 갑자기 뜬 게시물에서 따로 보고, 팔로워 대비 반응도 같이 봐요.</p>
        <p>저장하면 회사 설정 파일(trends.toml)에 적힌 목록 대신 이 목록을 써요. 한 번에 많이 부르지 않게 묶음으로 나눠 읽어요.</p>
        <p>둘 다 없으면 분석 보고서의 경쟁사 찾기가 공식 API로 확인한 계정을 자동으로 써요.</p>
        <p>인스타 API 키(데이터용 계정)는 서버 설정에 있어야 실제로 읽혀요.</p>
      </More>
      {done && <p role="status" className="text-sm text-emerald-800">{done}</p>}
      {error && <p role="alert" className="text-sm text-red-700">{error}</p>}
    </section>
  )
}
