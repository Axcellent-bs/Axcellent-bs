"""의뢰 일 목록: 분류기가 고를 수 있는 일과, 일마다 어느 부서의 어떤 skill을 어떤 순서로 시키는지.

분류 LLM(`request_classifier.py`)은 이 목록에 있는 `name`만 고를 수 있고, 순서와 등급은 여기서
정한 대로만 돈다(LLM은 순서를 만들지 못한다). 부서 이름(`dept`)과 skill은 부서 카드의 것과 같아야
하며 시험(`test_request_tasks.py`)이 카드와 맞는지 확인한다.

- 흐름: 부서가 이어지는 일(홍보 영상: 데이터분석 → 기획 → 사람 확인 → 마케팅).
- 단독 의뢰: 부서 하나의 skill 하나를 시키는 일. 처음에는 읽기 위주이고, 상태를 바꾸는 일은
  등급(A·B·C)에 따라 안전 관문과 승인을 거친다.
- `blocked_by`가 있으면 끝까지 돌릴 준비가 안 된 일이다(다른 팀 작업 대기). 접수·분류는 되고
  실행(2단계)이 이 값을 보고 사람에게 넘긴다.
부서 일 조사: `backend/docs/notes/orchestrator/request-catalog.md`.
"""

from __future__ import annotations

from dataclasses import dataclass, replace
from typing import Any

DEPT_LABEL = {
    "data": "데이터분석",
    "planning": "기획",
    "marketing": "마케팅",
    "support": "고객지원",
    "finance": "재무",
    "dev": "개발",
    "infra": "인프라",
    "hr": "인사",
    "archive": "아카이브",
}
READ_GRADE = "read"
HUMAN_GRADE = "C"  # 총괄이 사람 승인을 먼저 받는 등급(`human_gate`가 있는 단계도 같다)
BRAND_CHARACTER_KEY = "brand_character_record_id"  # 홍보 영상에 쓸 회사 대표 캐릭터 (11/23)
# 11/24: 홍보 영상은 회사 캐릭터 답을 먼저 받는다. 이 사람 단계는 사람 확인(승인·거절)이 아니라
# 캐릭터 답(POST /api/requests/{id}/brand-character)으로만 닫힌다
CHARACTER_SKILL = "brand_character"
CHARACTER_STEP_ID = "character"


@dataclass(frozen=True)
class TaskStep:
    step_id: str
    dept: str | None  # 사람 확인 단계(kind == "human")는 None
    skill: str
    label: str
    grade: str | None  # read · A · B · C (사람 확인 단계는 None)
    after: tuple[str, ...] = ()
    kind: str = "agent"  # agent | human
    # 결과 번호: 이 단계 결과에서 부서가 정한 번호를 읽을 칸 이름(예: "report_id"),
    # 그리고 이 단계가 앞 단계 번호를 받을 입력 칸 이름. 큰 결과는 부서 저장소에 두고 번호만 넘긴다
    ref_key: str | None = None
    takes_ref: str | None = None
    # 부서가 요구하는 고정 입력(예: 긴 작업으로 받기 `caller`), 고객 문장을 넣을 입력 칸 이름
    fixed_input: tuple[tuple[str, Any], ...] = ()
    text_key: str | None = None
    # 이 단계 결과에서 다음 단계가 그대로 받을 값의 칸 이름 / 앞 단계가 둔 값을 입력으로 받는다
    carries: str | None = None
    takes_carry: bool = False
    # 고객 문장에서 부서 입력 칸을 채우는 규칙 이름 (`request_inputs.RULES`)
    fill: str | None = None
    # 고객 문장에서 입력이 나오는 상태 변경이라 총괄이 사람 승인을 먼저 받는 단계다. 분류 LLM이
    # 고객 글에 속아도 승인 없이는 실행되지 않게 한다. 입력이 앞 부서 결과에서만 오는 단계는
    # 부서의 안전 관문에 맡긴다
    human_gate: bool = False
    # 앞 단계 결과를 그대로 받는 단계 설정. `carry_keys`는 이 단계 결과의 여러 칸(예: proposal_id·
    # video_plan)을 한 묶음으로 다음 단계에 넘기고, `carry_from`은 넘겨받을 단계(없으면 `after`),
    # `on_missing`은 넘길 값이 없을 때 건너뛸지(skip) 실패로 닫을지(fail)다
    carry_keys: tuple[str, ...] = ()
    carry_from: str | None = None
    on_missing: str = "skip"
    # 이 단계 호출에 기다리는 시간(초). 없으면 실행기 기본값(30초). 영상 제작처럼 오래 걸리는 일용
    timeout_s: float | None = None
    # 의뢰 기록에서 값이 있으면 입력에 그대로 더할 칸 이름(11/23 홍보 영상: 사람이 고른 회사 대표
    # 캐릭터 `brand_character_record_id`). 사람이 화면에서 정한 번호만 넣는다(고객 글이 아니다)
    row_input: tuple[str, ...] = ()
    # 의뢰 기록 값을 입력에 더하기"만" 하는 칸(11/26 기획 제작안이 고른 회사 캐릭터를 따르게).
    # row_input과 달리 캐릭터 바꾸기(_reprepare)·넘겨받은 값 남기기·예전 기록 번호 붙이기에 쓰지
    # 않는다(row_input에 BRAND_CHARACTER_KEY가 있는 단계 = 영상 초안 준비 단계, #1838)
    pass_row: tuple[str, ...] = ()


