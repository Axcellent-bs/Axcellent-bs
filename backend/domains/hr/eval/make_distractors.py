"""방해 카드 500장을 만든다(합성). 골든 셋 카드 120장과 합쳐 620장으로 평가한다.

사용: python make_distractors.py  ->  cards_distractors.json (같은 시드면 같은 파일)

방해 카드는 골든 카드와 같은 형식이고 같은 기능 풀에 들어간다. 다만 다루는 서비스와 규칙이 달라서
골든 질의의 정답이 되지 않게 만든다(검색·추천·알림 등 골든에 없는 서비스, 골든에 없는 규칙).
무료 임베딩 API의 하루 한도 때문에 최대 500장만 쓴다. 같은 난수열(카드 글과 id)을 유지하려고
380·290·210개를 만든 뒤 기능별로 같은 비율로 앞에서부터 500개만 남긴다.
문장은 정해진 조각을 시드 난수로 섞어 만들므로 현실의 잡음보다 규칙적이다. 정답이 될 수 있는 카드가
섞였는지는 `audit_distractors.py`로 따로 점검한다.
"""

from __future__ import annotations

import json
import random
import sys
from collections import Counter
from datetime import UTC, datetime, timedelta
from pathlib import Path

HERE = Path(__file__).parent
SEED = 7
NUMS = [3, 5, 7, 10, 14, 20, 30, 45, 60, 90]
KEEP = 500  # 남기는 방해 카드 수(임베딩 무료 한도 때문에 이 수를 넘기지 않는다)

# ----------------------------------------------------------------------------- A. 계획 점검
REASONS_A = [
    "같은 문제가 분기마다 반복돼서",
    "담당자마다 처리 방식이 달라 혼선이 있어서",
    "비용이 예상보다 빠르게 늘어서",
    "고객 문의가 이 부분에 몰려서",
    "감사에서 기록이 부족하다는 지적을 받아서",
    "수동 처리 중 실수가 몇 번 있었어서",
    "다른 팀과 기준이 달라 조정이 필요해서",
    "장애 때 원인을 찾는 데 시간이 오래 걸려서",
    "신규 인력이 따라 하기 어려워서",
    "외부 업체와의 약속 조건이 바뀌어서",
    "사용량이 늘면서 기존 방식이 한계에 닿아서",
    "측정해 보니 효과가 분명하지 않아서",
]
ALTS_A = [
    "현재 방식 유지",
    "담당자 재량에 맡김",
    "다음 분기로 연기",
    "외부 도구 도입",
    "전체 적용 대신 일부만 시범 적용",
    "자동화 없이 점검표만 추가",
    "기준을 더 엄격하게 설정",
    "팀별로 다르게 운영",
]
SCOPES_A = [
    "해당 서비스 운영팀",
    "관련 서비스 전체",
    "신규 변경 건부터",
    "운영 환경에만",
    "전 부서 공통",
    "담당 부서 내부",
]
EXC_A = [
    "긴급 상황은 사후에 보고",
    "담당 팀장이 승인한 경우",
    "법적 요구가 있는 경우",
    "단순 오탈자 수정",
    "읽기 전용 작업",
    "사전에 공유된 일정 변경",
]
SUBJ = {
    "shop": [
        "검색 서비스",
        "추천 서비스",
        "알림 서비스",
        "배송조회 서비스",
        "리뷰 서비스",
        "찜하기 기능",
        "포인트 서비스",
        "판매자 포털",
        "상품 등록 기능",
        "고객센터 챗봇",
    ],
    "generic": [
        "관리자 콘솔",
        "메일 발송 서버",
        "이미지 변환 서버",
        "앱 푸시 서버",
        "회원 가입 기능",
        "파일 업로드 기능",
        "통계 대시보드",
        "배치 스케줄러",
    ],
}
GENERIC_SUBJ = set(SUBJ["generic"])

