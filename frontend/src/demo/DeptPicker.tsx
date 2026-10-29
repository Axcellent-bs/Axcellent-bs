import { useState } from 'react'

// 부서 고르기. 목록에 없으면 "직접 입력"으로 적는다. 회원가입·회원 연결·받은 요청 보내기가 같이 쓴다.
export const DEPTS = ['기획팀', '개발팀', '고객지원팀', '재무팀', '마케팅팀', '인사팀', '데이터분석팀', '인프라팀', '경영지원팀']
const CUSTOM = '__custom'

export function DeptPicker({ value, onChange, className = '', label = '부서', size = 'md' }: {
  value: string
  onChange: (dept: string) => void
  className?: string
  label?: string
  size?: 'md' | 'sm'
}) {
  const [custom, setCustom] = useState(() => !!value && !DEPTS.includes(value))
  const field = size === 'sm' ? 'field w-auto py-1.5 text-xs' : 'field'
  return (
    <div className={`flex gap-2 ${className}`}>
      <select className={field} aria-label={label} value={custom ? CUSTOM : value}
        onChange={(e) => {
          const v = e.target.value
          if (v === CUSTOM) {
            setCustom(true)
            onChange(DEPTS.includes(value) ? '' : value)
          } else {
            setCustom(false)
            onChange(v)
          }
        }}>
        <option value="" disabled>부서 선택</option>
        {DEPTS.map((d) => <option key={d} value={d}>{d}</option>)}
        <option value={CUSTOM}>직접 입력</option>
      </select>
      {custom && (
        <input className={size === 'sm' ? 'field w-32 py-1.5 text-xs' : 'field'} value={value} onChange={(e) => onChange(e.target.value)}
          placeholder="부서 이름" aria-label={`${label} 직접 입력`} autoFocus={!value} />
      )}
    </div>
  )
}
