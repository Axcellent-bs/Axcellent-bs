"""옛 경로. 인사 연동 클라이언트는 `ax_workflow.hr_link`로 옮겼다 (기획·개발이 같이 쓴다)."""

from ax_workflow.hr_link import (  # noqa: F401
    HR_TIMEOUT_SECONDS,
    AgentCaller,
    HrClient,
    HrRecordResult,
    PlanCheck,
    default_client,
)
