"""
game-balance-analyzer — 핵심 분석 엔진
Phase 2에서 Claude API 연동 및 범용화 예정

현재: 규칙 기반 로컬 분석 로직 (하드코딩 수치)
목표: CSV + schema.json + 계산 공식 자동 추출 기반 범용 분석
"""

import math
from dataclasses import dataclass
from typing import Dict, List

DOMINANCE_THRESHOLD = 70.0
OUTLIER_THRESHOLD   = 50.0

GROWTH_CONFIG = {
    "레벨":   {"max": 1000},
    "골드":   {"max": 3000},
    "유물":   {"max": 50},
    "펫":     {"max": 50},
    "장비":   {"max": 200},
    "스킬":   {"max": 100},
    "장신구": {"max": 1000},
}


def calc_contributions(p: float) -> Dict[str, Dict[str, float]]:
    lv  = round(p * GROWTH_CONFIG["레벨"]["max"])
    g   = round(p * GROWTH_CONFIG["골드"]["max"])
    r   = round(p * GROWTH_CONFIG["유물"]["max"])
    pet = round(p * GROWTH_CONFIG["펫"]["max"])
    eq  = round(p * GROWTH_CONFIG["장비"]["max"])
    sk  = round(p * GROWTH_CONFIG["스킬"]["max"])
    ac  = round(p * GROWTH_CONFIG["장신구"]["max"])

    def acc_val(lvl):
        if lvl < 100:   return lvl*1.25,  lvl*0.0125, lvl*0.005
        elif lvl < 300: return 125+(lvl-100)*2.0,  1.25+(lvl-100)*0.04,  0.5+(lvl-100)*0.0165
        elif lvl < 600: return 525+(lvl-300)*4.5,  9.25+(lvl-300)*0.105, 3.8+(lvl-300)*0.0425
        elif lvl < 800: return 1875+(lvl-600)*10.0, 40.75+(lvl-600)*0.25, 16.55+(lvl-600)*0.09
        else:           return 3875+(lvl-800)*22.5, 90.75+(lvl-800)*0.575, 34.55+(lvl-800)*0.185

    ab, aa, abonus = acc_val(ac)
    ab *= 8; aa *= 8; abonus *= 8

    return {
        "기본공격력":  {"레벨": lv*10,        "유물": r*50,    "펫": pet*30},
        "추가공격력":  {"골드": g*0.001,       "펫": pet*0.001, "무기(보유)": eq*0.002,
                        "무기(장착)": eq*0.003, "벨트(보유)": eq*0.002,
                        "장갑(보유)": eq*0.002, "장신구": aa},
        "치명타확률":  {"골드": g*0.0005,      "유물": r*0.002, "펫": pet*0.001, "벨트(장착)": eq*0.0005},
        "치명타데미지":{"골드": g*0.001,        "유물": r*0.005, "펫": pet*0.005, "신발(장착)": eq*0.001},
        "스킬데미지":  {"유물": r*0.005,        "장갑(장착)": eq*0.001,
                        "스킬강화": sk*0.002,   "장신구": abonus},
        "체력회복량":  {"골드": g*0.5,          "투구(장착)": eq*1.0},
        "마나회복량":  {"유물": r*1.0,          "펫": pet*0.5},
    }


@dataclass
class Issue:
    severity: str
    progress: int
    stat: str
    source: str
    share_pct: float
    message: str


def analyze(check_points=(25, 50, 75, 100)) -> List[Issue]:
    issues = []
    prev_shares = {}

    for pct in check_points:
        p = pct / 100
        contribs = calc_contributions(p)
        curr_shares = {}

        for stat, sources in contribs.items():
            total = sum(sources.values())
            if total == 0:
                continue
            curr_shares[stat] = {src: v/total*100 for src, v in sources.items()}

            for src, share in curr_shares[stat].items():
                if share >= DOMINANCE_THRESHOLD:
                    severity = "CRITICAL" if share >= 90 else "WARNING"
                    issues.append(Issue(severity, pct, stat, src, share,
                        f"{src}이(가) {share:.1f}% 독점 — 나머지 요소 사실상 무의미"))

        if prev_shares:
            for stat in curr_shares:
                if stat not in prev_shares:
                    continue
                for src in curr_shares[stat]:
                    if src not in prev_shares.get(stat, {}):
                        continue
                    delta = abs(curr_shares[stat][src] - prev_shares[stat][src])
                    if delta >= OUTLIER_THRESHOLD:
                        issues.append(Issue("WARNING", pct, stat, src,
                            curr_shares[stat][src],
                            f"비율 급변화 {delta:.1f}%p — 구간별 성장 불균형"))

        prev_shares = curr_shares

    return issues


def print_report():
    SEP = "═" * 60
    print(f"\n{SEP}")
    print("  game-balance-analyzer — 밸런스 검증 리포트")
    print(SEP)

    issues = analyze()

    for pct in (25, 50, 75, 100):
        contribs = calc_contributions(pct / 100)
        print(f"\n{'─'*60}  진행률 {pct}%")
        for stat, sources in contribs.items():
            total = sum(sources.values())
            if total == 0:
                continue
            print(f"\n  [{stat}]  합계: {total:.1f}")
            for src, val in sources.items():
                share = val/total*100
                bar = "█" * int(share/5)
                flag = " ◀ CRITICAL" if share>=90 else (" ◀ WARNING" if share>=DOMINANCE_THRESHOLD else "")
                print(f"    {src:<16} {val:>8.1f}  {share:>5.1f}%  {bar}{flag}")

    print(f"\n{SEP}")
    print(f"  이슈 요약 ({len(issues)}건)")
    print(SEP)

    seen = set()
    for i in sorted(issues, key=lambda x: x.severity):
        key = (i.stat, i.source, i.severity)
        if key not in seen:
            seen.add(key)
            icon = "🔴" if i.severity == "CRITICAL" else "🟡"
            print(f"  {icon} [{i.stat}] {i.message}")

    print(f"\n{SEP}\n")


if __name__ == "__main__":
    print_report()
