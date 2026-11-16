# 아카이브 AI (archive)

회사의 이슈와 결정을 카드로 남기고, 비슷한 과거를 찾아 주는 에이전트다. 인사 AI 안에 있던 **결정 기록**을
분리한 것이다(2026-11-16, Notion "아카이브 AI 작업").

- 서비스: `services/decisions`(id `decisions`, 포트 8114)가 이 도메인의 `archive_agent.main:app`을 띄운다.
- 하는 일: 카드 만들기·승인·중복 병합·내리기, 이슈 해결·시각 기록, 비슷한 과거 찾기, 유사 사기 카드,
  계획 비교, 감사 기록 조회, 발령 근거가 될 과거 결정 찾기.
- 하지 않는 일: 조직 변동·팀 인원·팀 예산 확정·휴가·에이전트 평가는 인사 AI(`domains/hr`)가 한다.
  이슈 단계 시간의 판정(정상/확인 필요)은 인사의 `judge_step_timings`를 A2A로 불러 받는다.
- 분리 직후(2단계)에는 인사와 같은 SQLite 파일(`HR_DB_PATH`)과 `HR_` 환경변수를 쓴다. 파일 분리는 4단계.

## skill 16개

| skill | 등급 | 설명 |
| --- | --- | --- |
| `record_case` | A | 부서가 보낸 이슈·결정을 카드로 남긴다 |
| `find_similar_cases` | 읽기 | 비슷한 과거 이슈와 해결 내용 |
| `find_similar_decisions` | 읽기 | 사유와 비슷한 과거 업무 결정 카드(발령 근거 찾기) |
| `find_similar_fraud_cards` | 읽기 | 비슷한 과거 사기 카드 |
| `record_resolution` | A | 이슈 카드에 해결 내용 기록 |
| `record_event_times` | A | 이슈 카드에 감지·복구 시각과 단계 시간·판정 기록 |
| `list_cards` · `list_candidate_cards` | 읽기 | 확정 카드·후보 카드 목록 |
| `decide_candidate_card` | A | 후보 카드 승인·폐기 |
| `list_duplicate_groups` | 읽기 | 사람 승인을 기다리는 중복 결정 묶음 |
| `decide_duplicate_group` · `restore_merged_card` | A | 중복 묶음 병합·되돌리기 |
| `retire_request_cards` | A | 거둬들인 요청의 결정 카드 내리기 |
| `get_audit_timeline` | 읽기 | 이슈의 감사 기록 |
| `check_plan` | 읽기 | 새 계획이 과거 기각된 안과 겹치는지 |
| `answer_planning_question` | 읽기 | 기획 AI의 질문에 답 |
