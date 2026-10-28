// 실제 백엔드 데이터 출처가 아직 없는 화면에 다는 "준비 중" 표시.
// 백엔드는 실제 모드에서 지어내지 않고 빈 목록을 돌려준다 (dashboard-api routes.py의 mock_only 응답).
// 출처가 연결되면 이 표시를 뗀다.

export function PreparingChip() {
  return <span className="chip bg-violet-50 text-violet-700">준비 중</span>;
}

export function PreparingNotice({ what, source }: { what: string; source: string }) {
  return (
    <p role="status" className="rounded-xl bg-violet-50 px-4 py-3 text-sm text-violet-800">
      아직 실제 데이터 출처가 연결되지 않아 {what} 목록이 비어 있어요. {source}에 연결되면 여기에 나타납니다.
    </p>
  );
}
