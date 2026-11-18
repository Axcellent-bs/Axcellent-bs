import type { ConnectionState } from '@/lib/companyConnection'
import { SectionHead, StatusChip } from '@/components/connect/parts'
import { Link } from '@/lib/nav'
import Integrations from './Integrations'

// 사이트 연결: 지금 실제로 되는 연결 방법(AXcellent 규격 API, Integrations → shop-api)만 보여 준다.
// 고를 수만 있고 동작하지 않는 칸("준비 중")은 두지 않는다(2026-11-18). 읽기 전용 DB 연결은 하지 않기로 했고,
// 다른 형식 API·플랫폼으로 만든 사이트는 사이트 변환기(docs/superpowers/specs/2026-11-18-site-adapter-design.md)가
// 생기면 동작하는 상태로 여기에 더한다.
// 배포 도구 연결은 시스템 관리 › 도구 연결에서 한다.
export default function SiteConnect({ onShop }: { onShop: (s: ConnectionState | 'error') => void }) {
  return (
    <section className="space-y-4" aria-label="사이트 연결">
      <SectionHead title="사이트 연결" desc="회사 사이트의 데이터를 AI들이 읽고 쓸 수 있게 이어요. 사이트 코드나 서버는 건드리지 않아요." />

      <div className="card">
        <div className="p-6">
          <SectionHead as="h3" title="사이트 데이터 연결 (읽기·쓰기)" desc="AXcellent 규격 API(shop-api)를 쓰는 사이트의 주소를 확인하고 저장해요."
            right={<StatusChip tone="wait">필수</StatusChip>} />
        </div>
        <div className="border-t border-ink-100 p-4"><Integrations onState={onShop} embedded /></div>
      </div>

      <div className="card px-5 py-3 text-sm text-ink-700">
        배포 도구(GitHub Actions·Jenkins)는 시스템 관리 › 도구 연결 › 배포에서 연결해요. <Link href="/system/tools" className="font-medium text-brand-700 underline">도구 연결 열기</Link>
      </div>
    </section>
  )
}
