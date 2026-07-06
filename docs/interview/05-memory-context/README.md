# 05. 四层记忆与上下文窗口

## 一句话

四层记忆 + 滑动窗口 + 滚动摘要 + 结构化抽取 + **重要性/时间排序**；  
冷热是 **prompt 装配策略**，不是独立冷热存储。记忆 **非权威**。

## 四层模型

| 层 | 存储 | 作用 |
|---|---|---|
| **L0** | `messages` | 全量原始对话 |
| **L1** | 最近 K 轮 + `conversations.summary` | 热区窗口 + 温区滚动摘要 |
| **L2** | `memory_items`（long_term_event 等） | 事件级 LTM，向量/关键词召回 |
| **L3** | `memory_items` entity / preference | 实体 upsert + 固定偏好 |

## 你学的概念 ↔ 项目实现

| 概念 | 项目落点 | 完成度 |
|---|---|---|
| 滑动窗口 | `load_recent_messages(window_turns=6)` | ✅ |
| 摘要压缩 | `compress_to_summary` → `conversation.summary` | ✅ |
| 重要性过滤 | writeback `salience` 阈值 + rank 中的 importance | ✅（写入+召回） |
| 时间记忆 | `expires_at` + recency 衰减 + access_boost | ✅（轻量） |
| 结构化抽取 | `extract_memory_events` | ✅ |
| 冷热区 | hot/warm/cold→selected 装配 | ⚠️ 装配有，非物理冷存 |

## 召回排序公式（可写白板）

```text
relevance = max(vector_sim, keyword * 0.85)
importance = 0.5 * confidence + 0.5 * meta.salience
recency = exp(-λ * age_days)          # λ 默认 0.08
access_boost = min(cap, log1p(access_count) * 0.03)
final = relevance * (0.55 + 0.35 * importance + 0.10 * recency) + access_boost
```

配置：`MEMORY_RANK_DECAY_LAMBDA`、`MEMORY_RANK_ACCESS_BOOST_CAP`、  
`MEMORY_CONVERSATION_FACT_TTL_DAYS`、`MEMORY_STM_UNLOAD_TTL_DAYS`。

## 冷热装配（诚实版）

| Zone | 内容 | 来源 |
|---|---|---|
| **hot** | 最近 K 轮 | STM 窗口 |
| **warm** | rolling summary + 固定 prefs/entities | summary + always-on |
| **cold→selected** | top-k LTM | `search_memories` 排序截断 |

窗外 raw messages **仍在 L0**；压缩不物理删除。

## 关键代码

| 文件 | 职责 |
|---|---|
| `backend/app/agents/memory_agent.py` | load / writeback / compress |
| `backend/app/services/memory_window.py` | 窗口与滚动摘要 |
| `backend/app/services/memory_extractor.py` | 事件抽取 + salience |
| `backend/app/services/memory_service.py` | 存储、排序、TTL 过滤、entity upsert |
| `backend/app/services/query_rewrite.py` | 短跟进补全检索 query |
| `backend/app/agents/answer_agent.py` | prompt 分区（热/温/冷文案） |

## 硬约束（面试必说）

1. **记忆非权威** — 不能覆盖本轮 RAG 证据  
2. **偏好禁政策事实** — `MEMORY_POLICY_FACT_FORBIDDEN`  
3. **短跟进靠 query rewrite**，不只靠窗口  
4. **fixed prefs/entities 不走衰减 search**，避免长期偏好被 recency 压掉  
5. 管理面 `GET/DELETE /api/memory` **仅本人**

## 30 秒口述版

> 多轮不能全历史塞 context。我用分层：热区最近 K 轮；窗外滚成摘要；异步结构化抽取偏好/实体/事件；召回时用相关性×重要性×时间衰减排序。  
> 冷热是装配，不是两套库。记忆只服务指代和风格，制度仍以本轮检索为准。

## 测试锚点

- `tests/test_memory_system.py`：排序、过期、TTL writeback、compress 幂等、多轮偏好  
- `tests/test_phase3_skill_draft_mcp_memory.py`：policy ban、non-authoritative  

## 相关

- 架构防腐 → [02](../02-architecture/README.md)
- 边界清单 → [09](../09-honesty-boundaries/README.md)
