# 陈昱题库 v2（全量版）

> 覆盖 2026-05 ~ 2026-08 共 47 份面经文档、1250+ 题、1696 条去重问句、E17–E135 题号库、43 道手撕题。
> 按「考点 3 层次」（E84 框架）组织。标 🔥 为多厂命中高频题。

---

## 第 1 层：概念与架构认知（入场券）

### Agent 基础
- 🔥 Agent 和 LLM / 普通 API 调用 / Chatbot 的本质区别？ChatBot 加上插件算 Agent 吗？网页版对话助手算 Agent 吗？RAG+Chat 算 Agent 吗？
- 🔥 Agent 和 Workflow 的区别？什么时候用 Workflow 就够了？如何判断一个场景用 Agent 还是 Workflow？
- Tools / Workflow / Agent 三层次区别（阿里必考）
- Agent = Model + Harness 这个等式怎么理解？Harness 六层组件各解决什么问题？
- Agent 的工作模式有哪些？四种模式怎么选？
- Agentic Loop 是什么？画出流程图。上下文治理怎么做？
- 🔥 ReAct 与 CoT 的区别？ReAct / Plan-and-Execute / Reflexion 三者区别和适用场景？
- ToT 和 CoT 的本质区别？什么时候 CoT 反而降低性能？BFS/DFS 搜索策略怎么选？
- 反思（Reflection）和 ReAct 循环有什么区别？反思结果会不会污染上下文？失败如何兜底？

### 模型底层
- 🔥 Transformer 核心结构，Q/K/V 为什么要分三个向量？Attention 怎么算？为什么除以 √d_k？
- 同一个 Token 在不同位置的向量一样吗？GQA 比 MHA 省多少 KV-Cache？
- Token 怎么切分（BPE）？为什么不直接用字符或单词？中英文/代码消耗为什么不同？
- 上下文窗口由什么决定？是不是越大越好？Lost in the Middle 怎么产生、怎么解？
- 支持更长上下文需要怎样的训练？为什么不直接把窗口做大？
- KV Cache 的作用？为什么 Agent 场景更敏感？PagedAttention / Continuous Batching / vLLM vs SGLang？
- 幻觉是怎么产生的？有哪些缓解方法？
- 大模型的"涌现能力"是什么？CoT 什么条件下才涌现？

### 训练方法
- 🔥 SFT / PPO / DPO / GRPO 分别什么特点？
- PPO 为什么既有 reward model 又有 critic model？
- DPO 为什么不需要在线采样？数据格式？损失函数怎么写？
- GRPO 训练出现全 0 全 1 怎么办？GSPO/DAPO 与 GRPO 区别？
- 为什么有了 SFT 还要 RLHF？RLHF 三阶段的 loss 各怎么定义？
- Agent 训练三阶段 CPT→SFT→RL：🔥 为什么 SFT 时要 mask observation tokens？（字节经典追问）
- LoRA / QLoRA 核心思想？LoRA 超越"减参数"的 4 个优点？矩阵初始化方式？
- Function Call 能力用 GRPO 提升，奖励函数怎么设计？过程奖励怎么加？

### 协议与生态四件套
- 🔥 Function Call 是什么？底层怎么实现？四步流程？Parallel Function Call？
- 🔥 MCP 是什么协议？解决什么问题？和 Function Calling 的本质区别（"三大绝症"）？能共存吗？
- MCP 三种原语 Resources/Tools/Prompts？Resource 和 Tool 什么时候用哪个？
- MCP stdio vs Streamable HTTP？JSON-RPC 怎么工作？notifications 机制？
- 🔥 MCP 2026-07-28 为什么要无状态化？旧 session 协议有什么问题？迁移要改什么？
- Mcp-Method/Mcp-Name HTTP 头解决了什么问题？
- MCP Token 税是什么？你用 MCP 遇到过什么问题、怎么解决？
- 🔥 A2A 和 MCP 的区别？为什么 MCP 解决不了 A2A 的问题？Agent Card 是什么？A2A 三种通信模式、任务状态机？
- MCP / A2A / AG-UI 三协议怎么分类？
- 🔥 Skill、MCP、Tools、Function Calling 四者关系？（2026 标准四件套题）
- 🔥 Skills vs Tool 的 5 个本质区别（8 月最高频）；Skill 是知识库还是融合在 Agent 里？什么阶段 fetch？
- Skills 和 System Prompt / Prompt / Few-shot 的区别？为什么说"Skill 不是更长的 Prompt"？
- 🔥 Rules 和 Skills 的本质区别？为什么 Skill 不能写进 Rules？Instructions+Rules+MCP+Skills 四件套怎么区分？
- 渐进式披露（Progressive Disclosure）是什么？和 RAG 是什么关系？怎么实现？
- SSE vs WebSocket 底层区别？为什么 Agent 用 SSE？
- RPC vs HTTP 本质区别？Agent 通信到底走哪个？

