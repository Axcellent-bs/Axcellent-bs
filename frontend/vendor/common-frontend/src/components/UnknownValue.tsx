/** 받지 못했거나 확인되지 않은 값. 0이나 빈칸으로 대신하지 않고 이것으로 표시한다. */
export function UnknownValue({ label = '확인할 수 없음' }: { label?: string }) {
  return <span className="unknown">{label}</span>
}
