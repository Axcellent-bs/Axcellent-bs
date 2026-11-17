import { useState, type ReactNode } from 'react'
import { SectionHead, StatusChip } from '@/components/connect/parts'
import { PreparingChip } from '@/components/Preparing'
import { Link } from '@/lib/nav'
import type { ConnectionState } from '@/lib/companyConnection'
import Integrations from './Integrations'

// 사이트 연결: 고객사 사이트가 어떻게 만들어졌는지 고르면(① 직접 만든 사이트 ② 사이트 제작 플랫폼) 그 경우에 맞는 연결 창구를 보여 준다.
// 고객사는 고르고·누르고·붙여 넣기만 한다. 사이트 코드나 서버는 건드리지 않는다.
// 'AXcellent 규격 API'만 실제 연결(Integrations, shop-api)이다. 나머지 방식은 백엔드가 없어 "준비 중" 표시만 두고 고를 수 없게 한다(지어낸 연결 상태 없음).
// 배포 도구 연결은 시스템 관리 › 도구 연결에서 한다.

const Radio = ({ on, onPick, title, desc, disabled = false }: { on: boolean; onPick?: () => void; title: string; desc: ReactNode; disabled?: boolean }) => (
  <button type="button" role="radio" aria-checked={on} aria-disabled={disabled || undefined} disabled={disabled} onClick={disabled ? undefined : onPick}
    className={`flex flex-col gap-1 border p-4 text-left ${disabled ? 'cursor-not-allowed border-ink-100 opacity-60' : on ? 'border-brand-600 bg-brand-50/50' : 'border-ink-100 hover:border-ink-300'}`}>
    <span className="font-semibold">{title}</span>
    <span className="text-xs leading-5 text-ink-500">{desc}</span>
  </button>
)

export default function SiteConnect({ onShop }: { onShop: (s: ConnectionState | 'error') => void }) {
  const [kind, setKind] = useState<'platform' | 'custom'>('custom')
  return (
    <div className="space-y-6">
      <section className="space-y-4">
        <SectionHead title="사이트 연결" desc="사이트가 어떻게 만들어졌는지 골라 주세요. 사이트 코드나 서버는 건드리지 않고, 여기서 고르고·누르고·붙여 넣기만 해요." />
        <div role="radiogroup" aria-label="사이트 종류" className="grid gap-3 md:grid-cols-2">
          <Radio on={kind === 'custom'} onPick={() => setKind('custom')} title="직접 만든 사이트예요"
            desc="자체 개발한 서비스·예약 사이트 등. 이미 쓰는 API·DB로 사이트 데이터를 연결해요." />
          <Radio on={kind === 'platform'} onPick={() => setKind('platform')} title="사이트 제작 플랫폼으로 만들었어요"
            desc={<>플랫폼 위에 만든 사이트예요. 플랫폼 계정으로 허락하면 한 번에 연결돼요. <PreparingChip /></>} />
        </div>
      </section>
      {kind === 'platform' && <PlatformPanel />}
      <div className={kind === 'custom' ? '' : 'hidden'}><CustomPanel onShop={onShop} /></div>
    </div>
  )
}

// 플랫폼(카페24 등) 연결은 보류: 플랫폼 앱 등록에 사업자 등록이 필요하다 (2026-11-16 결정). 지어낸 연결 상태는 보여 주지 않는다.
function PlatformPanel() {
  return (
    <section className="space-y-4" aria-label="플랫폼 연결">
      <SectionHead as="h3" title="플랫폼 연결" desc="플랫폼 계정으로 허락하면 한 번에 연결되는 방식이에요." right={<PreparingChip />} />
      <div className="card space-y-2 p-6">
        <p className="text-sm text-ink-700">플랫폼 연결은 아직 준비 중이에요. 열리면 이 자리에서 플랫폼을 고르고 허락하기만 하면 돼요.</p>
        <p className="text-sm text-ink-500">그전에는 사이트가 쓰는 API로 연결할 수 있어요. 위에서 "직접 만든 사이트예요"를 골라 주세요.</p>
      </div>
    </section>
  )
}

function CustomPanel({ onShop }: { onShop: (s: ConnectionState | 'error') => void }) {
  return (
    <section className="space-y-4" aria-label="직접 만든 사이트 연결">
      <SectionHead as="h3" title="직접 만든 사이트 연결" desc="사이트가 쓰는 연결 방법을 하나 골라요." />

      <div className="card">
        <div className="space-y-4 p-6">
          <SectionHead as="h3" title="사이트 데이터 연결 (읽기·쓰기)" desc="지금은 AXcellent 규격 API만 쓸 수 있어요."
            right={<StatusChip tone="wait">필수</StatusChip>} />
          <div role="radiogroup" aria-label="사이트 데이터 연결 방법" className="grid gap-3 md:grid-cols-3">
            <Radio on title="AXcellent 규격 API" desc="AXcellent 규격(shop-api)을 그대로 쓰는 사이트예요. 주소만 확인·저장해요." />
            <Radio on={false} disabled title="다른 형식의 API" desc={<>이미 있는 API 주소와 지금 쓰는 인증으로 연결하는 방식이에요. <PreparingChip /></>} />
            <Radio on={false} disabled title="읽기 전용 DB" desc={<>읽기만 되는 DB 계정으로 연결하는 방식이에요. <PreparingChip /></>} />
          </div>
        </div>
        <div className="border-t border-ink-100 p-4"><Integrations onState={onShop} embedded /></div>
      </div>

      <div className="card px-5 py-3 text-sm text-ink-700">
        배포 도구(GitHub Actions·Jenkins)는 시스템 관리 › 도구 연결 › 배포에서 연결해요. <Link href="/system/tools" className="font-medium text-brand-700 underline">도구 연결 열기</Link>
      </div>
    </section>
  )
}