---

## 第 2 层：设计与决策能力（拉开分差）

### 记忆与上下文
- 🔥 Agent Memory 怎么设计？工作/短期/长期/实体记忆分别怎么存、怎么落地？
- 🔥 记忆的滚动更新、摘要压缩、结构化压缩怎么做？多重上下文压缩机制？（蚂蚁网商）
- 🔥 会话很长 Prompt 越来越大怎么处理？除截断外的压缩方法？压缩过度导致效果下降怎么发现？
- 记忆为什么用向量库存储而不是每轮拼进 prompt？记忆和 RAG 知识库共不共用？
- 记忆检索怎么平衡相关性+时近性？注入多少记忆合适？用户反复说同一件事，重复存储还是语义合并？
- 长期记忆怎么避免"记忆污染"？Memory 冷启动问题？
- Context Engineering vs Prompt Engineering vs Harness Engineering 三者关系？
- 上下文腐化（Context Rot）/ 上下文漂移是什么？根因？怎么识别和解决？
- Auto-Compact 在压什么、留什么、丢什么？RAG 能不能替代 Auto-Compact？
- Claude Code 底层记忆原理？CLAUDE.md 为什么作为用户消息注入而不是 System Prompt？
- 为什么 Claude Code 不用 RAG 检索代码而用 grep？什么时候该用 RAG 什么时候该用 Grep？
- To-Do List 机制为什么能让模型更聚焦？怎么落地？

### 工具调用设计
- 🔥 Tool schema 怎么设计？描述为什么要写 What+When+How+Limit？annotations（destructiveHint/readOnlyHint）怎么用？
- 🔥 工具太多时怎么管理？百级工具路由怎么设计？工具选择错误怎么优化？
- 多工具并行调用怎么实现？依赖关系（DAG）怎么处理？
- 工具描述怎么优化才能提升调用准确率？
- 两个工具对同一问题返回格式不统一怎么处理？MCP 工具返回格式不统一怎么办？
- 🔥 大模型输出格式不稳定，工程上怎么约束？约束解码 / JSON Mode / Grammar-based 解码区别？
- Tool 直接暴露给模型还是服务端分发？
- 采用 SFT 或 RL 怎么解决工具调用不准确？
- 什么是工具调用幻觉？类型及解法？

### RAG 设计
- 🔥 RAG 完整链路：从用户提问到答案返回？每个环节为什么这么做？
- 🔥 Chunk 策略怎么设计？大小、重叠率怎么定？不同文档类型（PDF/表格/条款/代码）怎么切？
- 法律文档"第三条第二款"和"第三条之二"会不会切散？
- 小 chunk 检索准但上下文碎，矛盾怎么解决？
- 🔥 检索优化：向量/关键词/混合检索怎么配合？BM25 和向量分数不在一个量纲怎么融合？
- 🔥 Rerank 方案有哪些？为什么初筛后还要 Rerank？TopK 截断值怎么定、验证过吗？
- Query 改写 / HyDE / multi-query 是一回事吗？改写缺信息时怎么让用户补充？
- 🔥 Agentic RAG vs 传统 RAG 核心区别？成本和稳定性怎么控制？
- GraphRAG vs LightRAG vs 传统 RAG？什么场景升级？实体提取不准怎么办？
- RAG 四大新范式（Graph-RAG / Agentic RAG / Memory-Augmented / Retrieval-free）？
- 向量库 vs 传统数据库怎么分工？索引类型 Flat/IVF_PQ/HNSW/DiskANN 选型？IVF_PQ 参数怎么选？
- 余弦相似度 vs 欧氏距离优缺点？工程上怎么存储计算？稠密/稀疏向量混合融合？
- 文档更新后怎么避免全量重建？增量索引怎么设计、有什么坑？embedding 模型升级了怎么办？
- 多模态数据（图片/表格/代码块）怎么处理？
- Prompt 明确要求不返回某商品但模型仍返回且多次出现——原因和解法？
- 为什么先调 Tool 查商品再走 RAG 检索？（小红书原题）
- 多轮对话中省略/指代怎么补全成可检索的问题？

