/** 불러오는 중 자리 표시 (DejaFix .skeleton). 기본 높이 120px.
    DejaFix 대비 예외: 빈 상자만으로는 스크린리더가 아무것도 읽지 않아 '불러오는 중' 이름을 붙인다. */
export function Skeleton({ height }: { height?: number }) {
  return <div className="skeleton" aria-label="불러오는 중" style={height ? { height } : undefined} />
}
