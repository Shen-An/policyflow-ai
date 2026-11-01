# T152 消费者清单（只读 preflight，2026-10-21）

生成脚本：`scripts/t152_legacy_column_consumers.py`（只读，不改任何 schema / 数据）
原始结果：`artifacts/acceptance/t152-consumers.json`
扫描根：`backend/`（排除 6 个路径）

## 一句话结论

数据权威切换的两个门都**没有**清：

- **列门（T152 自身要清）**：`columns_gate_cleared = false`，还剩 **1 处**直接读 legacy 列。
- **路径门（T159/T160 删除要清）**：`path_gate_cleared = false`，还剩 **43 处**引用 legacy `Draft` 模型 / `drafts` 表。

所以「切换权威数据源」现在仍然不能做：destructive contract 的前置条件（消费者清零 + 恢复点）都还不成立。

## 总数

| 指标 | 数量 |
| --- | --- |
| 命中总行数 | 45 |
| legacy `Draft` 路径消费者 | 43 |
| legacy 列消费者 | 1 |
| 需人工复核（未分类噪声） | 0 |
| `faq_draft`（同名噪声，与 legacy Draft 无关） | 2 |

## 列门：1 处（T152 需要先处理）

| 文件:行 | 代码 |
| --- | --- |
| `backend/app/services/draft_service.py:87` | `statement = statement.where(Draft.draft_type == draft_type)` |

待收缩的三列：`draft_type` / `related_sources` / `source_question`。

关键发现：**三列里只有 `draft_type` 还有代码在读**，`related_sources` 和 `source_question` 已经没有读取点。也就是说 T152 的列级 contract 收敛面比原先记录的「45 处引用」小得多——45 这个数字里绝大部分是 *path* 引用，不是列引用。

## 路径门：43 处（T159/T160 删除工作的真实范围）

| 文件 | 命中行数 |
| --- | --- |
| `backend/app/services/draft_service.py` | 20 |
| `backend/app/services/draft_projection.py` | 14 |
| `backend/app/observability/compatibility.py` | 3 |
| `backend/app/api/routes_draft.py` | 2 |
| `backend/app/db/base.py` | 2 |
| `backend/app/db/init_db.py` | 1 |
| `backend/app/schemas/draft.py` | 1 |

`draft_projection.py` 的 14 处属于 **T108 的只读兼容投影**（有意的计数读路径），不是待删代码；真正要删的是 `routes_draft.py` / `draft_service.py` / `schemas/draft.py` 这条写入链，加上 `db/base.py` + `db/init_db.py` 的表与权限注册、`main.py` 的路由与工具注册、`compatibility.py` 的指标。

## 同名噪声（脚本已分类，不算消费者）

`faq_draft`（2 处，`backend/app/api/routes_faq.py` 的 `/api/faq-drafts` 路由与前缀）、`material_source_type`、`mock_connector`、`change_set_state`、`task_taxonomy`、`upload_saga_local_var` —— 这些在第一次宽正则扫描时全部混进「45 处引用」里，导致历史结论高估了 legacy Draft 的实际耦合度。分类后 `needs_review = 0`。

## 复跑

```bash
python scripts/t152_legacy_column_consumers.py   # 有人类可读清单 + exit 1（存在消费者）
```

`gate_cleared` 只有两个门都为 true 才为真；有 legacy 消费者时脚本 exit 1，可直接当 CI 门用。