### 架构选型
- 🔥 单 Agent 还是多 Agent？判据？多 Agent 的代价？什么情况不该用 Multi-Agent？
- 🔥 多 Agent 为什么需要中心化编排？Supervisor / Swarm / Hierarchical 三种架构怎么选？
- Multi-Agent 三层架构（Router→Manager→Sub-Agent）？主子 Agent 通信链路？
- 为什么拆多个 Agent？一个 Agent 多挂几个 Tool 不行吗？子 Agent 能不能共享所有工具？
- 子 Agent 为什么能减少上下文污染？子 Agent 上下文和父 Agent 什么关系？
- 🔥 框架自研还是 LangChain/LangGraph？LangGraph 的 State/Node/Edge？为什么不选 AutoGen/CrewAI？
- 框架能力不满足时怎么扩展？选错框架的替换成本？
- OpenClaw 为什么火爆？技术/架构上做对了什么？核心边界和局限？
- OpenClaw vs Nanobot vs NanoClaw？OpenClaw/Hermes/Claude Code 三框架区别？
- Mem0 vs Zep vs Letta 三大记忆框架怎么选？
- LangSmith / LangFuse / Phoenix 观测工具怎么选？
- 为什么 Anthropic 说"不要过早引入 Multi-Agent"？
- 意图识别：四层意图识别是哪四层？为什么不能直接让大模型判断意图？并行化意图识别怎么实现、为什么有必要？
- Agent 处理模糊指令：直接检索 vs 先反问确认？怎么判断 query 模糊还是清晰？

---

## 第 3 层：落地与工程化（SP/SSP 分水岭）

### 稳定性与容错
- 🔥 工具调用失败怎么处理？超时/4xx/5xx/权限分别怎么办？重试怎么写才不烧钱？
- 🔥 死循环怎么防？三层防御（工具层/推理层/系统层）？连续三次返回相同结果的停止条件？
- 🔥 "Agent 调了三个工具就死循环了，异常处理在哪写的？"（字节原话）
- Tool 调用链中某环节超时，如何保证会话不崩？
- ReAct 消息格式怎么设计？tool_response 用 user 还是 assistant 角色传回？为什么？（字节必考）
- 循环终止：硬终止 vs 软终止？Token/时间/步数预算怎么分配？预算耗尽怎么优雅终止？
- Agent 一直在"思考"但从不行动，怎么检测？
- 初始计划错了怎么办？规划失败怎么回退？
- 用户强行中断 Agent 执行怎么办？取消机制三层设计？
- Agent 宕机后能否恢复？检查点机制？状态存哪、为什么？
- 状态机在 Agent 中怎么用？核心状态有哪些？状态怎么持久化？多 Agent 状态竞争怎么避免？
- LLM API 故障时可用性怎么保证？多模型 fallback 路由？模型供应商限流怎么降级？

### 幻觉治理与评测
- 🔥 高准确性场景怎么控制幻觉？幻觉率/引用错误率多少？业务能否接受？
- 🔥 Agent 幻觉六层治理？多 Agent 的"幻觉放大"问题？
- RAG 生成阶段怎么在 Prompt 里设边界防无中生有？拒答阈值怎么定？用户问题文档里完全没有怎么处理？
- 🔥 Badcase 怎么定义？review 拒绝/用户不采纳/高质量样本分别怎么处理？回流 SFT 完整链路？
- Badcase 怎么快速定位是哪个环节？怎么判断该对哪个 Agent 做 SFT？
- 🔥 评测体系：检索/生成/链路/线上四层各用什么指标？没有用户反馈时怎么有效抽检？
- LLM-as-Judge 怎么设计？偏见问题？怎么避免"自己给自己打分偏乐观"？
- 怎么证明新 Prompt 比旧 Prompt 好？如何判断是 Prompt 问题还是模型能力问题？
- Prompt 调到极限后：换模型、调参数还是上 SFT？
- 怎么测 Agent？非确定性测试（每输入跑 5-10 次断言通过率）？Pass@k vs 稳定性指标？
- 离线评测与线上表现差距大怎么办？评测集怎么构造？正负样本？评测模型偏见怎么避免？
- AgentBench / WebArena / SWE-bench / GAIA 各测什么？
- Agent 的 A/B 测试和普通 Web A/B 有什么不同？灰度发布怎么做？Shadow Testing？