@dataclass(frozen=True)
class TaskDef:
    name: str  # 분류기가 고르는 값(영어 소문자와 _)
    label: str  # 화면·안내에 쓰는 이름
    description: str  # 분류기가 읽는 설명
    examples: tuple[str, ...]  # 이런 글이 오면 이 일이다
    steps: tuple[TaskStep, ...]
    blocked_by: str | None = None
    note: str | None = None  # 접수 안내에 붙일 "어떻게 읽었는지" (예: 가장 최근 캠페인 기준)

    @property
    def first_dept(self) -> str | None:
        """처음 일을 받는 부서 (사람 단계는 건너뛴다: 홍보 영상은 캐릭터 답 뒤 데이터분석)."""
        first = next((s for s in self.steps if s.kind != "human"), self.steps[0])
        return first.dept

    @property
    def asks_character_first(self) -> bool:
        """회사 캐릭터 답을 먼저 받고 시작하는 일 (11/24 홍보 영상)."""
        return self.steps[0].skill == CHARACTER_SKILL

    @property
    def needs_human_approval(self) -> bool:
        """총괄이 부서에 보내기 전에 사람 승인을 받는 일. 나머지 A·B는 부서의 안전 관문이 정한다."""
        return any(s.human_gate or s.grade == HUMAN_GRADE for s in self.steps)

    @property
    def changes_state(self) -> bool:
        return any(s.grade not in (READ_GRADE, None) for s in self.steps)


def _one(
    name: str,
    label: str,
    description: str,
    examples: tuple[str, ...],
    dept: str,
    skill: str,
    grade: str = READ_GRADE,
    blocked_by: str | None = None,
    fill: str | None = None,
    human_gate: bool = False,
) -> TaskDef:
    step = TaskStep("run", dept, skill, label, grade, fill=fill, human_gate=human_gate)
    return TaskDef(name, label, description, examples, (step,), blocked_by)


# 트렌드 보고서는 몇 분 걸리는 긴 작업이다: `caller=orchestrator`로 불러야 부서가 `WORKING`으로 받고
# 진행·완료를 알린다. 부서는 고객 문장을 `input.request_text`로만 읽는다(`untrusted`는 읽지 않는다).
TREND_STEP = TaskStep(
    "trend",
    "data",
    "trend_report_create",
    "트렌드 분석 보고서",
    READ_GRADE,
    ref_key="report_id",
    fixed_input=(("caller", "orchestrator"),),
    text_key="request_text",
)

