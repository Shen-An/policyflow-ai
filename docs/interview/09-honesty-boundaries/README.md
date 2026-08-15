# 09. 诚实边界与反吹牛清单

> 面试加分项：**主动说边界**，比被追问穿帮强。

## 必说清单（建议逐条能举例子）

1. **不是 multi-agent 平台** — 统一编排 + Answer tool loop  
2. **Rerank 默认本地 lexical fusion** — 不是 BGE/cross-encoder  
3. **MCP 企业连接器可 mock** — 响应带 `status=mock`；stdio demo 可真连  
4. **记忆非权威** — 不能当制度依据；偏好禁政策事实  
5. **冷热记忆 = 装配策略** — 非独立冷热存储 / 非物理归档窗外消息  
6. **SQLite + JSON embedding** — demo 规模，非百万向量生产检索  
7. **claim–evidence 是词重叠规则门** — 不是 LLM-as-judge 全文事实核查  
8. **Eval 采样** — 默认随机 50/100；写清策略与 N；干扰文档防虚高  
9. **Hybrid 未必显著优于 BM25** — 看任务形态，1-doc 匹配常接近  
10. **LightRAG 分数可能 synthetic** — 别当真实相似度  
11. **Reflection 是可选质量环** — Critique→Improve 双 prompt + 硬轮次；**不是**事实 oracle，也**不是** peer multi-agent 辩论；规则 Compliance 仍是最后一道门；Eval 默认关  
12. **数据库 rollback 不能撤销外部副作用** — 邮件、日历、飞书超时后可能已经执行；应标记 `unknown`，先核对状态，再决定重试或补偿
13. **统一兜底门已落地基础版** — 已有请求级 Turn Budget、Retrieval Quality Gate、`PASS/REVISE/REFUSE` 发布门和一次改稿复检；质量判断仍以确定性规则与轻量词重叠为主，不是完美事实核查
14. **外部副作用恢复不是完整 Saga** — 已有幂等键、成功结果复用、超时 `unknown` 和禁止盲目重发；状态查询适配器、补偿 Tool、高风险确认仍待按真实连接器实现
15. **进程内 LightRAG 使用离线 tokenizer** — 为避免启动时依赖 `tiktoken` CDN，适配器注入可逆 Unicode codepoint tokenizer 做切块和 token 预算；它不等同于 OpenAI BPE token 统计，实际检索指标仍以离线 Eval 为准

## 简历禁用词 → 替换说法

| 禁用/慎用 | 替换 |
|---|---|
| 多智能体协作平台 | 统一编排的 tool-using RAG |
| 真 cross-encoder rerank | 本地 lexical fusion rerank |
| 已对接飞书生产 | MCP 协议客户端 + mock 企业连接器 |
| 完整记忆中台 / Memory OS | 四层记忆装配 + 事件抽取 |
| 生产级向量数据库 | SQLite JSON embedding（MVP） |
| 100% 准确率 | Hit@K/MRR + N + 策略 + 干扰设置 |

## 半实现 / 已做 / 不做（速查）

| 主题 | 状态 |
|---|---|
| 滑动窗口 + 滚动摘要 | 已做 |
| 结构化记忆抽取 | 已做 |
| salience + 时间衰减排序 | 已做（轻量公式） |
| 物理冷归档 messages | **不做**（本阶段） |
| cross-encoder | **不做**（默认） |
| 群聊式 multi-agent | **不做** |
| 静态拓扑 + 中心化 Supervisor | 已做 |
| 正式 TurnState 黑板 + errors[] | **已做**（单轮请求内；非分布式状态机） |
| 错误集中写入（步骤/检索/Skill/合规） | **已做**（写入 ledger + diagnostics；非静默吞错） |
| Critique→Improve 反思闭环 | **已做**（高风险触发；max 2 轮；Eval 默认关；非群聊辩论） |
| 请求级 Turn Budget | **已做**（LLM/检索/Tool 次数 + 整轮耗时） |
| Retrieval Quality Gate | **已做基础版**（空证据、偏题、rewrite 漂移；最多回退原问题一次） |
| PASS/REVISE/REFUSE 发布门 | **已做**（定向改稿一次并复检，失败则安全拒答） |
| 外部副作用恢复 | **半实现**（幂等与 `unknown` 基础；未实现完整查询/补偿/Saga） |
| Tool / Reflection 模块级最大轮数 | **已做** |
| 单轮全局调用/耗时预算（Turn Budget） | **规划中，未做** |
| Retrieval Quality Gate（accept/retry/refuse） | **规划中，未做** |
| Answer 发布门（PASS/REVISE/REFUSE） | **规划中，未做** |
| 外部 Tool 幂等键 / unknown 状态 / 补偿 | **规划中，未做** |
| peer 消息总线 / actor 系统 | **不做** |
| LLM-as-judge 替代 Hit@K/MRR | **不做** |
| RAGAS | 可选，非主指标 |
| 硬删知识库/文档 | 已做（含关联清理意图） |

## 被追问时的态度模板

> 「这是面试可演示的 MVP。我把 **可验证路径** 和 **指标** 做扎实，并在文档里写清 mock / 本地公式 / 采样边界。扩展向量库或 cross-encoder 是下一阶段，不是当前诚实叙事的一部分。」

## 文档同步点

- `docs/08` 落地状态  
- `docs/09` 演示与诚实边界  
- `docs/interview/*` 本知识库  
- `CLAUDE.md` 项目约定  

改 AI 行为却不更新这些 = 下次面试自找穿帮。  