### 成本与性能
- 🔥 Token 成本怎么优化？五策略组合？成本大头在哪、哪些优化 ROI 最高？
- 🔥 大数据量高并发下怎么做成本和效率优化？日均十几万请求、5000-6000 人怎么扩容？
- Prompt Caching 原理和效果？新定价怎么减少 Agent 成本？
- 模型路由：大小模型混用？GPT-5.6 Sol/Terra/Luna 三级路由？规则/模型/混合路由优缺点？
- DeepSeek V4 涨价 1100% 对成本路由设计的启示？
- 端到端延迟怎么降？LLM Compiler 范式？
- 高峰期降级和兜底怎么设计？三级容灾？级联降级的坑？
- Agent 成本上限怎么设定、怎么熔断？怎么向老板解释成本？$47K 事故怎么避免？

### 安全与合规
- 🔥 Prompt Injection 攻击手法和四层纵深防御？
- 🔥 最小权限怎么落地？权限怎么分级（低风险自动/高风险审批）？权限升级后怎么保持最小权限？
- HITL 介入时机？哪些操作必须人工确认？审批超时策略？怎么评估 HITL 是否过度？
- "AI 执行删库你还没点取消怎么办？"四层防呆？Cursor 9 秒删库事件的防御设计？
- Agent 操作数据库怎么保证不误删？危险工具怎么防？工具调用注入怎么防？
- OWASP Agentic AI Top 10 前三大风险？
- Agent 的推理过程暴露给用户有什么风险？
- 沙箱边界？Code Interpreter 执行安全？Docker 逃逸/K8s 安全？
- MCP Server 安全治理？鉴权怎么设计？OAuth 授权？
- 隐私：GDPR/个保法删除请求？数据出境合规？用 AI 写代码怎么不泄露公司代码？
- Red Teaming 怎么做？企业级合规（Compliance API）？
- 信通院《即时零售智能体可信基本要求》三个数字：简单任务 >95% / 复杂任务 >90% / 接口成功率 >99%

### 可观测性与运维
- 🔥 Agent 日志怎么设计？Debug 最需要什么信息？（4 问框架：选了哪个 Tool？参数？返回？状态怎么变？）
- Agent Tracing 与微服务 Tracing 有何不同？轨迹评估怎么做？
- 可观测性三大支柱+告警阈值？线上行为退化怎么快速定位？
- 生产环境健康检查应该查什么？Doctor 诊断设计原则？
- Agent 系统怎么部署到 K8s？与传统微服务区别？水平扩展后一致性？
- Agent 压测怎么做？与传统压测区别？峰值 QPS 怎么估算？
- SLO/SLA 怎么定？Agent 可靠性怎么量化？
- 仅 41% Agent 进入生产的原因？企业内部落地最关心哪三个非功能需求？

---

## 场景设计题（用 E85 五步框架追问：约束→架构→正常链路→异常枚举→观测迭代）

- **S1 外卖平台三端智能客服**（美团 Keeta）：B 商户/C 用户/D 骑手。追问：RAG vs Skill 怎么选？为什么订单类用 Skill？三类任务哪些环节调什么工具？
- **S2 企业级智能客服 Agent**（CSDN 三 offer）：产品问答+售后+工单系统。追问 8 连：计划谁审查？Worker 失败谁决定重试？多轮对话状态？幻觉？敏感词？
- **S3 研究型 Agent**（字节二面）：架构？幻觉压到多少？checkpoint/trace 回放？
- **S4 会议转写纪要 Agent**（字节 TikTok）：音视频→文本全链路？ASR 选型对比？隐私保护？中心化 vs 去中心化？
- **S5 高并发降本**：10 万→1000 万日调用，成本不能线性涨。
- **S6 每秒 10w QPS 实时输出最近 1 小时 Top10 访问 IP**（字节后端 Agent 二面最高难度）
- **S7 企业知识库问答系统 / Agent 平台设计**：几十万份文档人工打标不现实怎么办？回答正确性怎么保证？
- **S8 短链接系统**（活动页场景）：怎么生成短链字符串？
- **S9 多实验分组系统**：多实验并行互不影响怎么分配用户？
- **S10 滑动窗口限流**：Redis 用什么数据结构？结构体包含哪些字段？和令牌桶比缺点？
- **S11 TUI 交互式视频剪辑工具 MVP**（字节 OC 版新题型）：分层架构？
- **S12 法律文档检索系统 / 新闻聚合智能问答**：GraphRAG or 传统 RAG，选哪个为什么？

## 手撕代码（43 道全景，按四大支柱）

**支柱一：经典算法（近期原题）**
TopK（美团）· LRU Cache（字节，双向链表+哈希手写）· 合并区间（蚂蚁）· 删除链表倒数第 k 节点（美团 Keeta）· 最长公共子串（字节二面）· 二叉树最大宽度（字节，位置编号法+溢出陷阱）· 合并两个有序链表 · 合并 K 个升序链表 · 搜索旋转数组（快手）

