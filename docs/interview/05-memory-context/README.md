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

## 白话详解（四层记忆 + 冷热 + 公式）

### 四层记忆像你脑子里的四层抽屉

| 层 | 大白话 | 打比方 |
|---|---|---|
| **L0** | 全量原始对话,每句都录着,不丢。 | 录音机,全程开着。 |
| **L1** | 最近几轮(手边能立刻想起的) + 一段「前面聊过啥」的滚动提要。 | 桌面上摊着的那几张纸 + 一张便签写的摘要。 |
| **L2** | 特意记下来的「发生过的事」(比如用户提过下周要出差),能按意思/关键词找回来。 | 你的工作笔记,能翻能搜。 |
| **L3** | 记住的「这人是谁、喜欢啥」(比如用户在财务部、喜欢简洁回答)。 | 名片夹 + 习惯小本。 |

### 冷 / 热 / 温是什么意思

**就是「拼提示词时,把记忆摆哪」的摆放方式,不是三个独立仓库。**

- **热区**:最近几轮对话,原样全塞进提示词。
- **温区**:滚动摘要 + 固定偏好/实体(比如「用户在财务部」),也塞进去。
- **冷区**:长期记忆(L2),按当前问题临时挑几条相关的出来塞进去,不是全倒进去。

摆不下的原始对话**还在 L0 录音机里,没删**,只是这轮没塞进提示词而已。

### 召回排序公式,翻成人话

那条公式(`final = relevance × (0.55 + 0.35·importance + 0.10·recency) + access_boost`)就是「从长期记忆里挑哪几条进提示词」的打分,按三个考量 + 一个小加分:

| 公式里的 | 人话 | 意思 |
|---|---|---|
| `relevance` | 相不相关 | 这条记忆跟现在的问题,意思上像不像(取向量相似度和关键词匹配里高的那个)。 |
| `importance` | 重不重要 | 当初记这条时打的重要分(由置信度 + salience 算来)。 |
| `recency` | 多久以前 | 越新分越高一点,老的慢慢淡化(`exp(-λ·age)`,λ 默认 0.08)。 |
| `access_boost` | 被翻过几次 | 经常被翻到的记忆,加一点点分(对数增长,封顶)。 |

最后:相关性是大头(乘 0.55 起步),重要性加成 0.35,新旧加成 0.10,再叠被翻过的加分。**核心:先看相不相关,再看重不重要,新旧只占一小成。**

**必说的诚实红线:**
1. **记忆非权威**——它只用来搞懂指代(「他」「那份」)和风格,绝不能盖过这一轮查到的制度证据。
2. **偏好禁写制度事实**——不会把「年假是 5 天」当偏好存进去(`MEMORY_POLICY_FACT_FORBIDDEN`)。
3. **「给我模板」这种短跟进**,要先 `query rewrite` 把省略的补全,光靠窗口翻不到主题。

▸**面试可以说**:「多轮不能把历史全塞进去。我分四层:原始全录、最近几轮+滚动摘要、事件级长期记忆、实体偏好。拼提示词时最近放热区、摘要和偏好放温区、长期记忆按需挑几条放冷区——是摆放策略不是三个库。召回长期记忆按相关×重要×新旧排序。记忆只服务指代和风格,制度仍以本轮检索为准。」

---

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

### 各层各拿多少（默认配额）

装配进 prompt 的每个区都有名额上限，不是无脑全塞。默认值（`config.py`，可调）：

| Zone | 内容 | 拿多少 | 配置项 |
|---|---|---|---|
| **hot** | 最近几轮原话 | 最近 **6 轮** | `MEMORY_STM_WINDOW_TURNS=6` |
| **warm** | 固定偏好 | 最多 **10 条** | `MEMORY_FIXED_PREFS_LIMIT=10` |
| **warm** | 实体 | 最多 **8 条** | `MEMORY_ENTITY_LIMIT=8` |
| **cold→selected** | 长期事件召回 | 排序后 **top-5** | `MEMORY_LTM_TOP_K=5` |

即长期记忆（L2 事件）按上面的公式打分排序后，只截 **top-5** 进 prompt；热区是最近 6 轮，固定偏好/实体各有 10 / 8 的名额。**不是把整个记忆库倒进上下文**——这既控 token，也避免旧记忆压过本轮证据。

▸**面试可以说**：「每个区都有配额：热区最近 6 轮，固定偏好/实体各封顶 10 / 8，长期记忆按相关×重要×新旧排序后只取 top-5。控 token，也防旧记忆盖过本轮检索。」

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
