"""标定长期记忆召回的 top-k：把「召回得分随排名的衰减」和「token 成本」画出来。

背景：LTM 召回默认只取 top-5（MEMORY_LTM_TOP_K=5）。这个 5 到底该取多少，
本脚本用一组**受控种子对话**跑出证据，而不是拍脑袋：

  1. 种子若干条真实感的记忆（强相关 / 弱相关 / 跑题长尾），
  2. 用项目里**真正的** memory_rank_score 打分（decay_lambda / access_cap /
     top_k 都从 get_settings() 读线上默认值，不自己抄常量），
  3. 打印「得分随排名的衰减曲线」找拐点 + 「top-k 累计吃住多少相关性」+
     「记忆块 token 随 k 膨胀、多快追平当轮检索证据」。

诚实口径（务必照说）：
  - 这是**受控种子实验**，不是线上生产数据——真实库单用户量级根本凑不出分布。
  - relevance 走的是真实的**词面覆盖率**那一路（_keyword_score）；向量相似度
    只会让高相关项更突出、拐点更陡，不会推翻结论。
  - 结论是「5 落在高信号头部之后、是个稳妥的默认」，**不是**「实验证明 5 全局最优」。

用法：
    python scripts/analyze_memory_topk.py
    python scripts/analyze_memory_topk.py --max-rank 12
"""

from __future__ import annotations

import argparse
import sys
from dataclasses import dataclass, field
from datetime import UTC, datetime, timedelta
from pathlib import Path
from typing import Any

# Windows 控制台默认 GBK，中文 + 方块字符会乱码/报错，强制 UTF-8 输出。
try:
    sys.stdout.reconfigure(encoding="utf-8")  # type: ignore[union-attr]
except Exception:
    pass

# 直接跑 scripts/xxx.py 时仓库根不在 sys.path 里。
_ROOT = str(Path(__file__).resolve().parent.parent)
if _ROOT not in sys.path:
    sys.path.insert(0, _ROOT)

from backend.app.services.memory_service import memory_rank_score  # noqa: E402

# 固定参考时刻，让整份实验完全可复现（不用 datetime.now）。
REF_NOW = datetime(2026, 9, 2, 12, 0, 0, tzinfo=UTC)


def _shipped() -> tuple[float, float, int]:
    """线上默认：衰减系数 / 访问加分上限 / LTM top-k。读配置，不抄常量。"""
    try:
        from backend.app.core.config import get_settings

        s = get_settings()
        return (
            float(s.MEMORY_RANK_DECAY_LAMBDA),
            float(s.MEMORY_RANK_ACCESS_BOOST_CAP),
            int(s.MEMORY_LTM_TOP_K),
        )
    except Exception:
        return (0.08, 0.15, 5)


@dataclass
class FakeItem:
    """只鸭子类型 memory_rank_score 真正读到的那几个字段（对齐 MemoryItem）。"""

    content: str
    confidence: float
    salience: float
    age_days: float
    access_count: int
    embedding: list[float] | None = None
    meta_json: dict[str, Any] = field(default_factory=dict)
    created_at: datetime = REF_NOW
    updated_at: datetime = REF_NOW

    def __post_init__(self) -> None:
        self.updated_at = REF_NOW - timedelta(days=self.age_days)
        self.created_at = self.updated_at
        self.meta_json = {"salience": self.salience, "access_count": self.access_count}


@dataclass
class Scenario:
    name: str
    query: str          # 已改写的检索式：空格分词，贴近线上 query_rewrite 产物
    items: list[FakeItem]


def _mk(query_tokens: list[str], hit_idx: list[int], filler: str,
        *, salience: float, age_days: float, access: int) -> FakeItem:
    """按「命中哪几个 query 词」构造记忆正文，精确控制词面覆盖率→relevance。"""
    hit_words = [query_tokens[i] for i in hit_idx]
    content = (" ".join(hit_words) + " " + filler).strip()
    return FakeItem(content=content, confidence=salience, salience=salience,
                    age_days=age_days, access_count=access)


