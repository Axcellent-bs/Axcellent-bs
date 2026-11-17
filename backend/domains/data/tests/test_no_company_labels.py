"""분석 코드에 회사 이름(캣북스 장르·고객 속성·속성 값 이름)을 쓰지 않는다 (11/16).

이름은 회사 카탈로그 `[sales]`에서만 온다. 매출·고객·시장 분석과 이벤트 제안 코드의 문자열 상수
(주석·docstring 제외)에 `catalogs/catbooks.toml` `[sales]`의 장르 이름·속성 이름·속성 값 이름이 들어
있으면 실패한다. 이름 목록은 테스트할 때 카탈로그에서 읽는다(여기에 적지 않는다).
카탈로그 파일·테스트는 보지 않는다. 가짜 판매 출처(`sales_fake`)도 자기 설정 toml에서 읽는다.
"""

from __future__ import annotations

import ast
import tomllib
from pathlib import Path

from data_agent import catalog

SRC = Path(catalog.__file__).resolve().parent  # src/data_agent

# 이름이 우연히 일반 낱말의 일부인 문자열 (파일 이름, 문자열) → 왜 회사 이름이 아닌지.
# 늘릴 때는 회사 설정으로 옮길 수 없는 일반 낱말인지 먼저 확인한다
GENERIC: dict[tuple[str, str], str] = {
    # 유입 출처가 카탈로그 source_labels에 없을 때 묶는 이름. 캣북스 직업 값 "기타"와 글자만 겹친다
    ("sales_analysis.py", "기타 경로"): "카탈로그에 없는 유입 출처를 묶는 일반 이름",
    # 목 표본(테스트 전용) 상품 제목 = shop-api seed 상품 이름. 장르 이름 "노트"와 글자만 겹친다
    ("sales_collect.py", "무지 노트 3권 세트"): "목 표본 상품 제목 (shop-api seed 상품 이름)",
}


def analysis_files() -> list[Path]:
    """매출·고객·시장 분석, 이벤트 제안, 판매 저장소·수집·정제·품질·가짜 출처 코드."""
    files = {
        *SRC.glob("skills/sales_*.py"),
        *SRC.glob("skills/segment_*.py"),  # market_coverage도 segment_analysis.py에 있다
        *SRC.glob("skills/event_proposal*.py"),
        *SRC.glob("sales_*.py"),
        SRC / "event_proposal_store.py",
    }
    return sorted(files)


def catbooks_labels() -> set[str]:
    with (SRC / "catalogs" / "catbooks.toml").open("rb") as f:
        sales = tomllib.load(f)["sales"]
    labels = set(sales["genres"].values())
    for attr in sales["attributes"]:
        labels.add(attr["label"])
        labels.update(attr["values"].values())
    return labels


def string_constants(path: Path) -> list[tuple[int, str]]:
    """docstring을 뺀 문자열 상수 (f-string 조각 포함). 주석은 ast에 없다."""
    tree = ast.parse(path.read_text(encoding="utf-8"))
    docstrings: set[int] = set()
    for node in ast.walk(tree):
        if isinstance(node, (ast.Module, ast.ClassDef, ast.FunctionDef, ast.AsyncFunctionDef)):
            body = node.body
            if body and isinstance(body[0], ast.Expr) and isinstance(body[0].value, ast.Constant):
                docstrings.add(id(body[0].value))
    return [
        (node.lineno, node.value)
        for node in ast.walk(tree)
        if isinstance(node, ast.Constant) and isinstance(node.value, str)
        and id(node) not in docstrings
    ]  # fmt: skip


def test_the_check_covers_the_analysis_code() -> None:
    names = {p.name for p in analysis_files()}
    assert {
        "sales_analysis.py", "segment_analysis.py", "event_proposal.py", "event_proposal_store.py",
        "sales_store.py", "sales_collect.py", "sales_quality.py", "sales_clean.py", "sales_fake.py",
    } <= names  # fmt: skip
    labels = catbooks_labels()
    assert {"소설", "연령대", "20대", "여성"} <= labels  # 카탈로그를 제대로 읽었는지


def test_the_check_finds_a_planted_label(tmp_path: Path) -> None:
    planted = tmp_path / "planted.py"
    planted.write_text(
        '"""소설 이야기는 docstring이라 괜찮다."""\n'
        "# 20대 여성 (주석도 괜찮다)\n"
        'LABEL = "20대 여성"\n'
        'def f(x):\n    """에세이 (docstring)"""\n    return f"{x} 소설"\n',
        encoding="utf-8",
    )
    labels = catbooks_labels()
    hits = sorted({v for _, v in string_constants(planted) if any(lab in v for lab in labels)})
    assert hits == [" 소설", "20대 여성"]


def test_analysis_code_has_no_catbooks_labels() -> None:
    labels = catbooks_labels()
    found: list[str] = []
    for path in analysis_files():
        for line, value in string_constants(path):
            if (path.name, value) in GENERIC:
                continue
            hit = sorted(lab for lab in labels if lab in value)
            if hit:
                found.append(f"{path.relative_to(SRC)}:{line} {hit} in {value!r}")
    assert not found, "분석 코드에 캣북스 이름이 있음 (카탈로그로 옮길 것):\n" + "\n".join(found)


def test_generic_exceptions_are_still_used() -> None:
    """예외 목록에 남은 문자열이 실제로 코드에 있어야 한다 (지운 뒤 예외만 남지 않게)."""
    by_name = {p.name: p for p in analysis_files()}
    for name, value in GENERIC:
        assert value in {v for _, v in string_constants(by_name[name])}, (name, value)
