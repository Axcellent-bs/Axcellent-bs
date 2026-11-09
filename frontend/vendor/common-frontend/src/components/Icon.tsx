import type { ReactElement } from 'react'

// 24x24 선 아이콘. 원본: DejaFix components/Icon.tsx (+ Layout의 메뉴 아이콘, UserMenu의 사람 아이콘).
// 색은 글자색(currentColor)을 따른다.
const paths = {
  dashboard: (
    <>
      <rect x="3" y="3" width="7" height="7" rx="1.5" />
      <rect x="14" y="3" width="7" height="7" rx="1.5" />
      <rect x="3" y="14" width="7" height="7" rx="1.5" />
      <rect x="14" y="14" width="7" height="7" rx="1.5" />
    </>
  ),
  cost: (
    <>
      <circle cx="12" cy="12" r="9" />
      <path d="M9.5 9.5c0-1 1.2-1.6 2.5-1.6s2.5.6 2.5 1.7c0 2.2-5 1.2-5 3.6 0 1.1 1.2 1.7 2.5 1.7s2.5-.6 2.5-1.5M12 6.5v1.4M12 16v1.5" />
    </>
  ),
  compare: <path d="M7 20V10M12 20V4M17 20v-7" />,
  report: (
    <>
      <path d="M7 3h7l4 4v14H7z" />
      <path d="M14 3v4h4M9.5 12h5M9.5 16h5" />
    </>
  ),
  plan: (
    <>
      <rect x="5" y="5" width="14" height="16" rx="2" />
      <path d="M9 3h6v4H9zM9 13l2 2 4-4" />
    </>
  ),
  bell: <path d="M6 16v-5a6 6 0 0112 0v5l1.5 2h-15zM10 20a2 2 0 004 0" />,
  admin: <path d="M12 3l8 3v6c0 4.5-3.4 8-8 9-4.6-1-8-4.5-8-9V6z" />,
  sparkle: (
    <path d="M12 3l1.8 5.2L19 10l-5.2 1.8L12 17l-1.8-5.2L5 10l5.2-1.8zM19 16l.8 2.2L22 19l-2.2.8L19 22l-.8-2.2L16 19l2.2-.8z" />
  ),
  search: (
    <>
      <circle cx="11" cy="11" r="6.5" />
      <path d="M16 16l4.5 4.5" />
    </>
  ),
  send: <path d="M12 19V5M6 11l6-6 6 6" />,
  close: <path d="M6 6l12 12M18 6L6 18" />,
  chevron: <path d="M9 6l6 6-6 6" />,
  logo: <path d="M5 16l4-9 3 6 2-4 5 7" />,
  menu: <path d="M4 6h16M4 12h16M4 18h16" />,
  user: (
    <>
      <circle cx="12" cy="8" r="4" />
      <path d="M4 21c1-4 4-6 8-6s7 2 8 6" />
    </>
  ),
} satisfies Record<string, ReactElement>

export type IconName = keyof typeof paths

export interface IconProps {
  name: IconName
  size?: number
}

export function Icon({ name, size = 18 }: IconProps) {
  return (
    <svg
      width={size}
      height={size}
      viewBox="0 0 24 24"
      fill="none"
      stroke="currentColor"
      strokeWidth="1.8"
      strokeLinecap="round"
      strokeLinejoin="round"
      aria-hidden="true"
    >
      {paths[name]}
    </svg>
  )
}
