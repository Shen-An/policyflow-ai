# RSI 自迭代中的 Judge 选型：LLM-as-Judge vs Agent-as-Judge

> 状态：设计决策（ADR 风格）。指导 spec-kit（specify → plan → tasks → implement → converge）之上叠加自动自迭代循环时，评估面（judge）怎么建。
> 关联：`docs/08-de-toy-multiagent-skill-eval-strategy.md`（诚实性约定）、`.specify/memory/constitution.md`、`backend/app/agents/reflection_loop.py`。

## 0. 一句话结论

**LLM-as-Judge 做内循环的打分器（pointwise/pairwise 标量，全量候选）；Agent-as-Judge 做外循环的审计员（可调用工具去"反驳"，低频，只有否决权没有打分权）；而两者之前必须先有一层不含 LLM 的确定性门控。**

任何"有 gold label 就能算"的维度，**不要用 judge**——judge 只用来补 gold 缺失的主观面。

## 1. 为什么内循环不能是 Agent-as-Judge（三条硬约束）

RSI 循环的数学形态是：`propose → score → keep/rollback`，迭代 N 个候选 × M 轮。这要求 `score()` 满足：

| 要求 | 含义 | LLM-as-Judge | Agent-as-Judge |
|---|---|---|---|
| **可比性** | 同 rubric 同尺度，第 3 轮分数能跟第 2 轮直接比 | ✅ 单次调用，输入固定 → 输出稳定 | ❌ 每轮工具轨迹不同，方差来自**评审过程**而不是**被评物**，跨轮不可比 |
| **吞吐/成本** | 内循环要跑得动（几十上百次评分） | ✅ 1 次调用，1–2k token，秒级 | ❌ 15–80 次工具调用，10–100× 成本，分钟级 → 循环只能跑 1–2 轮，等于没有自迭代 |
| **单调改进可验证** | 分数上涨要能归因到改动 | ✅ 固定 model+prompt hash+seed 后噪声可控 | ❌ 无法区分"变好了"和"这次它多读了两个文件" |

反过来，LLM-as-Judge 的致命弱点恰好是 Agent-as-Judge 的存在理由：
**LLM judge 读不到真相，只读得到你给它看的文本。** 它会相信"测试结果全绿"的自述、被结构整齐的 diff 说服、给冗长文档高分（verbosity bias）、给自己同源模型的输出加分（self-preference bias）。所以凡是**"必须动手验证才知道"**的判断（跑测试、查调用点、打接口、复现一个说法），LLM judge 做不了，必须交给带工具的审计 agent。

> 记法：**Judge = 打分器，Auditor = 否决器。** 把 Auditor 塞进内循环当打分器 = 又慢又不可比；把 Judge 当最终验收 = 自欺。

## 2. 四层评估面（本项目落地形态）

```
候选改动 (patch / spec / 答案 / 检索配置)
   │
   ├─ L0 确定性门控   100% 候选，零 LLM，任一失败 → 直接拒绝 + 机器可读失败原因回灌下一轮
   ├─ L1 LLM-as-Judge 仅 L0 通过者，pointwise 打分 + 候选间 pairwise 排序 → 循环的 fitness 信号
   ├─ L2 Agent-as-Judge 仅 top-1 / 最终验收 / L1 临界带 / 指标回退时 → 只有 veto 权
   └─ L3 人工抽检     ~10% 抽样 + 每一次 L2 veto → 唯一的地面真值，用于校准 L1
```

### L0 — 确定性门控（先做这个，它才是 RSI 的主目标函数）

不花 token、不可被讨好、失败原因可以原样喂回迭代。分两类：

1. **构建正确性**：`ruff` / `mypy` / `pytest`（改动的测试子集先跑，全量最后跑）、`alembic check`（迁移一致性）、`quickstart.md` 里的验收命令。
2. **项目约定 lint（把 AGENTS.md 里的禁令变成可执行检查）** —— 这是本项目最独一份、也最能证明"不是玩具"的一层。写成 `scripts/check_constitution.py`，命中即拒绝：
   - 出现伪造 diagnostics（`skill.suggest:*` 之类假 tool trace）；
   - Chat / Eval 出现双写 stage（绕过 `AgentPipeline` 的平行编排实现）；
   - 把 `relevance_judgement.negative=true` 的样本算进 Hit@K / MRR（见 `backend/app/evals/negatives.py`）；
   - 评测语料导入到 `hr` / `finance` 等业务库（只允许 `code=eval_test`）；
   - MCP / mock 响应缺 `status=mock`；
   - 把 local lexical fusion 描述成 cross-encoder、或把 mock 写成生产可用（文档字符串层面 grep）；
   - **触碰评审面**（见 §3，最重要的一条）。