PROMO_VIDEO = TaskDef(
    "promo_video",
    "홍보 영상 만들기",
    "상품·서비스를 알리는 영상을 만들어 달라는 의뢰. 트렌드를 분석하고 영상 제작안을 정한 뒤 "
    "사람이 확인하고 제작한다(여러 부서가 차례로 한다).",
    (
        "홍보 영상 만들어 주세요",
        "신간 소개 숏츠 영상 만들어 줘",
        "우리 브랜드 광고 영상이 필요해요",
    ),
    (
        # 11/24: 캐릭터를 고르기 전에 기획서가 회사 기본 마스코트로 짜이지 않게 답을 먼저 받는다
        # (답이 없으면 기다린다. 자동으로 "캐릭터 없이" 가지 않는다)
        TaskStep(
            CHARACTER_STEP_ID, None, CHARACTER_SKILL, "회사 캐릭터 고르기", None, kind="human"
        ),
        replace(TREND_STEP, after=(CHARACTER_STEP_ID,)),
        TaskStep(
            "insight",
            "data",
            "trend_report_insight",
            "기획에 넘길 인사이트",
            READ_GRADE,
            after=("trend",),
            takes_ref="report_id",
            carries="insight",
        ),
        TaskStep(
            "plan",
            "planning",
            "receive_insight",
            "영상 제작안",
            "A",
            after=("insight",),
            takes_carry=True,
            carry_keys=("proposal_id", "video_plan"),
            # 11/23: 고객 문장의 길이("30초 이내")를 기획이 제작안 길이로 쓴다 (문장은 데이터로만)
            text_key="request_text",
            # 11/26: 사람이 고른 회사 캐릭터 번호를 기획에도 넘긴다(제작안 주인공 = 그 캐릭터)
            pass_row=(BRAND_CHARACTER_KEY,),
        ),
        TaskStep("confirm", None, "human_check", "사람 확인", None, after=("plan",), kind="human"),
        # 사람이 제작안을 확인한 뒤: 마케팅이 제작안으로 SNS 초안·장면 템플릿을 정하고(읽기),
        # 그 결과(`generate_media_input`)를 그대로 영상 제작(B)에 넘긴다
        # 11/23: 의뢰자가 회사 대표 캐릭터를 골랐으면 그 번호를 같이 넘긴다. 마케팅이 의뢰용 제작
        # 기록(이미지 시트·스토리보드)을 만들고 그 번호(`creative_record_id`)가 이 단계 결과 번호다.
        # 그때 영상 제작은 캐릭터 경로(그림 B + 영상 C 사람 승인)이고, 없으면 지금 그대로다
        TaskStep(
            "prepare",
            "marketing",
            "prepare_video_draft_from_plan",
            "영상 초안 준비",
            READ_GRADE,
            after=("confirm",),
            takes_carry=True,
            carry_from="plan",
            carries="generate_media_input",
            on_missing="fail",
            ref_key="creative_record_id",
            row_input=(BRAND_CHARACTER_KEY,),
        ),
        TaskStep(
            "make",
            "marketing",
            "generate_media",
            "영상 제작",
            "B",
            after=("prepare",),
            takes_carry=True,
            on_missing="fail",
        ),
    ),
)

