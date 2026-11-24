import { SERVICES, type Service } from '@/demo/services'
import { PreparingChip, PreparingNotice } from '@/components/Preparing'

// 서비스 관리 (AXcellent 운영팀). 우리가 만든 서비스 목록(카탈로그 `SERVICES`)을 본다.
// 서비스는 모두 필수라 카탈로그에서 내리는 기능은 없다(2026-11-18).
// 서비스별 버전·배포·오류 수는 고객사 설치가 보내는 상태 신호가 아직 없어 "준비 중"이다(데이터를 지어내지 않는다).

function Mark({ s }: { s: Service }) {
  return (
    <span className={`flex h-9 w-9 shrink-0 items-center justify-center ${s.by === 'DejaFix' ? 'bg-[#1d1d3a]' : 'bg-brand-50 text-brand-600'}`}>
      {s.logo ? <img src={s.logo} alt="" width={20} height={20} /> : <s.Icon size={18} />}
    </span>
  )
}

export default function ServiceOps() {
  return (
    <div className="space-y-6 px-8 pb-12">
      <header>
        <p className="text-sm text-ink-500">관제 센터</p>
        <h1 className="text-2xl font-semibold">서비스 관리</h1>
        <p className="mt-1 text-sm text-ink-500">우리 서비스 목록이에요. 고객사별 버전·배포 현황은 상태 신호가 연결되면 여기에 나와요.</p>
      </header>

      <div className="grid gap-3 sm:grid-cols-2 lg:grid-cols-4">
        <div className="card px-5 py-4"><p className="text-xs text-ink-500">서비스</p><p className="mt-1 text-3xl font-bold tabular-nums">{SERVICES.length}</p></div>
        <div className="card px-5 py-4"><p className="text-xs text-ink-500">배포할 새 버전</p><p className="mt-2"><PreparingChip /></p></div>
        <div className="card px-5 py-4"><p className="text-xs text-ink-500">오늘 오류</p><p className="mt-2"><PreparingChip /></p></div>
        <div className="card px-5 py-4"><p className="text-xs text-ink-500">쓰는 고객사</p><p className="mt-2"><PreparingChip /></p></div>
      </div>

      <PreparingNotice what="서비스별 버전·배포" source="고객사 설치의 상태 신호" />

      <section className="card overflow-x-auto">
        <table className="w-full min-w-[640px] text-sm">
          <thead className="bg-app text-left text-xs text-ink-500">
            <tr>
              <th className="px-5 py-3 font-medium">서비스</th>
              <th className="px-3 py-3 font-medium">만든 곳</th>
              <th className="px-3 py-3 font-medium">버전·배포</th>
            </tr>
          </thead>
          <tbody className="divide-y divide-ink-100">
            {SERVICES.map((s) => (
              <tr key={s.id}>
                <td className="px-5 py-3">
                  <div className="flex items-center gap-3">
                    <Mark s={s} />
                    <p className="font-medium">{s.name}</p>
                  </div>
                </td>
                <td className="px-3 py-3 text-ink-700">{s.by}</td>
                <td className="px-3 py-3"><PreparingChip /></td>
              </tr>
            ))}
          </tbody>
        </table>
      </section>
    </div>
  )
}
