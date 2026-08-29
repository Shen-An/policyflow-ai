"""标定重排分数阈值：把「金标片段」和「非金标片段」的分数分布分开看。

背景：跑题门控现在用二元分词覆盖率（阈值 0.08，语义很弱）。要换成
「重排 top-1 分数低于 X 判跑题」，先得知道 X 取多少才不误杀。本脚本从
已跑过的 retrieval eval run 里把召回片段的分数捞出来，按「是不是金标」
分两堆，打印分布 + 阈值扫描表。

负样本（2026-08 起）：`retrieval_eval_items.relevance_judgement.negative=true`
的 query 本来就没有金标（语料里答不了），它们是「该被拦掉」的那一侧。
脚本会把它们从正样本统计里摘出去单独算拦住率，否则它们的 top-1 会被
误记成「非金标片段」，把正样本分布带偏。

注意：
1. 只读 DB，不重新调用重排 API，不动线上逻辑。
2. 分数来自哪一路（RRF 融合分 / cross-encoder 分 / 按排名合成的假分数）
   取决于该 run 的 retrieval_config，脚本会一并打印供判断。
3. retrieved_sources 的字段名不确定，先用 --inspect 看真实结构。

用法：
    python scripts/analyze_rerank_scores.py --inspect
    python scripts/analyze_rerank_scores.py
    python scripts/analyze_rerank_scores.py --run ces --run Momentum_5d
    python scripts/analyze_rerank_scores.py --threshold -8 --threshold -6
    python scripts/analyze_rerank_scores.py --max-false-reject 0.01
"""

from __future__ import annotations

import argparse
import json
import math
import sqlite3
import statistics
import sys
from pathlib import Path
from typing import Any

SCORE_KEYS = ("rerank_score", "score", "final_score", "fused_score", "similarity")
DOC_KEYS = ("document_id", "doc_id", "documentId")
CHUNK_KEYS = ("chunk_id", "chunkId", "id")


def _loads(raw: Any) -> Any:
    if raw is None or isinstance(raw, (dict, list)):
        return raw
    try:
        return json.loads(raw)
    except (TypeError, ValueError):
        return None


def _pick(data: dict[str, Any], *names: str) -> Any:
    for name in names:
        value = data.get(name)
        if value is not None:
            return value
    return None


def percentile(values: list[float], q: float) -> float:
    if not values:
        return float("nan")
    ordered = sorted(values)
    return ordered[max(0, math.ceil(q * len(ordered)) - 1)]


def describe(label: str, values: list[float]) -> None:
    if not values:
        print(f"    {label}: 无样本")
        return
    print(
        f"    {label}: n={len(values)} min={min(values):.4f} "
        f"p10={percentile(values, 0.10):.4f} p50={percentile(values, 0.50):.4f} "
        f"p90={percentile(values, 0.90):.4f} max={max(values):.4f} "
        f"mean={statistics.mean(values):.4f}"
    )


def inspect(conn: sqlite3.Connection) -> None:
    """先看清 retrieved_sources 到底长什么样，再谈解析。"""
    row = conn.execute(
        "SELECT eval_run_id, retrieved_sources, retrieval_metrics FROM eval_results "
        "WHERE retrieved_sources IS NOT NULL AND retrieved_sources != '' LIMIT 1"
    ).fetchone()
    if row is None:
        print("eval_results 里没有 retrieved_sources，先在评估中心跑一次检索评测。")
        return
    sources = _loads(row[1])
    print(f"样本来自 run={row[0]}")
    print(f"retrieved_sources 类型={type(sources).__name__} 条数={len(sources) if isinstance(sources, list) else 'N/A'}")
    if isinstance(sources, list) and sources and isinstance(sources[0], dict):
        print(f"单条字段={sorted(sources[0].keys())}")
        print("前两条原文：")
        print(json.dumps(sources[:2], ensure_ascii=False, indent=2)[:2000])
    print("\nretrieval_metrics 原文：")
    print(json.dumps(_loads(row[2]), ensure_ascii=False)[:800])


