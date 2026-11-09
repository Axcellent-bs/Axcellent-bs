import { useId, type InputHTMLAttributes, type ReactNode, type SelectHTMLAttributes } from 'react'
import { cx } from '../lib/cx'

export interface FieldProps {
  label: ReactNode
  /** 있으면 입력칸 아래 빨간 글씨(.form-error)로 표시하고 aria-invalid를 붙인다 */
  error?: ReactNode
  /** id 등을 받아 입력칸에 붙이는 함수 */
  children: (control: { id: string; 'aria-describedby'?: string; 'aria-invalid'?: true }) => ReactNode
  className?: string
}

/** 라벨 + 입력칸. DejaFix 로그인 화면 마크업: label.field (글 + input), 오류는 p.form-error */
export function Field({ label, error, children, className }: FieldProps) {
  const id = useId()
  const errId = `${id}-err`
  return (
    <>
      <label className={cx('field', className)} htmlFor={id}>
        {label}
        {children({ id, 'aria-describedby': error ? errId : undefined, 'aria-invalid': error ? true : undefined })}
      </label>
      {error && (
        <p role="alert" className="form-error" id={errId}>
          {error}
        </p>
      )}
    </>
  )
}

/** Field 안에서 쓰는 입력칸 (스타일은 .field input) */
export function Input(props: InputHTMLAttributes<HTMLInputElement>) {
  return <input {...props} />
}

export interface SelectProps extends SelectHTMLAttributes<HTMLSelectElement> {
  options: { value: string; label: string }[]
}

/** 고르기 상자 (DejaFix .select, 32px) */
export function Select({ options, className, ...rest }: SelectProps) {
  return (
    <select className={cx('select', className)} {...rest}>
      {options.map((o) => (
        <option key={o.value} value={o.value}>
          {o.label}
        </option>
      ))}
    </select>
  )
}

export interface SwitchProps {
  checked: boolean
  onChange: (next: boolean) => void
  /** 스크린리더용 이름 (예: '알림 켜기'). 꼭 넣는다 */
  ariaLabel: string
  disabled?: boolean
}

/** 켜기/끄기 스위치. DejaFix 마크업: button.switch[role=switch][aria-checked] */
export function Switch({ checked, onChange, ariaLabel, disabled }: SwitchProps) {
  return (
    <button
      type="button"
      role="switch"
      className="switch"
      aria-checked={checked}
      aria-label={ariaLabel}
      disabled={disabled}
      onClick={() => onChange(!checked)}
    />
  )
}
