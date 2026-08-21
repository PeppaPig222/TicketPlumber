#!/usr/bin/env python
# -*- coding: utf-8 -*-
"""
反馈回灌评测集：把线上反馈（尤其「判错」的 case）转成评测用例，形成闭环。

用法：
    # 导出所有含修正标注的反馈到独立评测集
    python scripts/export_feedback_to_eval.py

    # 只导出判错的反馈
    python scripts/export_feedback_to_eval.py --incorrect-only

    # 直接合并进核心评测集（谨慎，会改原文件）
    python scripts/export_feedback_to_eval.py --append-to-dataset
"""
import argparse
import json
import sys
from pathlib import Path
from typing import Any, Dict, List

project_root = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(project_root))

from services.storage import SQLiteStore  # noqa: E402

DEFAULT_DB = project_root / "data" / "diagbot.db"
DEFAULT_DATASET = project_root / "data" / "evaluation" / "core_eval_set.json"
DEFAULT_OUTPUT = project_root / "data" / "evaluation" / "feedback_eval_set.json"


def load_feedback(store: SQLiteStore, incorrect_only: bool) -> List[Dict[str, Any]]:
    items = store.list_feedback(limit=10000)
    if incorrect_only:
        items = [i for i in items if not i.get("correct")]
    return items


def build_case(feedback: Dict[str, Any], store: SQLiteStore) -> Dict[str, Any] | None:
    """由一条反馈 + 原始 trace 构造评测用例；无法还原 query 时跳过。"""
    trace_id = feedback.get("trace_id", "")
    payload = store.get_trace(trace_id)
    if not payload:
        return None
    query = payload.get("query", "")
    if not query:
        return None

    diagnosis = payload.get("diagnosis", {}) or {}
    scenario = diagnosis.get("scenario", "") or "generic_ticket_diagnosis"
    expected_responsible = feedback.get("expected_responsible_party")
    expected_root_cause = feedback.get("expected_root_cause")

    # 缺修正标注的反馈无法构成可评测用例（只靠 comment 无法自动判定对错）
    if not expected_responsible and not expected_root_cause:
        return None

    return {
        "id": f"feedback_{trace_id}",
        "category": "feedback",
        "query": query,
        "expected_scenario": scenario,
        "expected_responsible_party": expected_responsible or "",
        "expected_root_cause_keywords": [expected_root_cause] if expected_root_cause else [],
        "expected_rounds": (diagnosis.get("trace") or {}).get("total_rounds"),
        "_source_trace_id": trace_id,
        "_comment": feedback.get("comment", ""),
    }


def main():
    parser = argparse.ArgumentParser(description="反馈回灌评测集")
    parser.add_argument("--db", type=Path, default=DEFAULT_DB, help="SQLite 数据库路径")
    parser.add_argument(
        "--incorrect-only", action="store_true", help="只导出判错的反馈"
    )
    parser.add_argument(
        "--output", type=Path, default=DEFAULT_OUTPUT, help="输出评测集路径"
    )
    parser.add_argument(
        "--dataset", type=Path, default=DEFAULT_DATASET, help="核心评测集路径"
    )
    parser.add_argument(
        "--append-to-dataset",
        action="store_true",
        help="合并进核心评测集（会直接修改原文件）",
    )
    args = parser.parse_args()

    if not args.db.exists():
        print(f"数据库不存在: {args.db}（可能还没有反馈数据）")
        sys.exit(1)

    store = SQLiteStore(db_path=str(args.db))
    feedback_items = load_feedback(store, args.incorrect_only)
    if not feedback_items:
        print("没有可导出的反馈记录。")
        return

    cases: List[Dict[str, Any]] = []
    skipped = 0
    for fb in feedback_items:
        case = build_case(fb, store)
        if case is None:
            skipped += 1
            continue
        cases.append(case)

    print(f"反馈总数: {len(feedback_items)}，可转评测用例: {len(cases)}，跳过: {skipped}")

    if not cases:
        return

    if args.append_to_dataset:
        target = args.dataset
        with open(target, "r", encoding="utf-8") as f:
            dataset = json.load(f)
        existing_ids = {c["id"] for c in dataset}
        added = [c for c in cases if c["id"] not in existing_ids]
        dataset.extend(added)
        with open(target, "w", encoding="utf-8") as f:
            json.dump(dataset, f, ensure_ascii=False, indent=2)
        print(f"已合并 {len(added)} 条到 {target}")
    else:
        args.output.parent.mkdir(parents=True, exist_ok=True)
        with open(args.output, "w", encoding="utf-8") as f:
            json.dump(cases, f, ensure_ascii=False, indent=2)
        print(f"已导出 {len(cases)} 条到 {args.output}")


if __name__ == "__main__":
    main()