# (군집, 종류, 형식, 규칙 목록). 규칙의 {n}은 숫자
THEMES_A = [
    (
        "Z-A-release",
        "decision",
        "D",
        [
            "릴리스 노트를 배포 전날까지 공유한다",
            "배포 후 {n}분 동안 담당자가 지표를 지켜본다",
            "배포 전 스테이징에서 {n}시간 이상 검증한다",
            "한 번의 배포에 담는 변경을 최대 {n}건으로 제한한다",
            "기능 플래그로 먼저 숨긴 뒤 단계적으로 연다",
            "배포 담당자와 검토자를 서로 다른 사람으로 정한다",
            "배포 결과를 팀 채널에 남긴다",
            "배포 중단 기준을 오류율 {n}%로 정한다",
            "의존하는 서비스 담당에게 일정을 미리 알린다",
        ],
    ),
    (
        "Z-A-retention",
        "policy",
        "P",
        [
            "사용 기록은 {n}일이 지나면 요약만 남기고 지운다",
            "임시 저장 데이터는 {n}일 뒤 자동으로 삭제한다",
            "삭제 요청은 {n}일 안에 처리하고 기록을 남긴다",
            "백업 데이터는 {n}일 단위로 순환 보관한다",
            "오류 보고서에 포함된 사용자 정보는 {n}일 뒤 가린다",
            "내보낸 파일은 다운로드 {n}일 후 만료시킨다",
            "접속 기록은 담당 팀만 열람하도록 제한한다",
            "보관 기간이 지난 데이터 목록을 매달 점검한다",
        ],
    ),
    (
        "Z-A-access",
        "policy",
        "P",
        [
            "계정 공유를 금지하고 사람마다 계정을 발급한다",
            "비밀번호는 {n}일마다 바꾸도록 안내한다",
            "다단계 인증을 모든 관리 기능에 적용한다",
            "미사용 계정은 {n}일 뒤 잠근다",
            "권한 목록을 분기마다 담당 팀장이 확인한다",
            "관리 기능 접속은 사내망에서만 허용한다",
            "접근 요청은 사유를 적어 티켓으로 남긴다",
            "세션은 {n}분 동안 활동이 없으면 끊는다",
        ],
    ),
    (
        "Z-A-cost",
        "decision",
        "D",
        [
            "호출 비용이 월 한도의 {n}%에 닿으면 담당자에게 알린다",
            "미사용 자원은 {n}일 단위로 정리한다",
            "신규 외부 서비스는 비용 검토 후 도입한다",
            "무료 사용량 안에서만 쓰고 초과 시 기능을 줄인다",
            "비용 보고서를 매달 첫 영업일에 공유한다",
            "개발용 자원은 야간에 자동으로 끈다",
            "예약 구매는 사용량이 {n}개월 안정된 뒤에 한다",
            "비용이 급증한 항목은 원인을 {n}일 안에 보고한다",
        ],
    ),
    (
        "Z-A-incident",
        "policy",
        "P",
        [
            "장애 대응 담당은 확인 후 {n}분 안에 상황을 기록한다",
            "원인을 확정하기 전에는 임시 조치를 먼저 한다",
            "장애 종료 후 {n}일 안에 사후 분석을 작성한다",
            "대응 중 결정한 내용은 대응 채널에 남긴다",
            "같은 장애가 {n}회 반복되면 근본 원인을 따로 추적한다",
            "대응 담당이 판단하기 어려우면 상위 담당에게 넘긴다",
            "복구 확인은 담당자가 아닌 다른 사람이 한다",
            "장애 등급 기준표를 분기마다 갱신한다",
        ],
    ),
    (
        "Z-A-perf",
        "decision",
        "D",
        [
            "응답 시간 목표를 {n}00ms로 정한다",
            "느린 질의는 {n}초를 넘으면 기록한다",
            "대기열 길이가 {n}00건을 넘으면 알림을 보낸다",
            "캐시 적중률이 낮은 항목은 만료 시간을 다시 정한다",
            "무거운 작업은 사용자가 적은 시간으로 옮긴다",
            "동시 처리 수를 서버당 {n}0개로 제한한다",
            "응답 크기가 큰 목록은 페이지로 나눈다",
            "측정 결과는 매주 담당 팀에 공유한다",
        ],
    ),
    (
        "Z-A-external",
        "decision",
        "D",
        [
            "외부 업체 호출은 {n}초 안에 응답이 없으면 건너뛴다",
            "외부 업체 장애 시 안내 문구를 대신 보여 준다",
            "연동 계약은 만료 {n}일 전에 갱신 여부를 확인한다",
            "외부 응답 형식이 바뀌면 알림이 오도록 점검한다",
            "외부 호출 실패는 {n}회까지 재시도한다",
            "업체 상태 페이지를 담당자가 구독한다",
            "연동 키는 {n}일마다 교체한다",
            "업체별 담당자 연락처를 문서로 관리한다",
        ],
    ),
    (
        "Z-A-notice",
        "policy",
        "P",
        [
            "약관 변경은 시행 {n}일 전에 안내한다",
            "점수 소멸 예정 사용자에게 {n}일 전에 알린다",
            "이벤트 종료 안내는 하루 전 한 번만 보낸다",
            "안내 메일은 수신 거부한 사용자에게 보내지 않는다",
            "정기 점검 공지는 {n}일 전에 올린다",
            "중요 변경은 앱과 메일에 같이 알린다",
            "안내 문구는 담당 팀장이 한 번 검토한다",
            "안내 후 문의 건수를 일주일간 기록한다",
        ],
    ),
    (
        "Z-A-quality",
        "policy",
        "P",
        [
            "새 기능은 담당자 외 한 명이 먼저 써 본다",
            "버그 수정은 재현 절차를 함께 남긴다",
            "회귀 테스트 목록을 분기마다 정리한다",
            "화면 변경은 주요 브라우저 {n}개에서 확인한다",
            "수동 테스트 결과를 변경 건에 첨부한다",
            "테스트 데이터에는 실제 고객 정보를 쓰지 않는다",
            "코드 리뷰는 영업일 {n}일 안에 시작한다",
            "미해결 결함은 심각도별로 처리 기한을 정한다",
        ],
    ),
    (
        "Z-A-vendor",
        "decision",
        "D",
        [
            "새 협력사는 시범 운영 {n}주 뒤에 정식 계약한다",
            "협력사 평가는 분기마다 한다",
            "계약서는 법무 확인을 거친 뒤에 서명한다",
            "협력사 접근 권한은 계약 기간과 같이 만료한다",
            "납기 지연이 {n}회 반복되면 대체 업체를 알아본다",
            "협력사 담당자 변경 시 알림을 받는다",
            "비밀 유지 약정을 모든 협력사와 맺는다",
            "정산 내역은 월 단위로 대조한다",
        ],
    ),
]

