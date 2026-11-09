import { useState, type ReactNode } from 'react'
import { cx } from '../lib/cx'
import { Icon } from './Icon'
import { defaultLink, type LinkRenderer, type NavItem } from './nav'

export interface Brand {
  name: string
  /** 32x32 로고 이미지 주소 (img.brand-logo, DejaFix: /logo-mark.png) */
  logoSrc: string
}

export interface SidePanel {
  open: boolean
  title: ReactNode
  /** 제목 아래 작은 글씨 */
  sub?: ReactNode
  onClose: () => void
  children: ReactNode
  /** 아래 고정 영역 (입력칸 등) */
  footer?: ReactNode
}

export interface AppShellProps {
  brand: Brand
  nav: NavItem[]
  /** 라우터 링크로 바꿀 때 (nav.tsx의 LinkRenderer 참고) */
  renderLink?: LinkRenderer
  /** 상단바 안 요소들. 순서대로 놓인다. 남는 폭을 채울 요소에는 style={{ flex: 1 }}을 주고, 알림 자리는 <ToastOutlet />로 둔다 */
  topbar?: ReactNode
  /** 오른쪽에서 밀고 들어오는 패널 (DejaFix .assistant 패널의 틀). open이면 본문이 왼쪽으로 밀린다 */
  panel?: SidePanel
  /** 사이드바 접힘 상태를 기억할 localStorage 키. 비우면 기억하지 않는다 */
  storageKey?: string
  /** 상단바 아래 전역 안내 띠 */
  banner?: ReactNode
  children: ReactNode
}

function readCollapsed(key?: string): boolean {
  if (!key) return false
  try {
    return localStorage.getItem(key) === 'collapsed'
  } catch {
    return false
  }
}

/**
 * DejaFix Layout의 화면 뼈대: 어두운 사이드바(접기) + 상단바 + 본문 + 오른쪽 패널.
 * 900px 이하에서는 사이드바 대신 가로 메뉴가 나온다.
 */
export function AppShell({
  brand,
  nav,
  renderLink = defaultLink,
  topbar,
  panel,
  storageKey,
  banner,
  children,
}: AppShellProps) {
  const [collapsed, setCollapsed] = useState(() => readCollapsed(storageKey))
  const toggleSidebar = () =>
    setCollapsed((v) => {
      if (storageKey) {
        try {
          localStorage.setItem(storageKey, v ? 'open' : 'collapsed')
        } catch {
          /* 저장이 막혀도 화면은 동작한다 */
        }
      }
      return !v
    })

  return (
    <div className={`shell${collapsed ? ' collapsed' : ''}`}>
      <aside className="sidebar">
        <div className="side-top">
          <button
            type="button"
            className="hamburger"
            aria-label={collapsed ? '메뉴 펼치기' : '메뉴 접기'}
            aria-expanded={!collapsed}
            onClick={toggleSidebar}
          >
            <Icon name="menu" size={20} />
          </button>
          <img className="brand-logo" src={brand.logoSrc} alt="" width={32} height={32} />
          <span className="brand-name">{brand.name}</span>
        </div>
        {nav.map((item) => (
          <span key={`${item.href}|${item.label}`} style={{ display: 'contents' }}>
            {renderLink({
              item,
              className: cx('nav-item', item.active && 'active'),
              children: (
                <>
                  {item.icon && <Icon name={item.icon} />}
                  <span className="nav-label">{item.label}</span>
                  {item.soon ? <span className="nav-soon">준비 중</span> : null}
                  {item.badge ? <span className="nav-badge">{item.badge}</span> : null}
                </>
              ),
            })}
          </span>
        ))}
      </aside>

      <div className={`main ${panel?.open ? 'panel-open' : ''}`}>
        <header className="topbar">{topbar}</header>
        <nav className="mobile-nav" aria-label="메뉴">
          {nav.map((item) => (
            <span key={`${item.href}|${item.label}`} style={{ display: 'contents' }}>
              {renderLink({
                item,
                className: cx(item.active && 'active'),
                children: `${item.label}${item.soon ? ' (준비 중)' : ''}`,
              })}
            </span>
          ))}
        </nav>
        {banner}
        {children}
      </div>

      {panel && (
        <aside
          className={`assistant ${panel.open ? 'open' : ''}`}
          aria-label={typeof panel.title === 'string' ? panel.title : undefined}
          aria-hidden={!panel.open}
        >
          <div className="assistant-head">
            <div style={{ flex: 1 }}>
              <div className="assistant-title">{panel.title}</div>
              {panel.sub && <div className="assistant-sub">{panel.sub}</div>}
            </div>
            <button type="button" className="btn small" onClick={panel.onClose} aria-label="닫기">
              <Icon name="close" size={14} />
            </button>
          </div>
          <div className="assistant-body">{panel.children}</div>
          {panel.footer && <div className="assistant-foot">{panel.footer}</div>}
        </aside>
      )}
    </div>
  )
}
