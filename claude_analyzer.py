"""
claude_analyzer.py — Claude API 연동 분석기
balance_checker 결과를 Claude에게 전달해 AI 분석 수행
"""

import json
import os
from pathlib import Path

import anthropic

from balance_checker import run_analysis, report_to_dict


MODEL = "claude-sonnet-4-6"
MAX_TOKENS = 4096


def _build_prompt(base_dir: str, user_context: str) -> str:
    schema_path = str(Path(base_dir) / "schema.json")

    try:
        report = run_analysis(base_dir=base_dir, schema_path=schema_path)
        data = report_to_dict(report)
    except Exception as e:
        return f"데이터 분석 중 오류 발생: {e}"

    parts = [
        f"게임: {data['game_name']}",
        f"\n## 정량 분석 결과",
        f"- CRITICAL 이슈: {data['summary']['critical']}개",
        f"- WARNING 이슈: {data['summary']['warning']}개",
        f"- 분석된 스탯 수: {data['summary']['total_stats']}개",
    ]

    if data["issues"]:
        parts.append("\n## 감지된 이슈")
        for issue in data["issues"]:
            parts.append(f"- [{issue['severity']}] {issue['message']}")

    if data["stat_contributions"]:
        parts.append("\n## 스탯별 기여도")
        for stat, sources in data["stat_contributions"].items():
            total = sum(sources.values())
            if total == 0:
                continue
            breakdown = ", ".join(
                f"{s}: {v / total * 100:.0f}%"
                for s, v in sorted(sources.items(), key=lambda x: -x[1])
            )
            parts.append(f"- {stat}: {breakdown}")

    if data["skill_dps"]:
        parts.append("\n## 스킬 DPS Top 10")
        for s in sorted(data["skill_dps"], key=lambda x: x["dps"], reverse=True)[:10]:
            parts.append(f"- [{s['tier']}티어/{s['type']}] {s['name']}: DPS={s['dps']:.1f}")

    if data["csv_info"]:
        parts.append("\n## CSV 파일 정보")
        for name, info in data["csv_info"].items():
            cols = ", ".join(info["columns"])
            parts.append(f"- {name}.csv: {info['rows']}행, 컬럼: {cols}")

    if user_context:
        parts.append(f"\n## 추가 컨텍스트\n{user_context}")

    parts.append(
        "\n위 게임 밸런스 데이터를 분석해주세요. "
        "주요 문제점, 원인, 개선 방향을 구체적으로 설명해주세요."
    )

    return "\n".join(parts)


def analyze_with_claude_stream(base_dir: str, user_context: str = ""):
    """
    Claude API 스트리밍 분석.
    SSE 포맷으로 yield: data: {"text": "..."}\n\n
    """
    client = anthropic.Anthropic(api_key=os.environ.get("ANTHROPIC_API_KEY"))
    prompt = _build_prompt(base_dir, user_context)

    try:
        with client.messages.stream(
            model=MODEL,
            max_tokens=MAX_TOKENS,
            system="당신은 게임 밸런스 전문 분석가입니다. 데이터를 기반으로 명확하고 실용적인 분석을 한국어로 제공합니다.",
            messages=[{"role": "user", "content": prompt}],
        ) as stream:
            for chunk in stream.text_stream:
                yield f"data: {json.dumps({'text': chunk}, ensure_ascii=False)}\n\n"

        yield "data: [DONE]\n\n"

    except Exception as e:
        yield f"data: {json.dumps({'error': str(e)}, ensure_ascii=False)}\n\n"


def analyze_with_claude_sync(
    base_dir: str,
    user_context: str = "",
    conversation_history: list = None,
) -> dict:
    """
    Claude API 동기 분석 (대화형 후속 질문용).
    반환: {"ai_response": str, "conversation": list, "usage": dict}
    """
    client = anthropic.Anthropic(api_key=os.environ.get("ANTHROPIC_API_KEY"))

    history = list(conversation_history or [])

    # 최초 분석이면 전체 프롬프트, 후속 질문이면 메시지만
    if not conversation_history:
        content = _build_prompt(base_dir, user_context)
    else:
        content = user_context

    history.append({"role": "user", "content": content})

    response = client.messages.create(
        model=MODEL,
        max_tokens=MAX_TOKENS,
        system="당신은 게임 밸런스 전문 분석가입니다. 데이터를 기반으로 명확하고 실용적인 분석을 한국어로 제공합니다.",
        messages=history,
    )

    ai_text = response.content[0].text
    history.append({"role": "assistant", "content": ai_text})

    return {
        "ai_response": ai_text,
        "conversation": history,
        "usage": {
            "input_tokens": response.usage.input_tokens,
            "output_tokens": response.usage.output_tokens,
        },
    }
