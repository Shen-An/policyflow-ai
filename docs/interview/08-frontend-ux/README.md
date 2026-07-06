# 08. 前端与交互体验

## 定位

管理端 + 制度问答工作台：信息层级清晰、少遮挡、贴近常见聊天产品；视觉为 **soft mint canvas + 白浮动卡片 + 浅色侧栏**（非厚重 indigo 管理台）。

## 聊天 UX 面试点

| 点 | 说明 |
|---|---|
| SSE 阶段 | 记忆加载 / rewrite / 检索 / 回答 / writeback 可见 |
| 助手 Markdown | 制度清单、引用可读 |
| 复制 | 回答底部复制；用户气泡悬停复制/编辑 |
| 滚动 | 打开/刷新滚到最新 |
| 空状态 | 可点示例问题 |
| diagnostics | 展示真实 stage/tool/memory source_slot |

## 记忆管理页

- 路由：`/memory`  
- 仅本人；类型过滤 + 删除  
- 诚实文案：制度事实仍以知识库检索为准  
- 展示 confidence 等；rank_score 可在 diagnostics，管理页不强制展示公式  

## 评估页

- 默认聚焦 **Hit@1 / Hit@5 / Hit@10 / MRR**  
- 次要配置、调试、逐条结果折叠  
- 策略名与 N 要看得见  

## 前端技术叙事（保持克制）

- 特征目录：`frontend/src/features/chat|memory|…`  
- API client 与页面契约测试（`*.test.tsx` / contract tests）  
- 不吹「自研低代码 / 微前端」——就是清晰的 feature 分层管理台  

## 面试怎么结合现场

1. 打开聊天 → 问流程题 → 指 SSE stage  
2. 指 diagnostics：无假 tool trace  
3. 再问短跟进「给我模板」→ 体现 rewrite + 历史  
4. 打开 `/memory` → 偏好可见，并口述非权威  
5. 评估页 → 指标卡片层级  

## 相关设计文档

- `docs/frontend/*` 全套 UI/路由/契约  
- `docs/06-frontend-implementation-design.md`  

## 边界

- 非生产 IM（已读回执、多端同步等未做）  
- 视觉持续打磨中；以可用性与信息层级为主，不炫技  
