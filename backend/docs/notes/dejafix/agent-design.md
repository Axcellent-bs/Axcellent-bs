# dejafix 에이전트 설계

아래는 2026-11-27 dev(`56cd5e8f`)의 코드에서 확인한 지금 상태다. 코드에 없는 것은 "정해진 것 없음"으로 남겼다.
구현: 인프라 에이전트 `backend/domains/infra-dejafix`(포트 8107, 카드 `agent-card.json`), DejaFix 프록시 `backend/shared/dejafix-proxy`(포트 8030), 화면 `frontend/src/lib/dejafix.ts`·`connectedPcs.ts`·`zabbixSettings.ts`. DejaFix 본체는 `DejaFix/DejaFix` 레포이고 AXcellent는 API로 데이터만 받는다(CLAUDE.md "지금 방향"). 운영 서버 구성은 `docs/ops/dejafix-server.md`.

1. 누구의 어떤 일을 대신하나 (하루 일 3가지):
   - 장애 감시: DejaFix의 현재 장애(`GET /api/incidents?status=problem`)를 주기적으로 읽어, 고른 부위(`component` 태그)의 장애만 총괄에 `infra.anomaly_detected`로 알리고 풀리면 `infra.recovered`를 보낸다 (`dejafix_watch.py`)
   - 장애 원인 진단과 조치: 배포 이력에서 원인 후보를 찾고(`diagnose_incident`), 문제 배포를 되돌리거나(`rollback_deployment`) 과거 같은 장애의 처리 방식을 다시 한다(`release_fault`)
   - 복구 확인: 인프라 지표와 업무 지표가 둘 다 정상인지 본다(`verify_recovery`, 30초 간격 최대 5회)
2. 스스로 판단하는 것 / 사람(safety 큐)에게 넘기는 것:
   - 스스로: 읽기 등급 skill(진단, 복구 확인, 현재 장애·최근 배포 조회). 확신도는 코드 규칙으로 정한다(LLM 안 씀)
   - 관문: `rollback_deployment`는 B 등급(교차검증), 기준에 걸리면 사람 승인. 보류는 개발·배포 › 안전 확인(`/dev/work/holds`)에서 사람이 결정한다. `release_fault`·`deploy_version`은 A 등급으로 실행 관문(`execute()`)을 거친다
   - 넘김: 원인 미확정이면 되돌리지 않고 사람에게, 다시 배포가 실패하면 처리 순서(`handoff_steps`)와 함께 사람에게
3. 다른 부서에게 받는 요청 (누가·언제·무엇) = skill 목록 (`agent-card.json`의 `allowed_requesters`):
   - orchestrator: `diagnose_incident`, `rollback_deployment`, `release_fault`, `verify_recovery`, `current_incidents`, `latest_deployments`, `receive_improvement_note`
   - marketing·support: `diagnose_incident`(읽기)
   - dev: `deploy_version`, `deploy_run_status`, `deploy_tool_services`
   - dashboard-api(인프라 AI 직접 대화): `infra_chat_send`·`infra_chat_list`·`infra_chat_get`·`infra_chat_close`·`infra_chat_remove`
4. 다른 부서에게 하는 요청 (누구에게·무엇을):
   - data: `anomaly_detection`(복구 확인의 업무 지표)
   - dev: `find_related_prs`, `revert_merged_pr`
   - safety: 실행 관문(템플릿 `execute()`)
   - 총괄: 이벤트 `infra.anomaly_detected`·`infra.recovered`
   - DejaFix: dejafix-proxy를 거쳐 읽기만(장애 목록·단건·요약, 호스트 목록). Zabbix 주소·읽기 토큰 저장은 화면에서 운영자·관리자가 프록시로 한다
5. 기억해야 하는 것 (memory 부서와 연결): 정해진 것 없음. 지금은 프로세스 메모리(진단 결과, 실행한 롤백)와 배포 이력만 갖고 있고 재시작하면 지워진다. 과거 사례 찾기·해결 기록은 총괄이 인사(`find_similar_cases`·`record_resolution`)에 맡긴다
6. 잘하는지 보는 지표: 이 에이전트 전용 지표는 정해진 것 없음. 사건별 복구 시간 비교는 데이터 분석 › 운영 지표(`/data/ops`)에 나온다

회사 분리(11/25~26): DejaFix로 가는 요청은 부르는 회사의 연결만 쓰고 부르는 회사를 싣는다. 같이 설치된 DejaFix가 회사별을 모르는 버전이면 그 데이터는 캣북스 것으로 보고 다른 회사에는 넘기지 않는다(`dejafix-proxy/src/ax_dejafix_proxy/main.py` 머리말).