TASKS: tuple[TaskDef, ...] = (
    PROMO_VIDEO,
    TaskDef(
        "trend_report",
        "트렌드·경쟁사 분석 보고서",
        "시장 트렌드, 경쟁사, 타깃 선호를 분석한 보고서를 만들어 달라는 의뢰(몇 분 걸린다).",
        ("경쟁사 대비 트렌드 보고서 만들어 주세요", "20대 여성 타깃 화장품 트렌드 분석해 줘"),
        (replace(TREND_STEP, step_id="run", label="트렌드·경쟁사 분석 보고서"),),
    ),
    # 캠페인 번호는 고객이 모르므로 마케팅 목록에서 가장 최근 것을 찾아 그 번호로 성과를 묻는다
    TaskDef(
        "campaign_performance",
        "캠페인·영상 성과 조회",
        "지난 캠페인이나 영상의 성과(조회수·효과)를 알려 달라는 의뢰.",
        ("지난 캠페인 결과 알려 주세요", "올린 영상 성과 어때요"),
        (
            TaskStep(
                "latest",
                "marketing",
                "list_campaign_briefs",
                "최근 캠페인 찾기",
                READ_GRADE,
                fixed_input=(("limit", 1),),
                ref_key="items.0.campaign_id",
            ),
            TaskStep(
                "run",
                "data",
                "campaign_result",
                "캠페인 성과 조회",
                READ_GRADE,
                after=("latest",),
                takes_ref="campaign_id",
            ),
        ),
        note="가장 최근 캠페인 기준",
    ),
    # 올릴 영상 번호를 고객이 모르므로 마케팅 의뢰 영상 목록에서 가장 최근 것을 찾아 쓴다.
    # 초안(제목·설명 또는 캡션·해시태그)은 없으면 AI가 만들고, 올리기는 등급 C라 늘 사람 승인이다
    TaskDef(
        "video_publish_youtube",
        "의뢰 영상 유튜브에 올리기",
        "가장 최근에 만든 의뢰 영상(AI 라벨 입힌 파일)을 유튜브에 올려 달라는 의뢰. "
        "초안을 쓰고 사람 승인을 거쳐 올린다.",
        ("이 영상 유튜브에 올려 주세요", "최근 만든 영상 유튜브에 올려 줘"),
        (
            TaskStep(
                "latest",
                "marketing",
                "list_request_videos",
                "최근 의뢰 영상 찾기",
                READ_GRADE,
                # 11/26: 유튜브에 이미 올렸거나 승인 대기 중인 영상은 "최근"에서 뺀다
                # (같은 영상을 또 올리기 요청하는 걸 막는다)
                fixed_input=(("limit", 1), ("exclude_platform", "youtube")),
                ref_key="items.0.post_draft_id",
            ),
            TaskStep(
                "draft",
                "marketing",
                "request_video_youtube_draft",
                "유튜브 제목·설명 초안",
                READ_GRADE,
                after=("latest",),
                takes_ref="post_draft_id",
                ref_key="post_draft_id",
            ),
            TaskStep(
                "run",
                "marketing",
                "request_video_youtube_request",
                "유튜브 올리기 요청",
                HUMAN_GRADE,
                after=("draft",),
                takes_ref="post_draft_id",
            ),
        ),
        note="가장 최근 의뢰 영상 기준",
    ),
    TaskDef(
        "video_publish_instagram",
        "의뢰 영상 인스타그램(릴스)에 올리기",
        "가장 최근에 만든 의뢰 영상(AI 라벨 입힌 파일)을 인스타그램 릴스로 올려 달라는 의뢰. "
        "초안을 쓰고 사람 승인을 거쳐 올린다.",
        ("이 영상 인스타에 올려 주세요", "최근 만든 영상 릴스로 올려 줘"),
        (
            TaskStep(
                "latest",
                "marketing",
                "list_request_videos",
                "최근 의뢰 영상 찾기",
                READ_GRADE,
                # 11/26: 인스타에 이미 올렸거나 승인 대기 중인 영상은 "최근"에서 뺀다
                fixed_input=(("limit", 1), ("exclude_platform", "instagram")),
                ref_key="items.0.post_draft_id",
            ),
            TaskStep(
                "draft",
                "marketing",
                "request_video_instagram_draft",
                "인스타 캡션·해시태그 초안",
                READ_GRADE,
                after=("latest",),
                takes_ref="post_draft_id",
                ref_key="post_draft_id",
            ),
            TaskStep(
                "run",
                "marketing",
                "request_video_instagram_request",
                "인스타 릴스 올리기 요청",
                HUMAN_GRADE,
                after=("draft",),
                takes_ref="post_draft_id",
            ),
        ),
        note="가장 최근 의뢰 영상 기준",
    ),
    _one(
        "sales_analysis",
        "매출 분석",
        "기간별 매출을 분석해 달라는 의뢰.",
        ("이번 달 매출 분석해 주세요", "지난 7일 매출 정리해 줘"),
        "data",
        "sales_analysis",
        fill="sales_period",
    ),
    _one(
        "customer_segment",
        "고객층 분석",
        "연령·성별 같은 고객층의 구성이나 선호를 분석해 달라는 의뢰.",
        ("20대 여성은 뭘 많이 사요", "30대 남성이 즐겨 사는 상품 알려 주세요"),
        "data",
        "segment_question",
        fill="customer_text",
    ),
    _one(
        "anomaly_check",
        "지표 이상 점검",
        "주문·결제 같은 지표가 평소와 다르게 튄 곳이 있는지 점검해 달라는 의뢰.",
        ("어제 결제 실패가 튄 곳이 있나요", "지표에 이상한 데 없는지 봐 주세요"),
        "data",
        "anomaly_detection",
        fill="anomaly_window",
    ),
    _one(
        "event_proposal",
        "이벤트 제안",
        "다음 달 이벤트·프로모션 아이디어를 제안해 달라는 의뢰.",
        ("12월 이벤트 제안해 주세요", "연말 프로모션 아이디어 줘"),
        "data",
        "event_proposal",
        fill="month_upcoming",
    ),
    _one(
        "inquiry_status",
        "문의 현황 정리",
        "고객 문의 현황(건수·대기·만족도)을 정리해 달라는 의뢰.",
        ("이번 달 문의 현황 정리해 주세요", "답변 대기 문의가 몇 건인지 알려 줘"),
        "support",
        "inquiry_overview",
    ),
    _one(
        "claim_stats",
        "클레임 현황 정리",
        "환불·교환 클레임 현황을 집계해 달라는 의뢰.",
        ("이번 달 환불 클레임 몇 건인지 알려 주세요", "클레임 현황 정리해 줘"),
        "support",
        "claim_stats",
    ),
    _one(
        "seller_settlement",
        "판매자 정산 조회",
        "판매자 정산 내역을 조회해 달라는 의뢰.",
        ("지난주 판매자 정산 내역 보여 주세요",),
        "support",
        "seller_settlement",
        fill="settlement_week",
    ),
    _one(
        "closing_report",
        "손익 정리",
        "기간별 손익(매출·비용)을 정리해 달라는 의뢰.",
        ("이번 달 손익 정리해 주세요", "주간 결산 만들어 줘"),
        "finance",
        "closing_report",
        fill="closing_period",
    ),
    _one(
        "notice_draft",
        "공지 초안 작성",
        "고객에게 보낼 공지 글 초안을 써 달라는 의뢰.",
        ("점검 공지 초안 써 주세요", "배송 지연 안내 공지 만들어 줘"),
        "marketing",
        "draft_notice",
        fill="notice_kind",
    ),
    _one(
        "sns_draft",
        "SNS 글 초안 작성",
        "SNS에 올릴 글 초안을 써 달라는 의뢰(올리는 것은 아니다).",
        ("인스타 글 초안 만들어 주세요", "신간 소개 SNS 문구 써 줘"),
        "marketing",
        "draft_sns_post",
        fill="sns_topic",
    ),
    _one(
        "dev_status",
        "개발 진행 현황",
        "개발이 어디까지 진행됐는지 알려 달라는 의뢰.",
        ("요청한 기능 개발 어디까지 됐어요", "진행 중인 개발 작업 보여 주세요"),
        "dev",
        "list_dev_progress",
    ),
    _one(
        "similar_cases",
        "비슷한 과거 기록 찾기",
        "비슷했던 과거 장애·업무 기록을 찾아 달라는 의뢰.",
        ("비슷한 과거 장애 기록 찾아 주세요",),
        "archive",
        "find_similar_cases",
        fill="search_query",
    ),
    _one(
        "team_headcount",
        "팀별 인원 조회",
        "기간별 팀 인원을 알려 달라는 의뢰.",
        ("다음 달 팀별 인원 알려 주세요",),
        "hr",
        "team_headcount",
        fill="month_any",
    ),
    _one(
        "feature_request",
        "기능 개발 요청",
        "서비스에 없는 기능을 만들어 달라거나 고쳐 달라는 의뢰(기획이 접수한다).",
        ("회원 등급 기능 만들어 주세요", "주문 화면에 필터를 추가해 줘"),
        "planning",
        "create_ticket",
        grade="A",
        human_gate=True,
        fill="ticket_text",
    ),
    _one(
        "claim_register",
        "환불·교환 접수",
        "주문의 환불·교환을 접수해 달라는 의뢰(고객지원이 접수한다).",
        ("이 주문 환불 접수해 주세요",),
        "support",
        "create_claim",
        grade="A",
        human_gate=True,
        fill="claim_input",
    ),
)

