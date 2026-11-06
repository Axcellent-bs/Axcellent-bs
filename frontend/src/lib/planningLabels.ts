// 기획·개발 화면에 보이는 내부 이름(skill·활동 지점·근거 출처·등급)을 사람이 읽는 말로 바꾼다.
// 원래 이름은 마우스를 올리면(title) 보인다. 모르는 이름은 그대로 둔다.

export const POINT_LABEL: Record<string, string> = {
  // 다른 부서 → 기획
  create_ticket: '요청 접수',
  receive_insight: '인사이트 받음',
  receive_campaign_update: '캠페인 소식 받음',
  receive_dev_result: '개발 결과 받음',
  receive_change_result: '변경 결과 받음',
  receive_release_order: '배포 지시 받음',
  receive_request_pause: '중단 요청 받음',
  get_request_status: '진행 상황 조회',
  dev_returned: '개발이 돌려보냄',
  // 기획 → 다른 부서
  receive_campaign_brief: '마케팅에 전달',
  start_dev: '개발에 전달',
  handoff: '다른 부서에 전달',
  record_case: '인사에 기록',
  check_plan: '인사 이력 확인',
  classify_request: '요청 분류',
  request_release_check: '배포 전 확인 요청',
  pull_insights: '인사이트 받아오기',
  pull_insights_failed: '인사이트 받아오기 실패',
  'request.related': '비슷한 요청 찾기',
  // 기획 안에서
  write_spec: '기획서 작성',
  split_tasks: '작업 나누기',
  make_plan: '기획안 만들기',
  fill_spec: '빈칸 자동 채우기',
  edit_spec: '기획서 수정',
  reject_spec: '기획서 폐기',
  reject_proposal: '제안 폐기',
  delete_proposal: '제안 삭제',
  import_notion: '노션 문서 가져오기',
  gather_doc: '문서 모으기',
  pause_request: '요청 중단',
  returned_dismissed: '돌려받은 작업 닫기',
  returned_marketing: '마케팅으로 다시 보냄',
  returned_dev: '개발로 다시 보냄',
  auto_review: '자동 확정 검사',
  'audit.auto_confirmed': '자동 확정',
  'audit.warn_ignored': '경고를 보고도 확정',
  force_delete_spec: '기획서 강제 삭제',
  message: '메시지',
  // 개발
  'code.plan': '코딩 계획',
  'code.read': '코드 읽기',
  'code.write': '코드 작성',
  'code.review': '코드 검토',
  'code.ci': '자동 테스트',
  'code.queue': '코딩 대기열',
  dev_task_create: '개발 작업 만들기',
  dev_task_retry: '개발 작업 다시 시도',
  dev_task_cancel: '개발 작업 취소',
  dev_task_discard: '개발 작업 폐기',
  dev_task_return: '기획으로 돌려보냄',
  dev_task_covered: '다른 작업에 포함',
  dev_task_to_agent: 'AI에게 맡김',
  'dev.settings': '개발 설정 변경',
  find_related_prs: '관련 PR 찾기',
  link_incident: '장애 연결',
  review_pr: 'PR 검토',
  'execute(generate_pr)': 'PR 만들기',
  'execute(merge_pr)': 'PR 합치기',
  github_create_pull: 'GitHub PR 생성',
  github_merge_pull: 'GitHub PR 합치기',
  'vcs.opened': 'PR 열림',
  'vcs.merged': 'PR 합쳐짐',
}

export const pointLabel = (p: string) => POINT_LABEL[p] ?? p

// 활동 종류: 어떤 길로 일어났나
export const KIND_LABEL: Record<string, string> = { A2A: '부서 간', execute: '실행 확인', API: '외부 서비스', 사람: '사람' }
export const kindLabel = (k?: string) => (k ? KIND_LABEL[k] ?? k : '')

// 안전 등급: A는 바로 실행, B는 검사 뒤 실행, C는 사람이 승인해야 실행
export const GRADE_LABEL: Record<string, string> = { A: '자동 실행', B: '검사 후 실행', C: '사람 승인', read: '조회만' }
export const GRADE_TITLE: Record<string, string> = {
  A: '안전 등급 A: 조건만 맞으면 바로 실행',
  B: '안전 등급 B: 검사를 통과해야 실행',
  C: '안전 등급 C: 사람이 승인해야 실행',
  read: '상태를 바꾸지 않고 읽기만 함',
}

// 근거 숫자의 출처
export const SOURCE_LABEL: Record<string, string> = {
  trend_history: '검색 기록',
  trend_popular: '인기 트렌드',
  shorts_stats: '숏츠 통계',
  'shop-api': '쇼핑몰 상품',
  campaign_effect: '캠페인 성과',
  youtube_velocity: '유튜브 조회',
  trend_scan: '검색 급상승',
}
export const sourceLabel = (x: string) => SOURCE_LABEL[x] ?? x