### L1 — LLM-as-Judge（内循环打分器，实现约束要写死）

- **任务分工**：**排序用 pairwise，绝对 PASS 用 pointwise**。pointwise 的 1–5 分跨批次不可比（分数分布会漂），pairwise 稳定得多；但 promote 需要一条绝对门槛，所以两个都要。
- **必须引用原文**：输出 JSON schema 强制 `{"dimension": str, "score": int, "evidence_quote": str, "reasoning": str}`；`evidence_quote` 必须能在被评文本里逐字命中，否则该条判词作废。这一条直接掐掉"凭空给差评/好评"和幻觉评审，成本极低、收益极大。
- **温度 0 / 结构化输出 / 单次调用**；rubric 维度建议 4 个，不要超过 5：需求覆盖、证据一致性、边界诚实性（有没有夸大、有没有该拒答不拒答）、可维护性。
- **异族模型**：judge 的模型族 ≠ 生成候选的模型族（self-preference bias 是可测的，同族打分普遍偏高 0.3–0.7 分档）。
- **版本入档**：每次 Run 记录 `judge_model` / `judge_prompt_sha256` / `rubric_version` / `seed`。**judge 换版本 = 历史分数全部作废**，必须重跑 baseline 才能再比——这条写进 DB 字段，别靠自觉。

### L2 — Agent-as-Judge（审计员，只给否决权）

- 触发点稀疏：一个 feature 1–3 次。只在 L1 通过、准备 promote 时；或 L1 分数落在临界带 ±0.5；或 L0 指标出现回退需要归因。
- 给它**工具**而不是给它**更长提示**：读 diff、`pytest`、grep 调用点、打 `/api` 端点、读 eval Run 明细、跑 `scripts/analyze_rerank_scores.py`。
- 任务是**反驳（refute）**不是**表扬**：prompt 目标写成"找出这个改动不满足 spec 验收条款的具体证据，找不到才判 PASS"，输出 `verdict` + `blocking_concerns[]` + 每条挂**证据链接**（文件:行 / 命令输出片段）。没有证据链接的 concern 视为无效，防止它自由发挥。
- **不允许**把 L2 的自然语言结论直接当 fitness——它的输出必须是 `PASS / FAIL / NEED_HUMAN` 三值 + 理由，否则又回到 §1 的不可比问题。

### L3 — 人工抽检 & 校准（决定整套东西可不可信）

没有这层，judge 只是把"我觉得好"换了个说法，面试一问就塌。

1. 人工标 **50 条**（够算一致性，不够也别硬凑几百）作为 judge 的金标；
2. 报告 **judge–human agreement**：分类 verdict 用 Cohen's κ，连续分用 Spearman ρ；
3. **门控规则**：κ < 0.6 → L1 只做"提示/排序参考"，**不得阻断循环**；0.6 ≤ κ < 0.7 → 只允许 L1 做候选排序；κ ≥ 0.7 → L1 才有资格当 promote gate；
4. 换 judge 模型 / 改 rubric / 换被评物类型（代码 → 答案）都要重跑校准；
5. 把这个数字写进 Run 记录和 `docs/09` 面试叙事——**"我知道我的评估器有多可信，κ=0.74，N=50"** 比"我们加了 LLM judge"强得多，也符合项目"指标必须写清策略和 N"的约定。

## 3. RSI 的第一原则：评审面必须在可改进面之外

这是自迭代和普通"多跑几次挑最好"的唯一实质区别，也是最容易塌的地方：