def load_gold(conn: sqlite3.Connection) -> dict[str, tuple[set[str], set[str]]]:
    gold: dict[str, tuple[set[str], set[str]]] = {}
    for item_id, docs, chunks in conn.execute(
        "SELECT id, relevant_document_ids, relevant_chunk_ids FROM retrieval_eval_items"
    ):
        gold[item_id] = (
            {str(x) for x in (_loads(docs) or [])},
            {str(x) for x in (_loads(chunks) or [])},
        )
    return gold


def load_negatives(conn: sqlite3.Connection) -> dict[str, str]:
    """item_id -> negative_kind（off_topic / near_miss）；正样本不在这个表里。"""
    negatives: dict[str, str] = {}
    for item_id, judgement_raw in conn.execute(
        "SELECT id, relevance_judgement FROM retrieval_eval_items"
    ):
        judgement = _loads(judgement_raw)
        if isinstance(judgement, dict) and judgement.get("negative"):
            negatives[item_id] = str(judgement.get("negative_kind") or "unknown")
    return negatives


def shipped_threshold() -> float | None:
    """线上现在实际用的分数阈值，避免脚本里再抄一份常量导致漂移。"""
    try:
        # 直接跑 scripts/xxx.py 时 sys.path[0] 是 scripts/，仓库根不在路径里。
        root = str(Path(__file__).resolve().parent.parent)
        if root not in sys.path:
            sys.path.insert(0, root)
        from backend.app.core.config import get_settings

        settings = get_settings()
        if not settings.RETRIEVAL_GATE_CROSS_ENCODER_ENABLED:
            return None
        return float(settings.RETRIEVAL_GATE_MIN_CROSS_ENCODER_SCORE)
    except Exception:  # 脚本要能脱离 app 依赖单跑
        return None


def threshold_grid(values: list[float], steps: int = 20) -> list[float]:
    """在实际观测到的分数区间上均匀取点——这些点可以直接填进配置。"""
    if not values:
        return []
    low, high = min(values), max(values)
    if high <= low:
        return [low]
    span = (high - low) / steps
    return [low + span * index for index in range(steps + 1)]


def print_sweep(
    title: str,
    thresholds: list[float],
    gold_top1: list[float],
    bad_top1: list[float],
    negative_top1: list[tuple[float, str]],
    marker: float | None = None,
) -> None:
    """一行一个候选阈值：左边是代价（误拒正样本），右边是收益（拦住负样本）。"""
    if not thresholds:
        return
    negative_scores = [score for score, _ in negative_top1]
    kinds = sorted({kind for _, kind in negative_top1})
    print(f"\n  {title}（top-1 分数 < 阈值 判跑题）：")
    header = "    阈值         误拒(金标top-1被卡)   负样本拦住"
    for kind in kinds:
        header += f"   {kind}"
    header += "   非金标top-1被卡"
    print(header)
    for threshold in thresholds:
        wrong = sum(1 for score in gold_top1 if score < threshold)
        blocked = sum(1 for score in negative_scores if score < threshold)
        suspect = sum(1 for score in bad_top1 if score < threshold)
        line = (
            f"    {threshold:<11.4f} {wrong:>4}/{len(gold_top1)} "
            f"({wrong / max(len(gold_top1), 1):>5.1%})    "
            f"{blocked:>3}/{len(negative_scores)} "
            f"({blocked / max(len(negative_scores), 1):>5.1%})"
        )
        for kind in kinds:
            of_kind = [score for score, item_kind in negative_top1 if item_kind == kind]
            hit = sum(1 for score in of_kind if score < threshold)
            line += f"  {hit:>3}/{len(of_kind)} ({hit / max(len(of_kind), 1):>5.1%})"
        line += (
            f"   {suspect:>4}/{len(bad_top1)} "
            f"({suspect / max(len(bad_top1), 1):>5.1%})"
        )
        if marker is not None and abs(threshold - marker) < 1e-9:
            line += "   ← 线上默认值"
        print(line)


