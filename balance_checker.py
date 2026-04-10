"""
balance_checker.py — 범용 밸런스 분석 엔진 v2
schema.json + CSV 기반으로 동작. 어떤 게임 데이터든 분석 가능.
"""

import json
import os
import math
import re
from pathlib import Path
from dataclasses import dataclass, field
from typing import Any

import pandas as pd


# ──────────────────────────────────────────────
# 데이터 클래스
# ──────────────────────────────────────────────

@dataclass
class Issue:
    severity: str       # CRITICAL / WARNING / INFO
    category: str
    stat: str
    source: str
    value: float
    message: str


@dataclass
class BalanceReport:
    game_name: str
    issues: list = field(default_factory=list)
    stat_contributions: dict = field(default_factory=dict)
    skill_dps_table: list = field(default_factory=list)
    raw_summaries: dict = field(default_factory=dict)

    @property
    def critical_count(self): return sum(1 for i in self.issues if i.severity == "CRITICAL")
    @property
    def warning_count(self):  return sum(1 for i in self.issues if i.severity == "WARNING")


# ──────────────────────────────────────────────
# 유틸
# ──────────────────────────────────────────────

def safe_div(a, b, default=0.0):
    return a / b if b else default


def eval_formula(formula: str, variables: dict) -> float:
    allowed = dict(variables)
    allowed["floor"] = math.floor
    try:
        return float(eval(formula, {"__builtins__": {}}, allowed))
    except Exception:
        return 0.0


def extract_multiplier(formula: str) -> float:
    m = re.search(r"\*\s*([\d.]+)", formula)
    return float(m.group(1)) if m else 1.0


def _accessory_values(level: int) -> tuple:
    """
    장신구 강화 레벨 → (bonus_atk_1부위, skill_bonus_1부위) 반환.

    설계 목표 (8부위 합산 기준):
      lv100  → 추가공격력 ~12%,  스킬데미지 ~10%
      lv250  → 추가공격력 ~22%,  스킬데미지 ~20%
      lv500  → 추가공격력 ~38%,  스킬데미지 ~35%
      lv750  → 추가공격력 ~45%,  스킬데미지 ~43%
      lv1000 → 추가공격력 ~51%,  스킬데미지 ~49%

    계수는 다른 소스(골드·유물·펫·장비)와 동일 단위(%)로 역산.
    단위: 1부위당 추가공격력(%), 스킬데미지(%)
    """
    if level <= 0:
        return 0.0, 0.0
    elif level <= 100:
        ba = level * 0.000827
        sb = level * 0.0000903
    elif level <= 250:
        ba = 0.08267 + (level - 100) * 0.000589
        sb = 0.009028 + (level - 100) * 0.0000752
    elif level <= 500:
        ba = 0.17099 + (level - 250) * 0.000802
        sb = 0.020313 + (level - 250) * 0.0000937
    elif level <= 750:
        ba = 0.37157 + (level - 500) * 0.000498
        sb = 0.043750 + (level - 500) * 0.0000702
    else:
        ba = 0.49602 + (level - 750) * 0.000540
        sb = 0.061294 + (level - 750) * 0.0000671
    return ba, sb


# ──────────────────────────────────────────────
# CSV 로더
# ──────────────────────────────────────────────

def load_csvs(base_dir: str, schema: dict) -> dict:
    dfs = {}
    for name, meta in schema.get("csv_files", {}).items():
        path = Path(base_dir) / meta.get("path", f"data/{name}.csv")
        if not path.exists():
            path = Path(base_dir) / "data" / f"{name}.csv"
        if path.exists():
            try:
                dfs[name] = pd.read_csv(path, encoding="utf-8-sig")
            except Exception as e:
                print(f"  [WARN] {name}.csv 로드 실패: {e}")
    return dfs


# ──────────────────────────────────────────────
# 기여도 계산
# ──────────────────────────────────────────────

def compute_max_contributions(schema: dict, dfs: dict) -> dict:
    stats_def = schema.get("stats", {})
    growth_sources = schema.get("growth_sources", {})
    contributions = {}

    short_map = {
        "레벨": "level", "골드": "gold", "유물": "relic",
        "펫": "pet", "장비": "equip", "스킬": "skill", "장신구": "acc"
    }

    for stat_name, stat_info in stats_def.items():
        sources_def = stat_info.get("sources", {})
        contrib = {}

        for src_name, src_info in sources_def.items():
            formula = src_info.get("formula", "")
            lookup = src_info.get("lookup")

            # lookup 테이블 기반
            if lookup:
                acc_max = growth_sources.get("장신구", {}).get("max", 1000)

                # CSV가 있으면 마지막 행 최대값 사용
                if lookup in dfs:
                    df_lookup = dfs[lookup]
                    numeric_cols = df_lookup.select_dtypes(include="number").columns
                    stat_cols = [c for c in numeric_cols
                                 if any(k in c.lower() for k in ("stat", "bonus", "max", "atk", "skill"))]
                    if stat_cols:
                        val = df_lookup[stat_cols].iloc[-1].max()
                        multiplier = extract_multiplier(formula)
                        contrib[src_name] = float(val) * multiplier
                        continue

                # CSV 없음 → _accessory_values 구간 함수 fallback
                ba, sb = _accessory_values(int(acc_max))
                if "skill" in formula.lower():
                    contrib[src_name] = sb * 8
                else:
                    contrib[src_name] = ba * 8
                continue

            # 공식 평가
            variables = {}
            for kr, en in short_map.items():
                variables[en] = growth_sources.get(kr, {}).get("max", 100)
            for gs_name, gs_info in growth_sources.items():
                vk = gs_name.replace("(","").replace(")","").replace(" ","_")
                variables[vk] = gs_info.get("max", 100)

            val = eval_formula(formula, variables)
            contrib[src_name] = abs(val)

        contributions[stat_name] = contrib

    return contributions