- `judge_prompt/`、`rubric.md`、`L0 lint 规则`、**hidden holdout 数据集** 放在候选 agent **无写权限**的路径；
- L0 加一条检查：本轮 diff 若触碰评审面 → **直接拒绝整轮，不进入 L1**（不解释、不协商）；
- 评审面变更只能由人发起，且必须 **bump 版本号 + 重跑 L3 校准 + 重跑 baseline**，三件事缺一不算变更完成；
- 循环永远看不到 holdout：日常迭代用可见的 dev 集算 fitness，**只在 promote 那一刻跑 holdout**。可见集涨、holdout 不涨 = 已经在过拟合循环本身，这是最有价值的报警信号。

**Goodhart 检测**（廉价，建议直接实现）：画两条曲线，L1 分数 vs 主指标（Hit@1/Hit@5/Hit@10/MRR 或 κ-校准过的客观指标）。若 **L1 分数持续上涨而主指标走平** → 循环在讨好 judge，立刻停 loop 并标记为 `judge_drift_suspected`，人工介入。

## 4. 三个别混淆的循环（本项目已有 / 待建）

| 循环 | 位置 | fitness 信号 | 用不用 judge |
|---|---|---|---|
| **运行时内循环**（答案质量） | 已存在：`reflection_loop.py` Critique → Improve，`CHAT_REFLECTION_MAX_ROUNDS=2`，显式 PASS 出口，只跑高风险轮 | critique verdict + `grounding.py` 词面支撑检查 | 轻量 LLM judge，**保持现状**，别再叠一层 agent |
| **开发循环**（spec → 代码） | 本次要加的 RSI 主体 | **L0 硬门 + L1 LLM-as-Judge**；L2 只做验收 | L1 + L2，按 §2 分层 |
| **检索/参数调优循环** | 已有 `eval_runner` / Hit@K / MRR | **金标指标，零 judge**（阈值调整先跑 `analyze_rerank_scores.py`，误拒率和拦截率两侧一起看） | ❌ 有 gold 的地方用 judge 是降级 |

> `grounding.py` 里那句注释 "Lexical claim–evidence support check (**not an LLM judge**)" 是对的判断，别在自迭代压力下把它换成 LLM judge——确定性检查的价值就在于不会被说服。

## 5. 终止与预算（复用 reflection_loop 的纪律）

`reflection_loop` 的注释已经写了核心信条：**never trust the model to stop**。RSI 循环照搬：

- `RSI_MAX_ROUNDS`（建议 3–5，代码类改动收益递减很快）；
- 连续 `K` 轮无提升（含统计噪声带）→ 停；
- token/成本 ceiling + wall-clock ceiling → 停；
- 每轮落一条审计记录：候选、L0 结果、L1 verdict（含 quote）、L1 分数、judge 版本、耗时、成本。**禁止**只存"最终成功"——循环的可信度等于它的日志完整度。

## 6. 实施顺序（最小可用先跑起来）

1. `scripts/check_constitution.py` —— 把 §2 L0 的约定禁令变成 exit-code；
2. L1 judge：`backend/app/evals/judge/`（`prompt_v1.md` + `rubric_v1.md` + `schemas.py`，强制 `evidence_quote` 校验）+ DB 字段 `judge_model/prompt_sha256/rubric_version/seed`；
3. L3 校准：人工标 50 条 + 算 κ/ρ + 定门槛（**必须在 L1 想当 gate 之前完成**）；
4. 把 spec-kit 的 `implement` 步骤接到 L0+L1 上（失败原因回灌），加 hidden holdout；
5. L2 审计 agent：最后做，且只给它 promote 路径的否决权；
6. `docs/08` §落地状态 与 `docs/09` 面试叙事补一段：judge 的可信度数字与边界。

## 7. 明确不做什么（避免变成玩具）

- ❌ 不做"多个 agent 互评取平均"当质量保障——那只是把一次 LLM 偏差采样三次；
- ❌ 不让被改进的 agent 参与定义/修改自己的评分规则（§3）；
- ❌ 不在金标指标（Hit@K/MRR）之上再叠一层 LLM 分数合成"综合得分"——两种不同证据强度加权平均没有意义，分开报；
- ❌ 不宣称"自主改进"；对外表述统一为 **bounded self-refinement with a frozen, calibrated evaluator**（有界自精化 + 冻结且经校准的评估器），边界写清楚：max rounds、人工 promote、κ 数值、N、采样规模。
