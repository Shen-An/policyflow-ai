# 08. 前端与交互体验

> 这章是「前端体验点清单」。看不懂的词先查 [白话术语表](../00-glossary/README.md)(SSE / HITL / diagnostics / token 化配色等)。

## 定位

管理端 + 制度问答工作台：信息层级清晰、少遮挡、贴近常见聊天产品；视觉为 **soft mint canvas + 白浮动卡片 + 浅色侧栏**（非厚重 indigo 管理台），并支持 **亮 / 暗主题切换**（`next-themes` + token 化配色）。

## 聊天 UX 面试点

| 点 | 说明 |
|---|---|
| SSE 阶段 | 记忆加载 / rewrite / 检索 / 回答 / writeback 可见 |
| 助手 Markdown | 制度清单、引用可读 |
| 复制 | 回答底部复制；用户气泡悬停复制/编辑 |
| 滚动 | 打开/刷新滚到最新 |
| 空状态 | 可点示例问题 |
| diagnostics | 展示真实 stage/tool/memory source_slot |
| ToT 路径选择 | `branched` 任务出 2–3 候选计划，双请求 HITL 让用户选路（可取消） |
| 会话管理 | 会话重命名 / 删除；操作走统一反馈 |

## 记忆管理页

- 路由：`/memory`  
- 仅本人；类型过滤 + 删除  
- 诚实文案：制度事实仍以知识库检索为准  
- 展示 confidence 等；rank_score 可在 diagnostics，管理页不强制展示公式  

## 模型设置页（`/model-settings`）

- **三类服务完全独立配置**：Chat / Embedding / **NVIDIA Cross-Encoder Reranker**，可分别接不同厂商  
- Reranker 走 `nvidia_rerank` 协议，模型在三个 NVIDIA rerank 模型间轮换 + 回退；API Key 加密存储  
- 每类可「拉取模型 / 测试连接」；测试连接对**真实服务**发一次调用（reranker 是 1-passage rerank）  
- 诚实文案：若检索由独立 LightRAG 服务执行，其 Embedding 配置需与此保持一致  

## 评估页

- 默认聚焦 **Hit@1 / Hit@5 / Hit@10 / MRR**；次要配置、调试、逐条结果折叠  
- 策略名与 N 要看得见  
- **评测知识库选择器**：CRUD 测试库（`eval_test`）/ 企业政策测试库（`enterprise_eval_test`），跑 Run 必须先选，越界报错  
- **一键 seed 企业政策测试集**（12 篇制度 + 200 用例）；索引后台排队，不卡页面  
- **Rerank 页面选择**：`local_lexical_fusion` / `cross_encoder`（真 NVIDIA），run summary 记方法 / 后端；选 `cross_encoder` 不可用直接报错、不静默降级  
- stale gold（金标已删 / 空）用例可一键清理，避免虚高  

## 前端技术叙事（保持克制）

- 特征目录：`frontend/src/features/chat|memory|model-settings|evaluation|…`  
- API client 与页面契约测试（`*.test.tsx` / contract tests）  
- 全局 mutation 反馈：`app/global-feedback.ts` + `#toast-root`；组件已内联 `Alert`/`message` 的不重复弹  
- 不吹「自研低代码 / 微前端」——就是清晰的 feature 分层管理台  

## 面试怎么结合现场

1. 打开聊天 → 问流程题 → 指 SSE stage  
2. 指 diagnostics：无假 tool trace  
3. 再问短跟进「给我模板」→ 体现 rewrite + 历史  
4. 打开 `/memory` → 偏好可见，并口述非权威  
5. 评估页 → 指标卡片层级；切评测库（CRUD / 企业政策）  
6. 模型设置页 → 三类服务独立；如已配 NVIDIA，评估页可 A/B `local_lexical_fusion` vs `cross_encoder`  

## 相关设计文档

- `docs/frontend/*` 全套 UI/路由/契约  
- `docs/06-frontend-implementation-design.md`  

## 边界

- 非生产 IM（已读回执、多端同步等未做）  
- 视觉持续打磨中；以可用性与信息层级为主，不炫技  