def recommend(
    gold_top1: list[float],
    negative_top1: list[tuple[float, str]],
    max_false_reject: float,
) -> None:
    """在误拒率上限内挑最高的阈值——门控越高，拦住的跑题越多。"""
    if not gold_top1:
        print("\n  ⚠ 没有 top-1 命中金标的样本，标不出误拒率，无法给建议阈值。")
        return
    negative_scores = [score for score, _ in negative_top1]
    candidates = threshold_grid(gold_top1 + negative_scores, steps=200)
    allowed = int(len(gold_top1) * max_false_reject)
    best = None
    for threshold in candidates:
        if sum(1 for score in gold_top1 if score < threshold) <= allowed:
            best = threshold
    if best is None:
        print("\n  ⚠ 连最低阈值都会超出误拒上限，说明分数分布本身不可用。")
        return
    blocked = sum(1 for score in negative_scores if score < best)
    print(
        f"\n  建议：误拒率 ≤{max_false_reject:.1%}（允许 {allowed}/{len(gold_top1)} 条）时，"
        f"阈值可取 {best:.4f}"
    )
    if negative_scores:
        print(
            f"    该阈值能拦住 {blocked}/{len(negative_scores)} "
            f"({blocked / len(negative_scores):.1%}) 的负样本。"
        )
    else:
        print(
            "    ⚠ 这个 run 没有负样本，只标出了误拒下限，拦得住多少跑题还不知道。\n"
            "      先在评估中心 seed 企业负样本套件，再跑一次 cross_encoder 的 run。"
        )


