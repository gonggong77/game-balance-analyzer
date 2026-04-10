"""
multi_agent_pipeline.py — 멀티 에이전트 밸런스 분석 파이프라인

흐름:
  Phase 1 (병렬): Agent 1 (CSV 분석가) + Agent 2 (스크립트 분석가)
  Phase 2       : Agent 3 (리서처 — 웹서치)
  Phase 3       : Agent 4 (시니어 밸런스 기획자 — 종합 보고서)
"""

import json
import os
import re
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path
from typing import Callable, Optional

import anthropic
import requests as _req

from balance_checker import run_analysis, report_to_dict

MODEL = "claude-sonnet-4-6"
MAX_TOKENS = 8192        # Agent 1·2·3 기본 출력 한도
MAX_TOKENS_FINAL = 16000  # Agent 4 최종 보고서 — extended output beta 활용


# ── 공통 헬퍼 ────────────────────────────────────────────────

def _client() -> anthropic.Anthropic:
    return anthropic.Anthropic(api_key=os.environ.get("ANTHROPIC_API_KEY"))


def _web_search(query: str) -> str:
    """Tavily API 웹 검색. API 키 없으면 Claude 내부 지식으로 대체."""
    key = os.environ.get("TAVILY_API_KEY", "")
    if not key:
        return (
            f'(TAVILY_API_KEY 미설정 — 검색어 "{query}"에 대해 '
            "Claude 내부 지식을 활용합니다.)"
        )
    try:
        r = _req.post(
            "https://api.tavily.com/search",
            json={
                "api_key": key,
                "query": query,
                "max_results": 5,
                "include_answer": True,
            },
            timeout=15,
        )
        r.raise_for_status()
        data = r.json()
        parts = []
        if data.get("answer"):
            parts.append(f"핵심 요약: {data['answer']}")
        for item in data.get("results", [])[:5]:
            snippet = (item.get("content") or "")[:400]
            parts.append(f"**{item['title']}**\n{snippet}\n출처: {item['url']}")
        return "\n\n".join(parts) or "(검색 결과 없음)"
    except Exception as e:
        return f"(검색 오류: {e})"


# ── Agent 1: CSV 분석가 ──────────────────────────────────────

def run_csv_analyst(base_dir: str, user_context: str = "") -> str:
    """업로드된 CSV 데이터 구조 및 수치를 분석한다."""
    schema_path = str(Path(base_dir) / "schema.json")
    try:
        report = run_analysis(base_dir=base_dir, schema_path=schema_path)
        data = report_to_dict(report)
    except Exception as e:
        return f"[CSV 분석 실패]: {e}"

    parts = [
        f"게임: {data['game_name']}",
        "\n### 정량 분석 요약",
        f"- CRITICAL 이슈: {data['summary']['critical']}개",
        f"- WARNING 이슈: {data['summary']['warning']}개",
        f"- 분석 스탯 수: {data['summary']['total_stats']}개",
    ]
    if data["issues"]:
        parts.append("\n### 감지된 이슈")
        for iss in data["issues"]:
            parts.append(f"- [{iss['severity']}] {iss['message']}")
    if data["stat_contributions"]:
        parts.append("\n### 스탯별 기여도")
        for stat, sources in data["stat_contributions"].items():
            total = sum(sources.values())
            if total == 0:
                continue
            bd = ", ".join(
                f"{s}: {v / total * 100:.0f}%"
                for s, v in sorted(sources.items(), key=lambda x: -x[1])
            )
            parts.append(f"- {stat}: {bd}")
    if data["skill_dps"]:
        parts.append("\n### 스킬 DPS Top 10")
        for s in sorted(data["skill_dps"], key=lambda x: x["dps"], reverse=True)[:10]:
            parts.append(
                f"- [{s['tier']}티어/{s['type']}] {s['name']}: DPS={s['dps']:.1f}"
            )
    if data["csv_info"]:
        parts.append("\n### CSV 파일 정보")
        for name, info in data["csv_info"].items():
            cols = ", ".join(info["columns"])
            parts.append(f"- {name}.csv: {info['rows']}행, 컬럼: {cols}")
    if user_context:
        parts.append(f"\n### 추가 컨텍스트\n{user_context}")

    prompt = "\n".join(parts)
    resp = _client().messages.create(
        model=MODEL,
        max_tokens=MAX_TOKENS,
        system=(
            "당신은 게임 데이터 분석 전문가입니다. "
            "CSV 수치와 구조를 분석하여 밸런스 관점의 핵심 발견사항을 "
            "한국어 마크다운으로 체계적으로 정리하세요."
        ),
        messages=[{"role": "user", "content": f"다음 게임 데이터를 분석하세요:\n\n{prompt}"}],
    )
    return resp.content[0].text