# ──────────────────────────────────────────────
# 검증 규칙들
# ──────────────────────────────────────────────

def check_dominance(contributions: dict, schema: dict) -> list:
    rules = schema.get("validation_rules", {})
    threshold_warn = rules.get("dominance_threshold", 10.0)
    threshold_crit = rules.get("critical_threshold", 20.0)
    issues = []

    for stat, sources in contributions.items():
        total = sum(sources.values())
        if total == 0:
            continue
        for src, val in sources.items():
            pct = safe_div(val, total) * 100
            if pct >= threshold_crit:
                issues.append(Issue(
                    severity="CRITICAL", category="source_dominance",
                    stat=stat, source=src, value=round(pct, 1),
                    message=f"[{stat}] '{src}' {pct:.1f}% 독점 → 다른 성장 요소 무의미화"
                ))
            elif pct >= threshold_warn:
                issues.append(Issue(
                    severity="WARNING", category="source_dominance",
                    stat=stat, source=src, value=round(pct, 1),
                    message=f"[{stat}] '{src}' {pct:.1f}% 집중 → 편중 주의"
                ))

    return issues


def check_progression(dfs: dict, schema: dict) -> list:
    issues = []

    if "stats" in dfs:
        df = dfs["stats"].copy()
        skip = {"grade", "level", "id"}
        stat_cols = [c for c in df.select_dtypes(include="number").columns if c not in skip]
        for col in stat_cols:
            vals = df[col].dropna().tolist()
            for i in range(1, len(vals)):
                if vals[i] < vals[i - 1]:
                    issues.append(Issue(
                        severity="WARNING", category="progression",
                        stat=col, source="stats.csv",
                        value=vals[i],
                        message=f"[stats/{col}] 행{i}: {vals[i-1]} → {vals[i]} 역전"
                    ))

    if "levels" in dfs:
        df = dfs["levels"].copy()
        if "exp_required" in df.columns:
            vals = df["exp_required"].tolist()
            for i in range(1, len(vals)):
                if vals[i] < vals[i - 1]:
                    issues.append(Issue(
                        severity="WARNING", category="progression",
                        stat="exp_required", source="levels.csv",
                        value=vals[i],
                        message=f"[levels] 레벨{i+1}: exp {vals[i-1]}→{vals[i]} 역전"
                    ))

    return issues


def check_skill_dps(dfs: dict, schema: dict) -> tuple:
    issues = []
    dps_table = []

    if "skills" not in dfs:
        return issues, dps_table

    df = dfs["skills"].copy()
    skill_types_def = schema.get("skill_damage_formula", {}).get("skill_types", {})

    for _, row in df.iterrows():
        try:
            st = str(int(row.get("skill_type", 1)))
            damage = float(row.get("damage", 0))
            hit_count = float(row.get("hit_count", 1))
            active_duration = float(row.get("active_duration", 0))
            hit_term = max(float(row.get("hit_term", 1)), 0.01)
            cooldown = max(float(row.get("cooldown", 1)), 0.01)
            skill_coef = float(row.get("skill_coef", 1))
            skill_dmg_bonus = float(row.get("skill_dmg_bonus", 1))

            if st == "1":   total_hit = damage
            elif st == "2": total_hit = damage * hit_count
            else:           total_hit = damage * math.floor(active_duration / hit_term)

            total_dmg = total_hit * skill_coef * skill_dmg_bonus
            dps = total_dmg / cooldown

            dps_table.append({
                "id": row.get("id", "?"),
                "name": row.get("name", f"Skill_{row.get('id','')}"),
                "tier": row.get("tier", "?"),
                "type": skill_types_def.get(st, {}).get("name", st),
                "total_dmg": round(total_dmg, 2),
                "dps": round(dps, 2),
            })
        except Exception:
            continue

    # 티어별 DPS 역전 검사
    if dps_table:
        df_dps = pd.DataFrame(dps_table)
        if "tier" in df_dps.columns:
            tier_avg = df_dps.groupby("tier")["dps"].mean().sort_index()
            vals = tier_avg.tolist()
            tiers = tier_avg.index.tolist()
            for i in range(1, len(vals)):
                if vals[i] < vals[i - 1] * 0.9:
                    issues.append(Issue(
                        severity="WARNING", category="skill_dps",
                        stat="dps", source=f"tier{tiers[i]}",
                        value=round(vals[i], 1),
                        message=f"[스킬] 티어{tiers[i-1]} DPS({vals[i-1]:.1f}) > 티어{tiers[i]} DPS({vals[i]:.1f}) 역전"
                    ))

    return issues, dps_table