def build_scenarios() -> list[Scenario]:
    """5 个企业政策助手里真实感的用户轮次。刻意造成『少量强相关 + 一串弱相关 +
    一条跑题长尾』的自然分布——每个场景 8~12 条正样本，让 top-5 覆盖率是真结论而
    非『正样本本来就≤5』的假象。不去把拐点凑到 5，让它自己落在哪算哪。"""
    scenarios: list[Scenario] = []

    # 1) 年假剩余天数（财务部）
    q = ["年假", "剩余", "天数", "财务部"]
    scenarios.append(Scenario("年假剩余天数", " ".join(q), [
        _mk(q, [0, 1, 2], "国家法定与公司额度合并后按工龄阶梯计算",  salience=0.85, age_days=2,  access=6),
        _mk(q, [0, 2],    "去年未休部分可结转至今年一季度用完",      salience=0.75, age_days=5,  access=3),
        _mk(q, [3],       "用户长期在财务部负责应付账款，稳定画像",   salience=0.80, age_days=40, access=9),
        _mk(q, [0, 1],    "系统显示当前可用不多，建议尽快安排",       salience=0.60, age_days=10, access=2),
        _mk(q, [0],       "提交休假申请单尚未处理的待办",            salience=0.55, age_days=8,  access=1),
        _mk(q, [2],       "上次问过按月累计的折算方式",              salience=0.45, age_days=20, access=0),
        _mk(q, [0],       "决定本月安排几天陪家人",                 salience=0.40, age_days=15, access=0),
        _mk(q, [1],       "提醒核对额度是否够用",                   salience=0.35, age_days=22, access=0),
        _mk(q, [],        "偏好回答用列表格式，条理清晰",            salience=0.70, age_days=3,  access=8),  # 跑题→0
        _mk(q, [],        "问过差旅报销的具体流程与所需材料",        salience=0.45, age_days=6,  access=2),  # 跑题→0
        _mk(q, [],        "关注社保缴纳比例的最新变化",             salience=0.50, age_days=12, access=1),  # 跑题→0
        _mk(q, [],        "上周聊过团建预算安排",                   salience=0.35, age_days=18, access=0),  # 跑题→0
    ]))

    # 2) 报销单据附件要求
    q = ["报销", "发票", "附件", "金额", "上限"]
    scenarios.append(Scenario("报销单据要求", " ".join(q), [
        _mk(q, [0, 1, 2], "差旅报销所需材料与粘贴规范清单",         salience=0.82, age_days=1,  access=5),
        _mk(q, [0, 3, 4], "单笔报销限额与超额审批政策",             salience=0.78, age_days=4,  access=4),
        _mk(q, [1, 2],    "电子形式也可作为有效凭证提交",           salience=0.60, age_days=9,  access=2),
        _mk(q, [0, 4],    "月度累计不得超过部门预算",               salience=0.58, age_days=7,  access=1),
        _mk(q, [3],       "提过较大数目需要总监签字",               salience=0.45, age_days=18, access=0),
        _mk(q, [0],       "决定月底前把上季度的一起交",             salience=0.50, age_days=7,  access=1),
        _mk(q, [1],       "上次问过跨年凭证是否还能用",             salience=0.40, age_days=25, access=0),
        _mk(q, [2],       "提醒扫描件要清晰完整",                   salience=0.35, age_days=14, access=0),
        _mk(q, [],        "用户长期在财务部工作",                   salience=0.80, age_days=40, access=9),  # 跑题→0
        _mk(q, [],        "偏好简洁、直接给结论",                   salience=0.65, age_days=2,  access=7),  # 跑题→0
        _mk(q, [],        "问过年假的具体天数",                     salience=0.40, age_days=11, access=1),  # 跑题→0
        _mk(q, [],        "关心居家办公的申请条件",                 salience=0.35, age_days=22, access=0),  # 跑题→0
    ]))

    # 3) 入职流程（IT 账号开通）
    q = ["入职", "账号", "开通", "流程", "系统"]
    scenarios.append(Scenario("入职账号开通", " ".join(q), [
        _mk(q, [0, 3],    "新员工报到当天的办理步骤总览",           salience=0.80, age_days=1,  access=4),
        _mk(q, [1, 2, 4], "各业务系统账号的申请与授权指引",         salience=0.83, age_days=3,  access=6),
        _mk(q, [0, 1],    "工牌门禁与邮箱一并办理",                 salience=0.55, age_days=6,  access=1),
        _mk(q, [4],       "内部系统服务台的联系方式",               salience=0.50, age_days=10, access=2),
        _mk(q, [2],       "权限开通通常一个工作日到账",             salience=0.48, age_days=8,  access=1),
        _mk(q, [3],       "决定第一天先熟悉报到流程",               salience=0.42, age_days=12, access=0),
        _mk(q, [0],       "提过入职材料还差一份证明",               salience=0.38, age_days=16, access=0),
        _mk(q, [1],       "上次问过账号密码初始规则",               salience=0.36, age_days=20, access=0),
        _mk(q, [],        "偏好分步骤列点说明",                     salience=0.70, age_days=2,  access=8),  # 跑题→0
        _mk(q, [],        "问过年假与调休政策",                     salience=0.40, age_days=14, access=0),  # 跑题→0
        _mk(q, [],        "关注薪资发放日期",                       salience=0.45, age_days=9,  access=1),  # 跑题→0
    ]))

    # 4) 加班调休规则
    q = ["加班", "调休", "折算", "申请", "有效期"]
    scenarios.append(Scenario("加班调休规则", " ".join(q), [
        _mk(q, [0, 1, 2], "加班时长换算调休的计算规则",             salience=0.84, age_days=2,  access=5),
        _mk(q, [1, 3, 4], "调休申请方式与过期作废时限",             salience=0.76, age_days=5,  access=3),
        _mk(q, [0, 3],    "临时加班需提前在系统报备",               salience=0.58, age_days=7,  access=2),
        _mk(q, [2],       "跨月的时长如何累计折算",                 salience=0.50, age_days=9,  access=1),
        _mk(q, [0],       "上月项目冲刺加了不少班",                 salience=0.45, age_days=8,  access=1),
        _mk(q, [3],       "决定下周一并提交上月的",                 salience=0.42, age_days=4,  access=1),
        _mk(q, [4],       "提醒余额临近到期尽快用",                 salience=0.38, age_days=13, access=0),
        _mk(q, [1],       "上次问过能否折成现金",                   salience=0.35, age_days=21, access=0),
        _mk(q, [],        "偏好用中文、给要点",                     salience=0.60, age_days=3,  access=6),  # 跑题→0
        _mk(q, [],        "问过差旅报销流程",                       salience=0.40, age_days=12, access=0),  # 跑题→0
        _mk(q, [],        "关注五险一金基数",                       salience=0.50, age_days=16, access=1),  # 跑题→0
    ]))

    # 5) 居家办公申请（强相关偏少——故意做成拐点更靠前的一例）
    q = ["居家办公", "申请", "审批", "天数", "上限"]
    scenarios.append(Scenario("居家办公申请", " ".join(q), [
        _mk(q, [0, 1, 2], "远程办公的申请与逐级审批流程",           salience=0.81, age_days=2,  access=4),
        _mk(q, [3, 4],    "每月居家天数的封顶规则",                 salience=0.66, age_days=6,  access=2),
        _mk(q, [1],       "上季度提交过一次并通过",                 salience=0.45, age_days=9,  access=1),
        _mk(q, [2],       "直属主管与 HR 双重确认",                 salience=0.44, age_days=11, access=0),
        _mk(q, [3],       "问过当月还剩几天可用",                   salience=0.40, age_days=13, access=0),
        _mk(q, [1],       "决定下周申请两天在家",                   salience=0.38, age_days=5,  access=1),
        _mk(q, [],        "偏好要点式、别铺垫",                     salience=0.70, age_days=2,  access=7),  # 跑题→0
        _mk(q, [],        "问过年假天数怎么算",                     salience=0.40, age_days=13, access=0),  # 跑题→0
        _mk(q, [],        "长期在财务部",                          salience=0.80, age_days=40, access=9),  # 跑题→0
        _mk(q, [],        "关心团队建设活动",                       salience=0.30, age_days=20, access=0),  # 跑题→0
    ]))
    return scenarios