# ── Agent 2: 스크립트 분석가 ─────────────────────────────────

def run_script_analyst(base_dir: str, user_context: str = "") -> str:
    """업로드된 C# 스크립트에서 밸런스 관련 로직을 분석한다."""
    scripts = []
    for p in Path(base_dir).rglob("*.cs"):
        try:
            size_kb = p.stat().st_size // 1024
            if size_kb > 60:
                scripts.append(f"## {p.name}\n> 파일 크기 초과 ({size_kb}KB — 60KB 제한)\n")
            else:
                content = p.read_text(encoding="utf-8", errors="ignore")
                scripts.append(f"## {p.name}\n```csharp\n{content}\n```")
        except Exception as e:
            scripts.append(f"## {p.name}\n> 읽기 오류: {e}\n")

    if not scripts:
        return (
            "**C# 스크립트 없음**\n\n"
            "업로드된 파일 중 `.cs` 파일이 발견되지 않았습니다. "
            "스크립트를 함께 업로드하면 데미지 계산식·스케일링 공식 등 "
            "코드 레벨 밸런스 분석이 추가됩니다."
        )

    block = "\n\n---\n\n".join(scripts[:10])  # 최대 10개
    resp = _client().messages.create(
        model=MODEL,
        max_tokens=MAX_TOKENS,
        system=(
            "당신은 게임 프로그래밍 및 밸런스 설계 전문가입니다. "
            "C# 스크립트에서 데미지 계산식, 스탯 수식, 레벨 스케일링, "
            "쿨다운·자원 시스템 등 밸런스 관련 로직을 분석합니다. "
            "한국어 마크다운으로 작성하세요."
        ),
        messages=[{
            "role": "user",
            "content": (
                "다음 C# 스크립트를 분석하여 밸런스 관련 로직과 "
                f"잠재적 문제점을 정리하세요:\n\n{block}"
            ),
        }],
    )
    return resp.content[0].text


# ── Agent 3: 리서처 ──────────────────────────────────────────

_SEARCH_TOOL = [{
    "name": "web_search",
    "description": (
        "게임 밸런스 설계 가이드라인, 업계 베스트 프랙티스, "
        "학술·개발자 블로그 사례를 웹에서 검색합니다."
    ),
    "input_schema": {
        "type": "object",
        "properties": {
            "query": {"type": "string", "description": "검색어 (한국어 또는 영어)"}
        },
        "required": ["query"],
    },
}]


def run_researcher(game_name: str, csv_summary: str) -> str:
    """웹서치로 게임 밸런스 가이드라인 및 업계 기준을 수집한다."""
    hint = re.sub(r"\s+", " ", re.sub(r"[^\w\s가-힣]", " ", csv_summary))[:400]
    messages = [{
        "role": "user",
        "content": (
            f"게임 '{game_name}' 밸런스 검토를 위한 업계 가이드라인을 조사해주세요.\n\n"
            f"현재 주요 이슈:\n{hint}\n\n"
            "다음 내용을 검색하여 한국어로 체계적으로 정리해주세요:\n"
            "1. 게임 밸런스 설계 원칙 및 프레임워크\n"
            "2. RPG·액션게임 스탯 스케일링 가이드라인\n"
            "3. 업계에서 검증된 DPS·수치 범위 기준\n"
            "4. 유사 장르 게임의 밸런스 패치 사례"
        ),
    }]

    client = _client()
    resp = None
    for _ in range(4):  # 최대 4회 tool-use 루프
        resp = client.messages.create(
            model=MODEL,
            max_tokens=MAX_TOKENS,
            tools=_SEARCH_TOOL,
            messages=messages,
        )
        if resp.stop_reason != "tool_use":
            break
        messages.append({"role": "assistant", "content": resp.content})
        tool_results = [
            {
                "type": "tool_result",
                "tool_use_id": b.id,
                "content": _web_search(b.input["query"]),
            }
            for b in resp.content
            if b.type == "tool_use" and b.name == "web_search"
        ]
        messages.append({"role": "user", "content": tool_results})

    if resp is None:
        return "(리서치 실패)"
    return next(
        (b.text for b in resp.content if hasattr(b, "text")),
        "(결과 없음)",
    )


# ── Agent 4: 시니어 밸런스 기획자 ───────────────────────────

