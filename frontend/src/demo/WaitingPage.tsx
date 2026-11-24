import { Navigate } from 'react-router'
import { homeOf, useSession, waiting } from './session'

// 연결 대기: 회사에 연결되지 않은 일반회원(관리자가 아직 연결하지 않았거나 연결을 끊은 계정).
// 서버도 이 세션을 회사 없음으로 받아 업무 경로는 403(NO_COMPANY)이라, 업무 화면 대신 이 안내만 보여 준다.
const WORDMARK = `${import.meta.env.BASE_URL}axcellent-wordmark.webp`

export default function WaitingPage() {
  const { account, signOut } = useSession()
  if (!account) return <Navigate to="/login" replace />
  // 연결되면(또는 관리자·운영자면) 원래 첫 화면으로
  if (!waiting(account)) return <Navigate to={homeOf(account)} replace />
  return (
    <div className="flex min-h-screen items-center justify-center bg-[#1d1d3a] px-6 py-12">
      <div className="w-full max-w-md space-y-6 text-center">
        <img src={WORDMARK} alt="AXcellent" className="mx-auto h-9 w-auto" />
        <div className="card space-y-3 p-6 text-left">
          <h1 className="text-lg font-semibold">회사 연결 대기</h1>
          <p className="text-sm text-ink-700">
            {account.name}({account.email}) 계정은 아직 어느 회사에도 연결되지 않았어요. 회사 관리자가 회원·서버 관리에서
            이 계정을 연결하면 쓸 수 있어요.
          </p>
          <button type="button" className="btn-ghost px-4" onClick={signOut}>로그아웃</button>
        </div>
      </div>
    </div>
  )
}
