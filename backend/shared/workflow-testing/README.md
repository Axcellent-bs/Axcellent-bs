# workflow-testing (`ax_workflow_testing`)

기획(`planning_agent`)·개발(`dev_agent`) 두 서비스를 메모리 안에서 이어 시험하는 도우미예요. 서비스 코드는 쓰지 않고 시험만 써요.
도메인끼리는 import하지 않는 규칙(`.importlinter`)이 있어서, 두 도메인을 함께 부르는 시험 도우미는 여기에 둬요.

| 모듈 | 내용 |
|---|---|
| `wire.py` | `Wire`(기획 ↔ 개발 넘기기를 가짜 송신기로), `feed`(개발 서비스 앞에 기획 서비스 붙이기), `plan`(기획 액션을 돌리고 개발로 넘기기) |
| `fixtures.py` | 두 시험 폴더가 같이 쓰는 pytest fixture (`service` 역할 표시, 목 쓰기 토큰, A2A 본문, `wire`) |