# ----------------------------------------------------------------------------- B. 총괄 이관
COMP_B = [
    ("검색", "캣북스"),
    ("추천", "캣북스"),
    ("알림", "캣북스"),
    ("배송조회", "캣북스"),
    ("리뷰", "캣북스"),
    ("찜하기", "캣북스"),
    ("포인트 적립", "캣북스"),
    ("판매자 포털", "캣북스"),
    ("상품 등록", "캣북스"),
    ("고객센터 챗봇", "캣북스"),
    ("관리자 콘솔", "일반"),
    ("이미지 변환", "일반"),
    ("회원 가입", "일반"),
    ("앱 푸시", "일반"),
    ("메일 발송", "일반"),
    ("파일 업로드", "일반"),
]
SYMPTOMS_B = [
    "{c} 응답이 평소의 {n}배로 느려짐",
    "{c} 요청 중 {n}%가 오류로 실패",
    "{c} 결과가 일부 사용자에게만 비어 나옴",
    "{c} 화면이 간헐적으로 멈춤",
    "{c} 처리가 대기열에 쌓여 {n}분 지연됨",
    "{c} 데이터가 실제와 다르게 표시됨",
]
CAUSE_ACTION_B = [
    ("설정 변경이 일부 서버에만 적용됨", "설정을 모든 서버에 맞추고 재시작"),
    ("외부 업체 API가 간헐적으로 응답하지 않음", "호출을 건너뛰도록 임시 우회"),
    ("디스크 여유 공간이 부족해짐", "오래된 파일을 정리하고 보관 주기를 줄임"),
    ("메모리가 서서히 늘어 재시작 직전까지 감", "서버를 순차 재시작하고 누수 위치를 추적"),
    ("배치 작업이 같은 시간에 몰림", "배치 시작 시각을 분산"),
    ("캐시에 오래된 값이 남음", "캐시를 비우고 유효시간을 조정"),
    ("신규 배포가 이전 데이터 형식을 처리하지 못함", "직전 버전으로 되돌리고 형식 변환을 추가"),
    ("큐 소비자가 알림 없이 멈춰 있었음", "소비자를 다시 띄우고 중단 알림을 추가"),
    ("인증 토큰 만료 주기가 짧게 바뀜", "토큰 주기를 원래대로 되돌림"),
    ("로드밸런서가 비정상 서버로 요청을 보냄", "비정상 서버를 제외하고 점검 기준을 강화"),
    ("네트워크 규칙 변경으로 일부 경로가 막힘", "막힌 규칙을 복구하고 변경 검토 절차를 추가"),
    ("데이터베이스 락이 오래 유지됨", "오래 잡힌 락을 풀고 해당 쿼리를 수정"),
]
RESULTS_B = [
    "{n}분 뒤 정상화",
    "재발 방지 점검 항목을 추가",
    "영향을 받은 사용자 {n}명에게 안내",
    "이후 {n}일간 재발 없음",
    "모니터링 알림 기준을 낮춤",
    "사후 분석 문서를 남김",
]
FRAUD_B = [
    (
        "포인트 적립이 한 계정에 비정상적으로 집중됨",
        "같은 기기에서 만든 여러 계정의 연쇄 적립",
        "해당 계정 적립을 보류하고 적립 한도를 추가",
        "의심 계정 {n}개 정지 검토",
    ),
    (
        "리뷰 작성 보상 신청이 짧은 시간에 몰림",
        "내용을 복사한 리뷰로 보상을 받으려는 반복 시도",
        "중복 내용 리뷰의 보상을 제외",
        "해당 패턴이 {n}일간 줄어듦",
    ),
    (
        "가입 직후 혜택만 쓰고 이탈하는 계정이 늘어남",
        "여러 계정을 만들어 가입 혜택만 받는 행위",
        "가입 혜택에 본인 확인 단계를 추가",
        "신규 혜택 사용 계정 {n}% 감소",
    ),
    (
        "판매자 정산 요청이 같은 계좌로 반복됨",
        "서로 다른 판매자 이름으로 같은 계좌를 등록",
        "정산 계좌 중복 등록을 막고 수동 검토",
        "해당 계좌 정산을 {n}일간 보류",
    ),
]

