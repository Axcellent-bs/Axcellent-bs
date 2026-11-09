import type { AnchorHTMLAttributes, ButtonHTMLAttributes } from 'react'
import { cx } from '../lib/cx'
import { Icon, type IconName } from './Icon'

/** DejaFix 버튼 3종: 기본(.btn), 주요(.btn.primary), 강조(.btn.dark) */
export type ButtonVariant = 'default' | 'primary' | 'dark'

export interface ButtonProps extends ButtonHTMLAttributes<HTMLButtonElement> {
  variant?: ButtonVariant
  /** small: .btn.small */
  size?: 'md' | 'small'
  /** 글자 앞 아이콘 (DejaFix 'AI 어시스턴트' 버튼처럼) */
  icon?: IconName
}

export function Button({
  variant = 'default',
  size = 'md',
  icon,
  type = 'button',
  className,
  children,
  ...rest
}: ButtonProps) {
  return (
    <button
      type={type}
      className={cx('btn', variant !== 'default' && variant, size === 'small' && 'small', className)}
      {...rest}
    >
      {icon && <Icon name={icon} size={16} />}
      {children}
    </button>
  )
}

export interface IconButtonProps extends Omit<AnchorHTMLAttributes<HTMLAnchorElement>, 'children'> {
  href: string
  icon: IconName
  /** 스크린리더용 이름. 꼭 넣는다 */
  label: string
  /** 오른쪽 위 점 (새 알림 등) */
  dot?: boolean
}

/** 상단바 아이콘 링크. DejaFix 상단바 알림(.bell + .bell-dot)과 같은 마크업 */
export function IconButton({ href, icon, label, dot, className, ...rest }: IconButtonProps) {
  return (
    <a href={href} className={cx('bell', className)} aria-label={label} {...rest}>
      <Icon name={icon} size={20} />
      {dot && <i className="bell-dot" />}
    </a>
  )
}
