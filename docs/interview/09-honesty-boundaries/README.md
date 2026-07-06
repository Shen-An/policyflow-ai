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
