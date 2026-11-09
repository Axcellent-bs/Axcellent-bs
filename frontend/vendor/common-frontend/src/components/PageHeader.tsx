import { Fragment, type ReactNode } from 'react'

export interface Crumb {
  label: string
  href?: string
}

export interface PageHeaderProps {
  title: ReactNode
  sub?: ReactNode
  /** 제목 위 경로. 마지막 항목은 현재 페이지 */
  crumbs?: Crumb[]
}

/** 페이지 제목 줄. DejaFix 마크업: div.crumb (링크 / 현재) + .page-head > h1.page-title + p.page-sub */
export function PageHeader({ title, sub, crumbs }: PageHeaderProps) {
  return (
    <>
      {crumbs && crumbs.length > 0 && (
        <div className="crumb">
          {crumbs.map((c, i) => (
            <Fragment key={`${c.label}-${i}`}>
              {i > 0 && ' / '}
              {c.href ? <a href={c.href}>{c.label}</a> : c.label}
            </Fragment>
          ))}
        </div>
      )}
      <div className="page-head">
        <h1 className="page-title">{title}</h1>
        {sub && <p className="page-sub">{sub}</p>}
      </div>
    </>
  )
}