def check_cross_source_ratio(contributions: dict, schema: dict) -> list:
    max_ratio = 5.0
    for gr in schema.get("global_rules", []):
        if gr.get("rule") == "cross_source_balance":
            max_ratio = gr.get("max_ratio", 5.0)

    issues = []
    for stat, sources in contributions.items():
        vals = [v for v in sources.values() if v > 0]
        if len(vals) < 2:
            continue
        ratio = max(vals) / min(vals)
        if ratio > max_ratio:
            min_src = min(sources, key=lambda k: sources[k])
            max_src = max(sources, key=lambda k: sources[k])
            issues.append(Issue(
                severity="WARNING", category="cross_source_ratio",
                stat=stat, source=f"{min_src}↔{max_src}",
                value=round(ratio, 1),
                message=f"[{stat}] 소스 간 기여 비율 {ratio:.1f}배 — '{max_src}' 압도적"
            ))
    return issues


# ──────────────────────────────────────────────
# 메인 분석
# ──────────────────────────────────────────────

def run_analysis(
    base_dir: str = ".",
    schema_path: str = None,
    schema_override: dict = None,   # 임계값 override용 — 파일 재로드 없이 dict 직접 주입
) -> BalanceReport:
    if schema_override is not None:
        schema = schema_override
    else:
        if schema_path is None:
            schema_path = os.path.join(base_dir, "schema.json")
        with open(schema_path, "r", encoding="utf-8") as f:
            schema = json.load(f)

    print(f"\n🎮 [{schema['game_name']}] 밸런스 분석 시작")
    print("=" * 60)

    dfs = load_csvs(base_dir, schema)
    print(f"  ✅ CSV 로드: {list(dfs.keys())}")

    report = BalanceReport(game_name=schema["game_name"])

    for name, df in dfs.items():
        report.raw_summaries[name] = {
            "rows": len(df),
            "columns": list(df.columns),
        }

    contributions = compute_max_contributions(schema, dfs)
    report.stat_contributions = contributions

    all_issues = []
    all_issues += check_dominance(contributions, schema)
    all_issues += check_progression(dfs, schema)
    all_issues += check_cross_source_ratio(contributions, schema)

    skill_issues, dps_table = check_skill_dps(dfs, schema)
    all_issues += skill_issues
    report.skill_dps_table = dps_table

    order = {"CRITICAL": 0, "WARNING": 1, "INFO": 2}
    report.issues = sorted(all_issues, key=lambda x: order.get(x.severity, 9))

    return report


def report_to_dict(report: BalanceReport) -> dict:
    return {
        "game_name": report.game_name,
        "summary": {
            "critical": report.critical_count,
            "warning": report.warning_count,
            "total_stats": len(report.stat_contributions),
        },
        "issues": [
            {
                "severity": i.severity,
                "category": i.category,
                "stat": i.stat,
                "source": i.source,
                "value": i.value,
                "message": i.message,
            }
            for i in report.issues
        ],
        "stat_contributions": {
            stat: {src: round(val, 4) for src, val in sources.items()}
            for stat, sources in report.stat_contributions.items()
        },
        "skill_dps": report.skill_dps_table,
        "csv_info": {
            name: {"rows": v["rows"], "columns": v["columns"]}
            for name, v in report.raw_summaries.items()
        },
    }


def print_report(report: BalanceReport):
    print(f"\n{'='*60}")
    print(f"  밸런스 리포트 — {report.game_name}")
    print(f"  CRITICAL: {report.critical_count}  WARNING: {report.warning_count}")
    print(f"{'='*60}")

    if not report.issues:
        print("  ✅ 이상 없음")
    else:
        for iss in report.issues:
            icon = "🔴" if iss.severity == "CRITICAL" else "🟡"
            print(f"  {icon} [{iss.severity}] {iss.message}")

    if report.skill_dps_table:
        print(f"\n  📊 스킬 DPS Top 10:")
        for s in sorted(report.skill_dps_table, key=lambda x: x["dps"], reverse=True)[:10]:
            print(f"    [{s['tier']}티어/{s['type']}] {s['name']}: DPS={s['dps']:.1f}")

    print(f"\n  📁 스탯 기여도:")
    for stat, sources in report.stat_contributions.items():
        total = sum(sources.values())
        if total == 0:
            continue
        parts = ", ".join(
            f"{s}: {safe_div(v,total)*100:.0f}%"
            for s, v in sorted(sources.items(), key=lambda x: -x[1])
        )
        print(f"    {stat}: {parts}")


if __name__ == "__main__":
    import sys
    base = sys.argv[1] if len(sys.argv) > 1 else "."
    rpt = run_analysis(base_dir=base)
    print_report(rpt)
