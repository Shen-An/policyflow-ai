"""统计每次回答的工具调用分布（面试用真实数据，替代拍脑袋的「平均 5-6 次」）。

数据来源有两处，本脚本都读：
1. `tool_call_logs` 表 —— 只有注册进 DB `tools` 表的工具才会落库；
   Answer Agent 用的 `kb.search` / `skill.run` 不在 DB 注册表里，所以这张表**没有**聊天轮次。
2. `messages.meta_json -> diagnostics.tools` —— Answer Agent 的真实 tool_trace
   （由 `chat_service._collect_tool_traces` 兜底写入），这才是可用的样本。

用法：python scripts/analyze_tool_calls.py [db_path]
"""

from __future__ import annotations

import collections
import json
import math
import sqlite3
import statistics
import sys
from typing import Any


def percentile(values: list[int], q: float) -> int:
    ordered = sorted(values)
    return ordered[max(0, math.ceil(q * len(ordered)) - 1)]


def main(db_path: str = "policyflow.db") -> None:
    conn = sqlite3.connect(db_path)

    total_logs = conn.execute("SELECT COUNT(*) FROM tool_call_logs").fetchone()[0]
    agent_logs = conn.execute(
        "SELECT COUNT(*) FROM tool_call_logs WHERE agent_name = 'AnswerAgent'"
    ).fetchone()[0]
    print(f"[tool_call_logs] 总行数={total_logs} 其中 AnswerAgent={agent_logs}")
    if agent_logs == 0:
        print("  → 这张表里没有聊天轮次，改用 messages.diagnostics.tools\n")

    rows = conn.execute(
        "SELECT meta_json, created_at FROM messages WHERE role='assistant' ORDER BY created_at"
    ).fetchall()

    counts: list[int] = []
    tools: collections.Counter[str] = collections.Counter()
    statuses: collections.Counter[str] = collections.Counter()
    budgets: list[dict[str, Any]] = []
    no_diagnostics = 0
    failures: list[tuple[str, str]] = []

    for meta_json, created_at in rows:
        meta = json.loads(meta_json) if meta_json else {}
        diagnostics = meta.get("diagnostics")
        if not isinstance(diagnostics, dict):
            no_diagnostics += 1
            continue
        trace = [t for t in (diagnostics.get("tools") or []) if isinstance(t, dict)]
        counts.append(len(trace))
        for call in trace:
            tools[str(call.get("tool_name"))] += 1
            statuses[str(call.get("status"))] += 1
            if call.get("status") != "success":
                failures.append((created_at[:19], str(call.get("error_message"))))
        budget = diagnostics.get("budget")
        if isinstance(budget, dict):
            budgets.append(budget)

    print(f"assistant 消息={len(rows)} 无 diagnostics（旧 schema）={no_diagnostics} 可统计={len(counts)}")
    if not counts:
        return

    print(f"每轮工具调用次数分布 = {sorted(collections.Counter(counts).items())}")
    print(
        f"mean={statistics.mean(counts):.2f} median={statistics.median(counts)} "
        f"p95={percentile(counts, 0.95)} max={max(counts)}"
    )
    nonzero = [c for c in counts if c > 0]
    print(f"用到工具的轮次={len(nonzero)}/{len(counts)} ({len(nonzero) / len(counts):.0%})")
    if nonzero:
        print(f"仅统计用到工具的轮次：mean={statistics.mean(nonzero):.2f} max={max(nonzero)}")
    print(f"工具分布={tools.most_common()} 状态={statuses.most_common()}")
    print(f"撞到 8 次上限的轮次={sum(1 for c in counts if c >= 8)}")

    print("\n失败调用：")
    for ts, err in failures:
        print(f"  {ts}  {err}")

    print("\n预算快照（有 budget 字段的轮次）：")
    for budget in budgets:
        print(
            "  llm={llm_calls}/{max_llm_calls} retrieval={retrieval_attempts}/"
            "{max_retrieval_attempts} tool={tool_calls}/{max_tool_calls} "
            "elapsed={elapsed_seconds}s/{max_total_seconds}s".format(**budget)
        )


if __name__ == "__main__":
    main(sys.argv[1] if len(sys.argv) > 1 else "policyflow.db")
