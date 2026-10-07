# T137 目标员工可用性研究 — 状态：`[~]` 受阻（诚实标注）

## 结论
Independent Test 的 **automated 部分已真实达成**（见 `README.md` / `e2e-full.log` /
`accessibility.log`）：真实 Electron、small/medium/large 窗口、键盘流程、axe-core 无阻断项，
chat 问答与报销材料审批两条核心流端到端跑通。

**但 SC-010–SC-012 要求真实目标员工参与的可用性研究**（完成率、任务耗时、误操作数、主观评分），
本机为无人值守开发环境，**没有真实目标员工被试**，无法真做。按既定诚实准则
（同 Stage 6 gVisor 运行时、Stage 7 签名生产打包），此项标 `[~]`，**不**以模拟数据冒充真人评分。

## 阻塞
- 无真实目标员工（报销申请人 / 审批人）可参与计时任务与评分。
- 无受控用户研究环境（屏录、同意书、主持人）。

## 已就绪、待真人研究即可执行
- 两条任务脚本已可端到端操作：
  1. 「用键盘完成一次制度问答」（chat：输入→阅读思考过程→查看依据→复制答案）。
  2. 「用键盘完成一次报销材料审批」（workspace 选材料→workflow 查看 tree/preview/diff→approval 核对 target/files/hashes/side-effects/expiry→批准→查看回执）。
- 边界状态可复现（workspace「演示状态场景」：无权限 / 冲突 / 失败），便于观察 recovery 行为。
- 自动化代理指标（可作为可用性研究前的客观下限）：
  - 键盘可达性：composer 可经 Tab 到达并显示可见焦点环（`accessibility.e2e.ts` 真跑通过）。
  - 无遮挡 / 无横向溢出：三档窗口通过布局断言。
  - a11y：axe wcag2a+aa 0 serious/critical。

## 建议补齐方式（有真人时）
- 招募 ≥5 名目标员工，分别计时完成上述两条任务；记录完成率、耗时、误操作、SUS/主观评分；
  屏录与 a11y 走查并存入本目录；据 SC-010–SC-012 阈值判定达成后再移除 `[~]`。