# ----------------------------------------------------------------------------- C. 인수인계
ROLES_C = [
    "검색 운영 담당",
    "추천 모델 담당",
    "알림 운영 담당",
    "판매자 지원 담당",
    "회원 문의 담당",
    "물류 연동 담당",
    "리뷰 운영 담당",
    "데이터 파이프라인 담당",
    "보안 점검 담당",
    "비용 관리 담당",
    "계약 관리 담당",
    "프로모션 운영 담당",
]
SITUATIONS_C = [
    "프로젝트 종료 후 담당 변경",
    "파견 복귀로 담당 교체",
    "휴직 후 복귀에 따른 업무 환원",
    "해외 출장 {n}일 동안 업무 대행",
    "조직 개편으로 업무 이관",
    "겸직 해제에 따른 업무 정리",
    "정기 순환 보직에 따른 인수인계",
    "업무 분리로 일부 담당 이전",
    "계약 만료에 따른 외주 담당 교체",
]
PROGRESS_C = [
    "정기 점검 {n}건 중 일부만 끝난 상태",
    "협력사와 일정 조율이 진행 중",
    "수정 요청 {n}건이 검토 대기 중",
    "문서 정리는 끝났고 확인만 남음",
    "월간 보고 초안이 작성된 상태",
    "접수된 문의 {n}건이 답변 대기 중",
    "이전 담당이 시작한 분석이 중간 단계",
    "작업 목록은 있으나 우선순위가 정해지지 않음",
]
CAUTION_C = [
    "예전 방식과 지금 규칙이 다른 항목이 있어 확인이 필요",
    "승인 한도가 바뀌어서 이전 기록을 그대로 쓰면 안 됨",
    "문서에 없는 예외 처리가 몇 가지 있음",
    "협력사에 담당 변경을 알렸는지 확인 필요",
    "공유 계정 접근 권한을 새 담당 이름으로 다시 받아야 함",
    "마감 기한이 곧 다가와 우선순위 확인이 필요",
    "이전 담당만 아는 데이터 위치가 있어 먼저 확인",
    "작업 결과를 팀 채널에 공유하는 관례가 있음",
]
CONTACT_C = [
    "이전 담당과 {n}일간 질문 가능",
    "이전 담당은 이동 후에도 메신저로 문의 가능",
    "팀장에게 먼저 상황 공유",
    "협력사 담당 연락처는 공용 문서에 있음",
    "연락이 어려워 문서에만 의존해야 함",
    "주 1회 짧은 점검 회의로 확인",
]
NOTICES_C = [
    (
        "사내 메신저 가입 안내",
        "입사 첫날 초대 메일의 링크로 가입하고, 이름 옆에 부서를 적어 주세요. 채널은 팀 채널부터 참여합니다.",  # noqa: E501
    ),
    (
        "복지 포인트 사용 안내",
        "복지 포인트는 입사 월부터 매월 지급되며 사용 기한은 연말까지입니다. 사용 내역은 포털에서 확인합니다.",  # noqa: E501
    ),
    (
        "근무 시간 안내",
        "기본 근무 시간은 오전 9시부터 오후 6시이며, 팀과 합의한 시차 근무를 신청할 수 있습니다.",
    ),
    (
        "노트북 수령 안내",
        "노트북은 입사 전날까지 준비되며 수령 확인서를 제출해야 합니다. 설정이 어려우면 지원 요청을 남겨 주세요.",  # noqa: E501
    ),
    (
        "휴가 신청 방법 안내",
        "휴가는 사내 포털에서 사용 {n}일 전까지 신청하며, 팀장 승인 뒤에 확정됩니다.",
    ),
    (
        "사내 문서 보관 안내",
        "업무 문서는 개인 폴더가 아닌 공용 위키에 두고, 폴더 이름은 팀 규칙을 따릅니다.",
    ),
    (
        "출입증 발급 안내",
        "출입증은 첫 출근일 오전에 보안 데스크에서 발급받으며, 분실하면 즉시 신고해야 합니다.",
    ),
    (
        "멘토 배정 안내",
        "입사 후 한 달 동안 팀에서 멘토가 배정되며, 주 1회 짧은 면담으로 적응을 돕습니다.",
    ),
]