BY_NAME: dict[str, TaskDef] = {t.name: t for t in TASKS}


def get(name: str | None) -> TaskDef | None:
    return BY_NAME.get(name or "")


def dept_label(dept: str | None) -> str:
    return DEPT_LABEL.get(dept or "", dept or "사람")


def plan_of(task: TaskDef) -> list[dict[str, Any]]:
    """`WorkRequests.set_plan`에 넘기는 계획(단계 목록)."""
    return [
        {
            "step_id": s.step_id,
            "dept": s.dept,
            "skill": s.skill,
            "label": s.label,
            "after": list(s.after),
            "grade": s.grade,
            "kind": s.kind,
            "ref_key": s.ref_key,
            "takes_ref": s.takes_ref,
            "fixed_input": dict(s.fixed_input),
            "text_key": s.text_key,
            "carries": s.carries,
            "takes_carry": s.takes_carry,
            "fill": s.fill,
            "human_gate": s.human_gate,
            "carry_keys": list(s.carry_keys),
            "carry_from": s.carry_from,
            "on_missing": s.on_missing,
            "timeout_s": s.timeout_s,
            # 의뢰 기록 칸을 입력에 더하는 단계만 둔다(다른 일의 계획 모양은 그대로)
            **({"row_input": list(s.row_input)} if s.row_input else {}),
            **({"pass_row": list(s.pass_row)} if s.pass_row else {}),
        }
        for s in task.steps
    ]


def catalog_text(tasks: tuple[TaskDef, ...] = TASKS) -> str:
    """분류기 지시문에 넣는 일 목록 (이름 | 이름표 | 설명 | 예시)."""
    lines = []
    for t in tasks:
        examples = " / ".join(f'"{e}"' for e in t.examples)
        lines.append(f"- {t.name} | {t.label} | {t.description} | 예: {examples}")
    return "\n".join(lines)