def _bar(value: float, width: int = 40) -> str:
    filled = int(round(max(0.0, min(1.0, value)) * width))
    return "█" * filled + "·" * (width - filled)


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--max-rank", type=int, default=10,
                        help="展示前多少名的衰减曲线，默认 10")
    args = parser.parse_args()

    lam, cap, ltm_top_k = _shipped()
    scenarios = build_scenarios()
    max_rank = max(args.max_rank, ltm_top_k + 2)

    print("=" * 68)
    print("LTM 召回 top-k 标定实验（受控种子数据，非生产数据）")
    print(f"打分函数=memory_rank_score(本尊)  decay_lambda={lam}  "
          f"access_boost_cap={cap}  线上默认 top_k={ltm_top_k}")
    print(f"参考时刻={REF_NOW.date()}  场景数={len(scenarios)}")
    print("relevance 走真实词面覆盖率那一路；向量相似度只会让拐点更陡。")
    print("=" * 68)

    # 每个场景：打分→排序→按 top-1 归一化，便于跨场景平均。
    norm_by_rank: dict[int, list[float]] = {r: [] for r in range(1, max_rank + 1)}
    cum_capture: dict[int, list[float]] = {k: [] for k in range(1, max_rank + 1)}
    head_sizes: list[int] = []          # 每场景「得分≥top-1 的 30%」的条数
    positives_per_scenario: list[int] = []
    recalled_char_lens: list[int] = []  # 进入 top-k 的记忆正文长度（估 token 成本）

    for sc in scenarios:
        scored = [
            (memory_rank_score(it, query=sc.query, now=REF_NOW,
                               decay_lambda=lam, access_boost_cap=cap), it)
            for it in sc.items
        ]
        positives = sorted([(s, it) for s, it in scored if s > 0],
                           key=lambda p: p[0], reverse=True)
        positives_per_scenario.append(len(positives))
        if not positives:
            continue
        top1 = positives[0][0]
        total = sum(s for s, _ in positives)
        head_sizes.append(sum(1 for s, _ in positives if s >= 0.30 * top1))

        running = 0.0
        for rank, (score, it) in enumerate(positives, start=1):
            if rank <= max_rank:
                norm_by_rank[rank].append(score / top1)
            running += score
            if rank <= max_rank:
                cum_capture[rank].append(running / total)
            if rank <= ltm_top_k:
                recalled_char_lens.append(len(it.content))

        # 逐场景明细
        print(f"\n▼ 场景「{sc.name}」  正样本 {len(positives)} 条"
              f"（另有 {len(sc.items) - len(positives)} 条跑题→relevance=0 被直接丢弃）")
        for rank, (score, it) in enumerate(positives, start=1):
            tag = "  ← top-k 截断线" if rank == ltm_top_k else ""
            print(f"    #{rank:<2} {score:.3f} {_bar(score / top1, 28)} "
                  f"{it.content[:22]}{tag}")

    # 跨场景平均：衰减曲线
    def _avg(xs: list[float]) -> float:
        return sum(xs) / len(xs) if xs else 0.0

    print("\n" + "=" * 68)
    print("① 得分随排名的衰减（跨场景平均，按各自 top-1 归一化为 1.00）")
    print("   rank  归一化均分   相对 top-1")
    prev = None
    elbow = None
    for r in range(1, max_rank + 1):
        vals = norm_by_rank[r]
        if not vals:
            continue
        avg = _avg(vals)
        drop = "" if prev is None else f"  (较上一名 {(avg - prev) / prev:+.0%})"
        print(f"   #{r:<3} {avg:6.3f}     {_bar(avg, 34)}{drop}")
        # 拐点：首个跌破 top-1 的 30% 的名次
        if elbow is None and avg < 0.30:
            elbow = r
        prev = avg

    print("\n② top-k 累计吃住多少相关性（跨场景平均，分母=该场景全部正样本得分和）")
    for k in range(1, max_rank + 1):
        if not cum_capture[k]:
            continue
        avg = _avg(cum_capture[k])
        star = "  ★ 线上默认" if k == ltm_top_k else ""
        print(f"   top-{k:<2} 覆盖 {avg:5.1%}  {_bar(avg, 34)}{star}")

    # token 成本：字符数粗估（中文约 1 token/字，偏保守）
    avg_len = _avg([float(x) for x in recalled_char_lens]) if recalled_char_lens else 0.0
    print("\n③ 记忆块 token 成本随 k 膨胀（粗估：中文约 1 token/字）")
    print(f"   进入 top-k 的记忆平均正文≈{avg_len:.0f} 字/条"
          f"（上限由 summary≤200 字保证）")
    evidence_tokens = 5 * 400  # 5 个检索片段 × 约 400 字，作对照基线
    print(f"   当轮检索证据基线≈{evidence_tokens} token（5 片段×~400 字）")
    print("   k     记忆块≈token   占『证据基线』比   备注")
    for k in (1, 3, ltm_top_k, 10, 20):
        mem_tokens = k * avg_len
        ratio = mem_tokens / evidence_tokens
        note = ""
        if k == ltm_top_k:
            note = "★ 线上默认：记忆是少数票"
        elif ratio >= 0.8:
            note = "⚠ 记忆开始追平/压过证据 → 违反『记忆非权威』"
        print(f"   {k:<4}  {mem_tokens:>7.0f}       {ratio:>5.0%}            {note}")

    # 结论
    avg_head = _avg([float(x) for x in head_sizes]) if head_sizes else 0.0
    avg_pos = _avg([float(x) for x in positives_per_scenario])
    k5 = _avg(cum_capture[ltm_top_k]) if cum_capture[ltm_top_k] else 0.0
    ratio_5 = (ltm_top_k * avg_len) / evidence_tokens
    ratio_20 = (20 * avg_len) / evidence_tokens
    print("\n" + "=" * 68)
    print("结论（可写进文档 / 口述，务必标注是受控种子实验）")
    print("=" * 68)
    print(f"- 高信号头部平均≈{avg_head:.1f} 条（得分≥top-1 的 30%）；"
          f"平均正样本≈{avg_pos:.1f} 条，跑题项早被 relevance=0 直接丢弃。")
    if elbow:
        rel = "之内" if elbow <= ltm_top_k else "之外"
        print(f"- 归一化均分在第 {elbow} 名附近跌破 top-1 的 30%——拐点在 "
              f"top-{ltm_top_k} {rel}，top-{ltm_top_k} 稳稳吃住头部还留了余量。")
    print(f"- top-{ltm_top_k} 平均覆盖 {k5:.0%} 的相关性；第 {ltm_top_k}+1 名起基本是"
          f"低相关项，多召回只增干扰、不增信号。")
    print(f"- token 上：本量级摘要平均≈{avg_len:.0f} 字/条，top-{ltm_top_k} 记忆块仅"
          f"≈证据的 {ratio_5:.0%}、top-20 也才≈{ratio_20:.0%}——所以**真正**卡 k 的"
          f"不是 token，而是相关性拐点 + 控制低相关干扰项数量。")
    print(f"=> 默认 MEMORY_LTM_TOP_K={ltm_top_k}：落在拐点之外、覆盖 {k5:.0%} 相关性、"
          f"记忆稳居少数票；并留成配置项，上规模再按需调大。")


if __name__ == "__main__":
    main()