def _fmt(text: str, rng: random.Random, **kw: str) -> str:
    """{n}을 숫자로, {c}를 대상 이름으로 바꾼다."""
    text = text.replace("{n}", str(rng.choice(NUMS)))
    for key, value in kw.items():
        text = text.replace("{" + key + "}", value)
    return text


def _add(out: list, bodies: set, row: tuple) -> bool:
    """본문이 이미 있는 카드면 넣지 않는다. 넣었으면 True."""
    if row[3] in bodies:
        return False
    bodies.add(row[3])
    out.append(row)
    return True


def make_a(rng: random.Random, total: int) -> list[tuple[str, str, str, str, str]]:
    """(군집, 종류, 제목, 본문, 도메인) 목록."""
    out: list = []
    seen: set = set()
    bodies: set = set()
    subjects = SUBJ["shop"] + SUBJ["generic"]
    guard = 0
    while len(out) < total and guard < total * 50:
        guard += 1
        cluster, kind, form, rules = THEMES_A[len(out) % len(THEMES_A)]
        subj = rng.choice(subjects)
        rule = _fmt(rng.choice(rules), rng)
        if (subj, rule) in seen:
            continue
        seen.add((subj, rule))
        reason, scope = rng.choice(REASONS_A), rng.choice(SCOPES_A)
        if form == "D":
            alt = rng.choice(ALTS_A)
            body = f"결정: {subj} {rule}\n이유: {reason}\n검토한 대안: {alt}\n적용 범위: {scope}"
        else:
            exc = rng.choice(EXC_A)
            body = f"정책: {subj} {rule}\n이유: {reason}\n예외: {exc}\n적용 범위: {scope}"
        domain = "일반" if subj in GENERIC_SUBJ else "캣북스"
        _add(out, bodies, (cluster, kind, f"{subj} {rule}"[:40], body, domain))
    return out


