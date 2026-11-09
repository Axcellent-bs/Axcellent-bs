"""평가 대상 단계 배정표: 총괄 장애 대응 단계를 부서와 묶음에 배정하거나 제외한다.

총괄의 장애 대응 계획(`scenario_a`)에 나오는 단계 번호(step_id) 기준이다. 표에 없는 단계는
평가하지 않는다(다른 계획의 같은 이름 단계가 섞이지 않게 한다).
- 제외: 기획 AI는 사람이 확인하는 단계라 처리 시간 기준을 정할 대상이 아니고, 복구 검증은 쇼핑몰이
  언제 복구되느냐에 달려 부서 평가에서 빼고 "복구 확인에 걸린 시간"을 결과 지표로만 보인다.
- 개발 `related_prs`·`link_incident`는 총괄 계획표에 아직 (planning)으로 적혀 있어도 개발 서비스에
  있는 단계라 개발로 센다.
"""

from __future__ import annotations

from dataclasses import dataclass

LOOKUP = "조회·분석"
LLM = "LLM 분석"
ACTION = "조치"
NOTICE = "공지·기록"
ROLLBACK = "되돌리기 교차검증"


@dataclass(frozen=True)
class Stage:
    dept: str
    group: str


@dataclass(frozen=True)
class Excluded:
    reason: str


STAGES: dict[str, Stage | Excluded] = {
    "diagnose": Stage("infra", LOOKUP),
    "similar_cases": Stage("hr", LOOKUP),
    "impact": Stage("data", LOOKUP),
    "root_cause": Stage("data", LLM),
    "cause_check": Excluded("기획 AI: 사람이 확인하는 단계"),
    "rollback": Stage("infra", ROLLBACK),
    "related_prs": Stage("dev", LOOKUP),
    "link_incident": Stage("dev", ACTION),
    "notice": Stage("marketing", NOTICE),
    "publish_notice": Stage("marketing", NOTICE),
    "compensation": Stage("support", ACTION),
    "recovery": Excluded("복구 검증: 쇼핑몰 복구 시점에 달려 결과 지표로만 본다"),
    "recovered_draft": Stage("marketing", NOTICE),
    "recovered_notice": Stage("marketing", NOTICE),
    "finance_record": Stage("finance", NOTICE),
    "kpi": Stage("data", LOOKUP),
    "record": Stage("hr", NOTICE),
    "resolution": Stage("hr", NOTICE),
    "late_compensation": Stage("support", ACTION),
}


def stage_of(step_id: str) -> Stage | None:
    """평가 대상이면 배정을, 제외거나 표에 없으면 None."""
    found = STAGES.get(step_id)
    return found if isinstance(found, Stage) else None


# 화면에 보이는 단계 이름. 영어 코드는 쓰지 않고 쉬운 말로 적는다
LABELS: dict[str, str] = {
    "diagnose": "원인 진단",
    "similar_cases": "비슷한 과거 이벤트 찾기",
    "impact": "영향 분석",
    "root_cause": "원인 후보 추정",
    "rollback": "문제 배포 되돌리기",
    "related_prs": "관련 코드 변경 찾기",
    "link_incident": "이벤트에 코드 변경 연결",
    "notice": "점검 공지 초안",
    "publish_notice": "점검 공지 게시",
    "compensation": "영향 고객 일괄 보상",
    "recovered_draft": "정상화 공지 초안",
    "recovered_notice": "정상화 공지 게시",
    "finance_record": "이벤트 비용 장부 기록",
    "kpi": "처리 시간 요약",
    "record": "이벤트 기록",
    "resolution": "해결 내용 기록",
    "late_compensation": "복구 뒤 놓친 고객 보상",
}
