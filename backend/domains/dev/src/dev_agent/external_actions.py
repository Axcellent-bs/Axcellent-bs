"""외부 연결에 쓰는 액션 목록 (안전 레이어 연결 보안 규칙용).

이 목록의 액션은 실행 관문(execute)에 갈 때 params에 항상 `connection_id`가 실린다
(`DevService._execute`가 채운다). 안전 레이어는 이 목록에 있는 액션에만
"연결이 긴급 취약(KEV + 취약 버전)이면 C 보류", "connection_id가 빠지면 C 보류"를 건다.

| 액션 | 외부에 하는 일 | connection_id 출처 |
| --- | --- | --- |
| `generate_pr` | 브랜치·커밋·PR 올리기 | 작업의 코딩 연결 · PR이 올라갈 연결 |
| `code_write` | 열린 PR 브랜치에 수정 커밋 | 작업의 코딩 연결 |
| `review_pr` | PR에 리뷰 결과 남기기 | PR이 올라간 연결 |
| `merge_pr` | PR 병합 | PR이 올라간 연결 |
| `dev_task_cancel` | 열린 PR 닫기·브랜치 지우기 | 작업의 코딩 연결 |
| `repo_comment` | 레포 이슈·PR 코멘트 (사람) | 레포 작업 공간 연결 |
| `repo_close_pr` · `repo_reopen_pr` | PR 닫기·다시 열기 (사람) | 레포 작업 공간 연결 |
| `repo_revert_pr` | 되돌리기 PR 열기 (사람) | 레포 작업 공간 연결 |
| `repo_delete_branch` | 브랜치 지우기 (사람) | 레포 작업 공간 연결 |

- 액션을 더하거나 뺄 때는 안전 등록표(`registry.toml`)의 그 액션 `connection_field`도 같이
  고친다. 둘이 어긋나면 시험이 실패한다 (안전 레이어 #419: connection_id가 비면 C 보류).
- 연결을 정하지 못하면 첫 연결, 연결이 하나도 없으면(시연용 목 부품) `mock`을 싣는다.
  빠진 채로 관문에 가는 일은 없다.
- 배포(`request_deploy`·`approve_deploy`)는 아직 외부 배포 도구 연결이 없어 목록에 없다.
  배포 도구 연결 계층(Jenkins·Argo CD 등)이 생기면 그 연결 id로 더한다.
"""

from __future__ import annotations

VCS_WRITE_ACTIONS: frozenset[str] = frozenset(
    {
        "generate_pr",
        "code_write",
        "review_pr",
        "merge_pr",
        "dev_task_cancel",
        "repo_comment",
        "repo_close_pr",
        "repo_reopen_pr",
        "repo_revert_pr",
        "repo_delete_branch",
    }
)

# 연결이 하나도 없을 때(시연용 목 부품) 싣는 연결 id
MOCK_CONNECTION = "mock"