def run_senior_designer(
    csv_result: str,
    script_result: str,
    guidelines: str,
    user_context: str = "",
) -> str:
    """Agent 1·2·3 결과를 종합하여 최종 밸런스 보고서를 작성한다."""
    sections = [
        "## [Agent 1 — CSV 분석 결과]\n" + csv_result,
        "## [Agent 2 — 스크립트 분석 결과]\n" + script_result,
        "## [Agent 3 — 업계 가이드라인 리서치]\n" + guidelines,
    ]
    if user_context:
        sections.append("## [추가 컨텍스트]\n" + user_context)
    sections.append(
        "## 요청\n"
        "위 세 에이전트의 분석을 종합하여 다음 구조의 최종 밸런스 보고서를 작성하세요. "
        "**한국어 마크다운, 테이블과 수치를 적극 활용**하세요.\n\n"
        "1. **종합 밸런스 평가** — 현재 게임 밸런스 전체 상태 총평\n"
        "2. **핵심 문제점** — CRITICAL / WARNING 분류, 우선순위 순\n"
        "3. **업계 기준 비교** — 가이드라인 대비 현재 수치 비교 테이블\n"
        "4. **개선 방향 및 액션 아이템** — 구체적 수치 제안 포함\n"
        "5. **단기 / 장기 로드맵** — 우선순위 기반 실행 계획"
    )

    # extended output beta로 최대 16 K 토큰까지 출력 허용
    # (미지원 모델에서는 일반 max_tokens=8192로 자동 폴백)
    try:
        resp = _client().messages.create(
            model=MODEL,
            max_tokens=MAX_TOKENS_FINAL,
            betas=["output-128k-2025-02-19"],
            system=(
                "당신은 10년 경력의 시니어 게임 밸런스 기획자입니다. "
                "여러 에이전트의 분석을 통합하여 데이터 기반의 명확하고 "
                "실행 가능한 밸런스 보고서를 작성합니다."
            ),
            messages=[{"role": "user", "content": "\n\n---\n\n".join(sections)}],
        )
    except Exception:
        resp = _client().messages.create(
            model=MODEL,
            max_tokens=MAX_TOKENS,
            system=(
                "당신은 10년 경력의 시니어 게임 밸런스 기획자입니다. "
                "여러 에이전트의 분석을 통합하여 데이터 기반의 명확하고 "
                "실행 가능한 밸런스 보고서를 작성합니다."
            ),
            messages=[{"role": "user", "content": "\n\n---\n\n".join(sections)}],
        )
    return resp.content[0].text


# ── 파이프라인 오케스트레이터 ────────────────────────────────

def run_pipeline(
    base_dir: str,
    user_context: str = "",
    on_progress: Optional[Callable[[str, str], None]] = None,
) -> dict:
    """
    멀티 에이전트 파이프라인을 실행한다.

    on_progress(agent_key, status)
      agent_key : "agent1" | "agent2" | "agent3" | "agent4"
      status    : "running" | "done" | "error"

    반환값: {"report": str, "steps": {"agent1": ..., ..., "agent4": ...}}
    """
    def emit(key: str, status: str) -> None:
        if on_progress:
            on_progress(key, status)

    steps: dict = {}

    # ── Phase 1: Agent 1 + Agent 2 병렬 ──────────────────────
    emit("agent1", "running")
    emit("agent2", "running")
    with ThreadPoolExecutor(max_workers=2) as ex:
        f1 = ex.submit(run_csv_analyst, base_dir, user_context)
        f2 = ex.submit(run_script_analyst, base_dir, user_context)
        for key, fut in [("agent1", f1), ("agent2", f2)]:
            try:
                steps[key] = fut.result()
                emit(key, "done")
            except Exception as e:
                steps[key] = f"[오류: {e}]"
                emit(key, "error")

    # ── Phase 2: Agent 3 리서처 ───────────────────────────────
    emit("agent3", "running")
    try:
        try:
            with open(Path(base_dir) / "schema.json", encoding="utf-8") as f:
                game_name = json.load(f).get("game_name", "Unknown Game")
        except Exception:
            game_name = "Unknown Game"
        steps["agent3"] = run_researcher(game_name, steps.get("agent1", ""))
        emit("agent3", "done")
    except Exception as e:
        steps["agent3"] = f"[오류: {e}]"
        emit("agent3", "error")

    # ── Phase 3: Agent 4 시니어 기획자 ───────────────────────
    emit("agent4", "running")
    try:
        steps["agent4"] = run_senior_designer(
            steps.get("agent1", ""),
            steps.get("agent2", ""),
            steps.get("agent3", ""),
            user_context,
        )
        emit("agent4", "done")
    except Exception as e:
        steps["agent4"] = f"[오류: {e}]"
        emit("agent4", "error")

    return {"report": steps.get("agent4", ""), "steps": steps}