def analyze_run(
    conn: sqlite3.Connection,
    run_row: tuple,
    gold: dict,
    negatives: dict[str, str],
    *,
    extra_thresholds: list[float],
    max_false_reject: float,
) -> None:
    run_id, name, cfg = run_row
    config = _loads(cfg) or {}
    rc = config.get("retrieval_config") or {}
    print(f"\n===== run: {name} ({run_id[:8]}) =====")
    print(
        f"  strategy={rc.get('strategy')} rerank_enabled={rc.get('rerank_enabled')} "
        f"reranker_method={rc.get('reranker_method')} query_mode={rc.get('query_mode')}"
    )
    if rc.get("reranker_method") != "cross_encoder" or not rc.get("rerank_enabled"):
        print(
            "  ⚠ 这个 run 不是 cross_encoder 重排，分数是 RRF 融合分或本地词面融合分，"
            "不能直接当语义阈值用（线上门控只对 cross_encoder 分数生效）。"
        )

    gold_scores: list[float] = []
    other_scores: list[float] = []
    top1: list[tuple[float, bool]] = []      # (top-1 分数, top-1 是否金标)
    negative_top1: list[tuple[float, str]] = []   # 负样本的 top-1 分数 + 类型
    negative_passages = 0
    per_query_any_gold = 0
    per_query_no_gold: list[float] = []      # 完全没命中金标的 query 的 top-1 分数
    synthetic = 0
    passages = 0
    queries = 0
    score_fields: dict[str, int] = {}

    for item_id, sources_raw in conn.execute(
        "SELECT retrieval_eval_item_id, retrieved_sources FROM eval_results WHERE eval_run_id = ?",
        (run_id,),
    ):
        sources = _loads(sources_raw)
        if not isinstance(sources, list) or not sources:
            continue
        negative_kind = negatives.get(item_id)
        gold_docs, gold_chunks = gold.get(item_id, (set(), set()))
        ranked: list[tuple[int, float, bool]] = []
        for index, src in enumerate(sources, start=1):
            if not isinstance(src, dict):
                continue
            score_key = next((key for key in SCORE_KEYS if src.get(key) is not None), None)
            if score_key is None:
                continue
            try:
                score = float(src[score_key])
            except (TypeError, ValueError):
                continue
            score_fields[score_key] = score_fields.get(score_key, 0) + 1
            doc = str(_pick(src, *DOC_KEYS) or "")
            chunk = str(_pick(src, *CHUNK_KEYS) or "")
            meta = _loads(_pick(src, "metadata")) or {}
            # score_is_synthetic 只描述融合分 score（rank_decay），不描述 rerank_score。
            if score_key == "score" and isinstance(meta, dict) and meta.get("score_is_synthetic"):
                synthetic += 1
            is_gold = (doc and doc in gold_docs) or (chunk and chunk in gold_chunks)
            rank = _pick(src, "rank")
            ranked.append((int(rank) if isinstance(rank, int) else index, score, is_gold))
            if negative_kind is not None:
                # 负样本没有金标，混进「非金标片段」会把正样本分布带偏。
                negative_passages += 1
                continue
            passages += 1
            (gold_scores if is_gold else other_scores).append(score)
        if not ranked:
            continue
        ranked.sort(key=lambda triple: triple[0])
        if negative_kind is not None:
            negative_top1.append((ranked[0][1], negative_kind))
            continue
        queries += 1
        top1.append((ranked[0][1], ranked[0][2]))
        if any(is_gold for _, _, is_gold in ranked):
            per_query_any_gold += 1
        else:
            per_query_no_gold.append(ranked[0][1])

    if not passages and not negative_passages:
        print("  没解析出分数——先跑 --inspect 确认字段名，再补进 SCORE_KEYS。")
        return

    print(f"\n  片段级分布（正样本 n={passages}，分数字段={score_fields}）：")
    describe("金标片段", gold_scores)
    describe("非金标片段", other_scores)
    if synthetic:
        print(f"    ⚠ {synthetic}/{passages} 个片段用的是 score 且 score_is_synthetic=true（rank_decay 假分数，阈值对它没意义）")

    gold_top1 = [s for s, is_gold in top1 if is_gold]
    bad_top1 = [s for s, is_gold in top1 if not is_gold]
    print(f"\n  query 级 top-1（门控真正看的东西，正样本 n={len(top1)}）：")
    describe("top-1 命中金标", gold_top1)
    describe("top-1 未命中金标", bad_top1)
    print(
        f"    返回结果里出现过金标的 query={per_query_any_gold}/{queries}；"
        f"完全没命中的={len(per_query_no_gold)}"
    )

    if negative_top1:
        print(f"\n  负样本 top-1（该被拦掉的那一侧，n={len(negative_top1)}）：")
        describe("全部负样本", [score for score, _ in negative_top1])
        for kind in sorted({kind for _, kind in negative_top1}):
            describe(kind, [score for score, item_kind in negative_top1 if item_kind == kind])
    else:
        print(
            "\n  ⚠ 这个 run 里没有负样本，只有「该放过」的一侧，标不出该拦掉的分数上限。\n"
            "    先 seed 企业负样本套件（20 off_topic + 20 near_miss），再把它们一起选进 run。"
        )

    pool = gold_top1 + bad_top1 + [score for score, _ in negative_top1]
    if pool:
        marker = shipped_threshold()
        print_sweep(
            "分位数扫描",
            [percentile(pool, step / 20) for step in range(1, 20)],
            gold_top1,
            bad_top1,
            negative_top1,
        )
        candidates = sorted(
            {round(value, 4) for value in extra_thresholds}
            | ({round(marker, 4)} if marker is not None else set())
        )
        print_sweep(
            "候选阈值（线上默认值 + --threshold）",
            candidates,
            gold_top1,
            bad_top1,
            negative_top1,
            marker=round(marker, 4) if marker is not None else None,
        )
        recommend(gold_top1, negative_top1, max_false_reject)
        print("    → 选点原则：先卡住误拒率（比如 ≤2%），再看能不能拦住负样本。")


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("db", nargs="?", default="policyflow.db")
    parser.add_argument("--inspect", action="store_true", help="只打印 retrieved_sources 结构")
    parser.add_argument("--run", action="append", default=[], help="按 run 名筛选，可多次")
    parser.add_argument(
        "--threshold",
        action="append",
        type=float,
        default=[],
        help="额外评估的候选阈值，可多次（线上默认值会自动带上）",
    )
    parser.add_argument(
        "--max-false-reject",
        type=float,
        default=0.02,
        help="建议阈值时允许的误拒率上限，默认 0.02",
    )
    args = parser.parse_args()

    conn = sqlite3.connect(args.db)
    if args.inspect:
        inspect(conn)
        return

    gold = load_gold(conn)
    negatives = load_negatives(conn)
    rows = conn.execute(
        "SELECT id, name, config_snapshot FROM eval_runs ORDER BY created_at"
    ).fetchall()
    if args.run:
        rows = [row for row in rows if row[1] in set(args.run)]
    if not rows:
        print("没有匹配的 run。")
        return
    print(f"数据集里的负样本条目：{len(negatives)}")
    for row in rows:
        analyze_run(
            conn,
            row,
            gold,
            negatives,
            extra_thresholds=args.threshold,
            max_false_reject=args.max_false_reject,
        )


if __name__ == "__main__":
    main()


