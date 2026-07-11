# PolicyFlow AI

## 项目概述
Enterprise Policy Assistant — 基于 FastAPI + SQLModel + SQLite 的企业内部政策问答与流程助手。

## 技术栈
- **Backend**: FastAPI
- **ORM**: SQLModel + SQLAlchemy
- **Database**: SQLite (可迁移至 PostgreSQL)
- **Python**: 3.11+
- **虚拟环境**: conda (`conda activate policyflow`)

## 目录结构
```
policyflow-ai/
├── backend/
│   └── app/          # FastAPI 应用
├── docs/             # 设计文档
└── tests/            # 测试
```

## 启动
```bash
conda activate policyflow
pip install -e ".[dev]"
uvicorn app.main:app --reload
```

## 关键文件
- `docs/05-development-roadmap.md` — 开发路线图
- `docs/01-architecture-design.md` — 架构设计
- `docs/02-database-design-sqlite.md` — 数据库设计
- `docs/03-api-design.md` — API 设计
- `docs/04-ai-pipeline-rag-eval-design.md` — AI/RAG/Eval 设计
