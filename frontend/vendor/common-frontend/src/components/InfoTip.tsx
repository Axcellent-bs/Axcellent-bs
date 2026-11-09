/** (i)에 마우스를 올리거나 Tab으로 가면 설명 말풍선이 뜬다. DejaFix 마크업 그대로 (span.info-tip[data-tip]) */
export function InfoTip({ text }: { text: string }) {
  return (
    <span className="info-tip" tabIndex={0} role="note" aria-label={text} data-tip={text}>
      i
    </span>
  )
}