def make_b(rng: random.Random, total: int) -> list[tuple[str, str, str, str, str]]:
    out: list = []
    bodies: set = set()
    fraud_quota = total // 10
    guard = 0
    while len(out) < total and guard < total * 50:
        guard += 1
        n_fraud = sum(1 for o in out if o[1] == "fraud")
        if n_fraud < fraud_quota and len(out) % 10 == 9:
            sym, cause, action, result = rng.choice(FRAUD_B)
            body = f"증상: {sym}\n원인: {cause}\n조치: {action}\n결과: {_fmt(result, rng)}"
            _add(out, bodies, ("Z-B-fraud", "fraud", sym[:40], body, "캣북스"))
            continue
        comp, domain = rng.choice(COMP_B)
        sym = _fmt(rng.choice(SYMPTOMS_B), rng, c=comp)
        cause, action = rng.choice(CAUSE_ACTION_B)
        result = _fmt(rng.choice(RESULTS_B), rng)
        body = f"증상: {sym}\n원인: {cause}\n조치: {action}\n결과: {result}"
        kind = "case" if len(out) % 3 == 0 else "incident"
        _add(out, bodies, ("Z-B-ops", kind, f"{sym} ({cause})"[:44], body, domain))
    return out


def make_c(rng: random.Random, total: int) -> list[tuple[str, str, str, str, str]]:
    out: list = []
    bodies: set = set()
    notices = list(NOTICES_C)
    guard = 0
    while len(out) < total and guard < total * 50:
        guard += 1
        if notices and len(out) % 12 == 11:
            title, text = notices.pop(0)
            _add(out, bodies, ("Z-C-notice", "notice", title, _fmt(text, rng), "일반"))
            continue
        role = rng.choice(ROLES_C)
        situ = _fmt(rng.choice(SITUATIONS_C), rng)
        body = (
            f"인수인계 대상: {role}\n진행 상황: {_fmt(rng.choice(PROGRESS_C), rng)}\n"
            f"주의: {rng.choice(CAUTION_C)}\n연락: {_fmt(rng.choice(CONTACT_C), rng)}"
        )
        shop = any(w in role for w in ("판매자", "리뷰", "프로모션"))
        title = f"{role} {situ}"[:40]
        _add(out, bodies, ("Z-C-handover", "case", title, body, "캣북스" if shop else "일반"))
    return out


def main(keep: int = KEEP, out_name: str = "cards_distractors.json") -> None:
    rng = random.Random(SEED)
    plan = {
        "plan_check": make_a(rng, 380),
        "escalation": make_b(rng, 290),
        "handover": make_c(rng, 210),
    }
    made = sum(len(rows) for rows in plan.values())
    left = keep
    funcs = list(plan)
    for i, f in enumerate(funcs):
        take = left if i == len(funcs) - 1 else round(keep * len(plan[f]) / made)
        plan[f] = plan[f][:take]
        left -= take
    base = datetime(2026, 8, 1, 9, 0, tzinfo=UTC)
    prefix = {"plan_check": "A", "escalation": "B", "handover": "C"}
    cards, n = [], 0
    for func, rows in plan.items():
        for i, (cluster, kind, title, body, domain) in enumerate(rows, 1):
            n += 1
            tag = prefix[func]
            created = (base + timedelta(hours=5 * n)).isoformat().replace("+00:00", "Z")
            cards.append(
                {
                    "card_id": f"dist_{tag}_{i:03d}",
                    "kind": kind,
                    "tenant_id": "shop-demo",
                    "context_id": f"ctx_dist_{tag}_{i:03d}",
                    "title": title,
                    "body": body,
                    "tags": ["합성", "방해"],
                    "status": "active",
                    "superseded_by": None,
                    "source_refs": [],
                    "author": "eval-gold",
                    "confidence": "high",
                    "created_at": created,
                    "candidate": False,
                    "extra": {
                        "synthetic": True,
                        "distractor": True,
                        "function": func,
                        "cluster": cluster,
                        "domain": domain,
                    },
                }
            )
    (HERE / out_name).write_text(
        json.dumps(cards, ensure_ascii=False, indent=1) + "\n", encoding="utf-8"
    )
    print("총", len(cards), Counter(c["extra"]["function"] for c in cards))
    print(Counter(c["kind"] for c in cards), Counter(c["extra"]["domain"] for c in cards))
    print("본문 중복:", len(cards) - len({c["body"] for c in cards}))


if __name__ == "__main__":
    # 기본은 500장. 규모 비교 실험용으로만 `--keep 880 --out cards_distractors_880.json`
    args = sys.argv[1:]
    n = int(args[args.index("--keep") + 1]) if "--keep" in args else KEEP
    name = args[args.index("--out") + 1] if "--out" in args else "cards_distractors.json"
    main(n, name)