**支柱二：Agent 原理手写（字节面试官原话："手写一个 ReAct 循环，不依赖任何框架，从 system prompt 到 tool_call 解析完整跑通"）**
ReAct 完整循环 + _detect_loop + 异常分类 · Tool 调用超时+重试+降级（指数退避+熔断）· 滑动窗口 + Token 计数器 · 多 Agent 消息总线 · Skills 渐进式披露加载器 · FSM 任务状态机（含 checkpoint）· Agentic RAG 循环控制器（4 层幻觉治理+分层路由）· 多 Agent 死循环防治（架构/逻辑/监控 3 层）· 工具选择路由器 · 并行化意图识别器 · 四层意图识别路由 · 智能客服状态机 · 多轮 Query 改写器 · 两阶段记忆压缩（Dream-like）· Mem0 风格记忆系统（向量+图）· RAG 召回率计算+评测 · LoRA 可插拔加载器 · PreToolUse Hook 确定性规则匹配（删库防御）· AI Gateway 网关治理（限流+熔断+降级+成本统计）· 生产级 MCP Server（Tool 定义+安全校验+风险标注）· Agent Harness 框架（调度+监控+错误隔离）

**支柱三：并发与系统**
高并发 Top N 实时统计 · Python Lock vs RLock 线程安全计数器 · asyncio.gather vs 多线程调 10 个 Embedding 接口的资源差异 · 滑动窗口限流

**支柱四：追问式深挖（写完必被追问）**
"能不能优化到 O(n)？""为什么用 pydantic？""B 为什么初始化为 0？""链表闭环怎么类比 Agent 死循环？"

## 工程基本功兜底（京东/蚂蚁风格，防偏科）

- MySQL：ACID、隔离级别与 ReadView 个数、MVCC、B+ 树 vs B 树、聚簇 vs 非聚簇、联合索引、索引失效（LIKE）、覆盖索引、慢 SQL 排查、海量数据优化
- Redis：缓存穿透/击穿/雪崩、分布式锁、缓存一致性、滑动窗口限流结构
- 消息队列：Kafka 为什么不直连 DB？RocketMQ 持久化？消息结果与请求怎么对应？dead-letter？
- Java：线程池参数、FullGC/OOM 排查命令、锁类型、volatile、HashMap 结构、Java 8→17
- Python：GIL、Lock vs RLock、协程 vs 线程、asyncio 优势与注意点
- 网络：HTTPS 握手、RPC vs HTTP、SSE vs WebSocket
- C++（如候选人是 C++ 背景）：编译链接流程

## 行业视野抽查（按面试月份换弹药，2026-08 版）

- OpenAI 开源 Codex Harness（Apache-2.0，ARC-AGI-3 13.3%→38.3%）意味着什么？与 DeepSeek Harness"安卓 vs iOS"哲学差异？
- 智谱 GLM-5.3 因 1097 个中高危缺陷推迟开源的启示？国产四强（GLM-5.3/Qwen3.8-Max/DeepSeek V4/Kimi K3）选型矩阵？
- 信通院可信标准三数字？中美双轨（vs OpenAI Preparedness Framework）？
- Claude Code subagent forking / @-mention 跨会话通信 / Hook 机制 / Dynamic Workflows？
- OpenClaw 24.8 万 Star 登顶 GitHub 反映什么趋势？fail-closed 策略？requireApproval？
- MCP 无状态化 / MCP Apps / Tasks 一级扩展 / OAuth 零接触授权？
- Anthropic 2 万亿 IPO 估值 / 阿里 800 亿港元全投 AI / 德勤"仅 1/5 美企就绪"——你怎么看 Agent 落地节奏？

## 各厂风格差异（E100，决定我扮演哪种压力模式）

| 厂 | 风格 | 典型压力点 |
|---|---|---|
| **字节** | 追工程细节 | 死循环、异常兜底、token 成本、trace、评估指标，场景题全是"线上出问题了怎么办" |
| **腾讯** | 考协议生态 | MCP 原理、A2A、FC schema 设计、外部工具生态集成 |
| **阿里/蚂蚁** | 求架构格局+工程可靠性 | 多 Agent 编排、平台化、网关治理、并发与成本、分布式锁/缓存一致性 |
| **美团** | 场景落地 | 智能客服三端设计、RAG vs Skill 选型、"线上会怎么处理" |
| **京东** | 后端基本功+大模型应用 | Java/MySQL/中间件占一半，RAG 链路 |
