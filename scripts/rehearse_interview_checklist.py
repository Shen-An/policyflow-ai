"""Interview rehearsal against a running PolicyFlow server (docs/09 §6)."""

from __future__ import annotations

import json
import sys
import time
from pathlib import Path
from typing import Any

import httpx

BASE = "http://127.0.0.1:8000"
CRUD_PATH = r"D:\Coding\Code\Github\CRUD_RAG\data\crud_split\split_merged.json"
ADMIN_USER = "admin"
ADMIN_PASS = "123456"
TIMEOUT = 180.0


class Rehearsal:
    def __init__(self) -> None:
        self.client = httpx.Client(base_url=BASE, timeout=TIMEOUT)
        self.token = ""
        self.results: list[tuple[str, bool, str]] = []

    def record(self, name: str, ok: bool, detail: str = "") -> None:
        self.results.append((name, ok, detail))
        mark = "PASS" if ok else "FAIL"
        print(f"[{mark}] {name}" + (f" — {detail}" if detail else ""))

    def headers(self) -> dict[str, str]:
        return {"Authorization": f"Bearer {self.token}"}

    def login(self) -> None:
        r = self.client.post(
            "/api/auth/login",
            json={"username": ADMIN_USER, "password": ADMIN_PASS},
        )
        ok = r.status_code == 200 and "access_token" in r.json()
        if ok:
            self.token = r.json()["access_token"]
        self.record("env.login", ok, f"status={r.status_code}")

    def health(self) -> None:
        r = self.client.get("/health")
        self.record("env.health", r.status_code == 200, f"status={r.status_code}")

    def models(self) -> None:
        r = self.client.get("/api/settings/model-providers", headers=self.headers())
        if r.status_code != 200:
            self.record("env.chat_provider", False, f"status={r.status_code}")
            self.record("env.embedding_provider", False, f"status={r.status_code}")
            return
        payload = r.json() if isinstance(r.json(), dict) else {}
        chat = payload.get("chat") or {}
        emb = payload.get("embedding") or {}
        self.record(
            "env.chat_provider",
            bool(chat.get("enabled")),
            f"enabled={chat.get('enabled')} model={chat.get('default_chat_model') or chat.get('name')}",
        )
        self.record(
            "env.embedding_provider",
            bool(emb.get("enabled")),
            f"enabled={emb.get('enabled')} model={emb.get('default_embedding_model') or emb.get('default_chat_model') or emb.get('name')}",
        )

    def indexed_docs(self) -> str | None:
        r = self.client.get("/api/knowledge-bases", headers=self.headers())
        if r.status_code != 200:
            self.record("env.knowledge_bases", False, f"status={r.status_code}")
            return None
        items = r.json().get("items") or r.json()
        if isinstance(items, dict):
            items = items.get("items") or []
        eval_test = next(
            (
                kb
                for kb in items
                if kb.get("code") == "eval_test" or kb.get("name") == "测试库"
            ),
            None,
        )
        hr = next((kb for kb in items if kb.get("code") == "hr"), None)
        self.record(
            "env.knowledge_bases",
            bool(items),
            (
                f"count={len(items)} eval_test={eval_test['id'] if eval_test else None} "
                f"hr={hr['id'] if hr else None}"
            ),
        )
        # Business chat demos can use hr; eval import uses dedicated sandbox below.
        return hr["id"] if hr else (items[0]["id"] if items else None)

    def chat(self, question: str, **extra: Any) -> dict[str, Any] | None:
        payload = {
            "question": question,
            "knowledge_base_ids": [],
            "enable_skill": True,
            "query_mode": "hybrid",
            **extra,
        }
        r = self.client.post("/api/chat", headers=self.headers(), json=payload)
        if r.status_code != 200:
            print(" chat error body:", r.text[:500])
            return {"_status": r.status_code, "_body": r.text}
        return r.json()

    def flow_question(self) -> None:
        data = self.chat("差旅申请流程有哪些步骤？")
        if not data or data.get("_status"):
            self.record("chat.flow_question", False, f"status={data}")
            return
        router = data.get("router_result") or {}
        diagnostics = data.get("diagnostics") or {}
        commands = diagnostics.get("commands") or []
        tools = diagnostics.get("tools") or []
        names = [c.get("name") for c in commands]
        fake = [t for t in tools if str(t.get("tool_name", "")).startswith("skill.suggest")]
        answer = data.get("answer") or ""
        need_skill = router.get("need_skill")
        # order check: SkillAgent before AnswerAgent if both present
        order_ok = True
        if "SkillAgent" in names and "AnswerAgent" in names:
            order_ok = names.index("SkillAgent") < names.index("AnswerAgent")
        ok = (
            data.get("confidence_score", 0) is not None
            and need_skill is True
            and order_ok
            and not fake
            and len(answer) > 20
        )
        detail = (
            f"need_skill={need_skill} task={router.get('task_type')} "
            f"order_ok={order_ok} fake_tools={len(fake)} "
            f"cmd={names} conf={data.get('confidence_score')} "
            f"answer_head={answer[:80]!r}"
        )
        self.record("chat.flow_question", ok, detail)
        allow = next((c for c in commands if c.get("name") == "ToolAllowlist"), None)
        self.record(
            "chat.tool_allowlist_diag",
            allow is not None,
            f"allow={allow.get('output') if allow else None}",
        )
        self.record(
            "chat.no_fake_skill_suggest",
            not fake,
            f"fake={fake}",
        )

    def refuse_question(self) -> None:
        data = self.chat(
            "unknown zzzz unmatched galaxy stipend policy 999999",
            retrieval_strategy="lightrag_only",
            query_mode="naive",
            top_k=3,
            enable_skill=False,
        )
        if not data or data.get("_status"):
            self.record("chat.refuse_question", False, f"status={data}")
            return
        compliance = data.get("compliance") or {}
        warnings = compliance.get("warnings") or []
        answer = data.get("answer") or ""
        ok = (
            compliance.get("passed") is False
            and "NO_RELIABLE_EVIDENCE" in warnings
            and data.get("confidence_score") == 0
            and ("没有检索到" in answer or "未检索到" in answer or "无法给出" in answer)
        )
        self.record(
            "chat.refuse_question",
            ok,
            f"passed={compliance.get('passed')} warnings={warnings} conf={data.get('confidence_score')} head={answer[:60]!r}",
        )

    def mcp_stdio_and_mock(self) -> None:
        # Create / reuse stdio demo server
        create = self.client.post(
            "/api/mcp/servers",
            headers=self.headers(),
            json={
                "name": "rehearsal-stdio-demo",
                "type": "external",
                "integration_mode": "stdio",
                "command": f"{sys.executable} -m backend.app.mcp.stdio_demo_server",
                "config": {"timeout_seconds": 20},
                "enabled": True,
            },
        )
        if create.status_code in {200, 201}:
            server_id = create.json()["id"]
            created = True
        else:
            # maybe exists
            listing = self.client.get("/api/mcp/servers", headers=self.headers())
            items = (listing.json().get("items") if listing.status_code == 200 else []) or []
            found = next((i for i in items if i.get("name") == "rehearsal-stdio-demo"), None)
            if found:
                server_id = found["id"]
                created = False
            else:
                self.record(
                    "mcp.stdio_create",
                    False,
                    f"status={create.status_code} body={create.text[:200]}",
                )
                server_id = None
                created = False
        if server_id:
            self.record("mcp.stdio_create", True, f"id={server_id} created_now={created}")
            health = self.client.post(
                f"/api/mcp/servers/{server_id}/health-check",
                headers=self.headers(),
            )
            tools = health.json().get("tools") if health.status_code == 200 else []
            ok = health.status_code == 200 and "echo" in tools and "time_now" in tools
            self.record(
                "mcp.stdio_health",
                ok,
                f"status={health.status_code} health={health.json().get('health_status') if health.status_code==200 else None} tools={tools}",
            )
            call = self.client.post(
                "/api/tools/mcp.call/run",
                headers=self.headers(),
                json={
                    "input": {
                        "server_id": server_id,
                        "tool_name": "echo",
                        "arguments": {"text": "rehearsal-ok"},
                    }
                },
            )
            body = call.json() if call.status_code == 200 else {}
            output = body.get("output") or body
            self.record(
                "mcp.stdio_call_echo",
                call.status_code == 200,
                f"status={call.status_code} output_head={str(output)[:160]}",
            )

        # Ensure mock server path still honest
        mock_create = self.client.post(
            "/api/mcp/servers",
            headers=self.headers(),
            json={
                "name": "rehearsal-mock-office",
                "type": "mock",
                "integration_mode": "mock",
                "config": {"mode": "mock"},
                "enabled": True,
            },
        )
        if mock_create.status_code in {200, 201}:
            mock_id = mock_create.json()["id"]
        else:
            listing = self.client.get("/api/mcp/servers", headers=self.headers())
            items = (listing.json().get("items") if listing.status_code == 200 else []) or []
            found = next((i for i in items if i.get("name") == "rehearsal-mock-office"), None)
            mock_id = found["id"] if found else None
        if not mock_id:
            # use any existing mock
            listing = self.client.get("/api/mcp/servers", headers=self.headers())
            items = (listing.json().get("items") if listing.status_code == 200 else []) or []
            found = next((i for i in items if i.get("integration_mode") == "mock" or i.get("type") == "mock"), None)
            mock_id = found["id"] if found else None
        if mock_id:
            call = self.client.post(
                "/api/tools/mcp.call/run",
                headers=self.headers(),
                json={
                    "input": {
                        "server_id": mock_id,
                        "tool_name": "mcp.email.create_draft",
                        "arguments": {"subject": "rehearsal"},
                    }
                },
            )
            output = (call.json().get("output") if call.status_code == 200 else {}) or {}
            status = output.get("status") if isinstance(output, dict) else None
            self.record(
                "mcp.mock_status_label",
                call.status_code == 200 and status == "mock",
                f"status={call.status_code} output_status={status}",
            )
        else:
            self.record("mcp.mock_status_label", False, "no mock server available")

    def eval_import_run_export(self, kb_id: str | None) -> None:
        # Always import into dedicated sandbox 测试库 (eval_test), never business KBs.
        if not Path(CRUD_PATH).exists():
            self.record("eval.crud_path", False, CRUD_PATH)
            return
        self.record("eval.crud_path", True, CRUD_PATH)

        imp = self.client.post(
            "/api/eval/datasets/crud-import",
            headers=self.headers(),
            json={
                # Omit knowledge_base_id so backend ensures/uses code=eval_test.
                "knowledge_base_id": None,
                "source_path": CRUD_PATH,
                "task_type": "questanswer_1doc",
                "sample_size": 20,
                "create_eval_cases": True,
                "index_documents": True,
                "use_eval_test_kb": True,
                "offset": 0,
            },
            timeout=600.0,
        )
        if imp.status_code not in {200, 201}:
            self.record(
                "eval.crud_import",
                False,
                f"status={imp.status_code} body={imp.text[:300]}",
            )
            return
        body = imp.json()
        sandbox_kb_id = body.get("knowledge_base_id")
        self.record(
            "eval.crud_import",
            body.get("retrieval_items_created", 0) > 0 and bool(sandbox_kb_id),
            (
                f"kb={sandbox_kb_id} docs+{body.get('documents_created')} "
                f"reused={body.get('documents_reused')} "
                f"items+{body.get('retrieval_items_created')} cases+{body.get('eval_cases_created')} "
                f"indexed={body.get('indexed')} failed={body.get('index_failed')}"
            ),
        )
        # Prefer the sandbox KB returned by import for subsequent debug/run steps.
        kb_id = sandbox_kb_id or kb_id
        if not kb_id:
            self.record("eval.run", False, "no sandbox kb_id after import")
            return

        items = self.client.get("/api/eval/retrieval-items", headers=self.headers())
        cases = self.client.get("/api/eval/cases", headers=self.headers())
        item_ids = [i["id"] for i in (items.json() if items.status_code == 200 else [])][:20]
        case_ids = [c["id"] for c in (cases.json() if cases.status_code == 200 else [])][:10]
        if not item_ids:
            self.record("eval.run", False, "no retrieval items")
            return

        run_resp = self.client.post(
            "/api/eval/runs",
            headers=self.headers(),
            json={
                "name": f"rehearsal-{int(time.time())}",
                "case_ids": case_ids[:5],
                "retrieval_item_ids": item_ids,
                "eval_types": ["retrieval"],
                "retrieval_config": {
                    "strategy": "hybrid_lightrag_bm25",
                    "top_k_values": [1, 3, 5],
                    "rerank_enabled": True,
                    "query_mode": "hybrid",
                },
                "compare_strategies": ["hybrid_lightrag_bm25", "bm25_only"],
                "ragas_config": {"enabled": False, "metrics": []},
            },
        )
        if run_resp.status_code not in {200, 201}:
            self.record(
                "eval.run_create",
                False,
                f"status={run_resp.status_code} body={run_resp.text[:300]}",
            )
            return
        run_id = run_resp.json()["id"]
        self.record("eval.run_create", True, f"id={run_id}")

        # poll — hybrid+compare can take several minutes on cold index
        final = None
        for _ in range(180):
            detail = self.client.get(f"/api/eval/runs/{run_id}", headers=self.headers())
            if detail.status_code != 200:
                time.sleep(3)
                continue
            final = detail.json()
            if final.get("status") in {"success", "failed", "skipped"}:
                break
            time.sleep(3)
        if not final:
            self.record("eval.run_finish", False, "no final payload")
            return
        metrics = final.get("metrics") or {}
        self.record(
            "eval.run_finish",
            final.get("status") in {"success", "skipped", "failed"},
            f"status={final.get('status')} metrics_keys={list(metrics)[:12]} mrr={metrics.get('mrr')} hit5={metrics.get('hit_at_5')}",
        )
        comparison = metrics.get("strategy_comparison")
        self.record(
            "eval.strategy_comparison",
            isinstance(comparison, dict) and len(comparison) >= 1,
            f"comparison={comparison}",
        )

        # retrieval debug with rerank to check metadata
        debug = self.client.post(
            "/api/eval/retrieval-debug",
            headers=self.headers(),
            json={
                "query": "差旅住宿标准",
                "knowledge_base_ids": [kb_id],
                "strategy": "hybrid_lightrag_bm25",
                "top_k": 5,
                "rerank_enabled": True,
                "query_mode": "hybrid",
            },
        )
        if debug.status_code == 200:
            payload = debug.json()
            items_dbg = payload.get("items") or []
            method = None
            if items_dbg:
                method = (items_dbg[0].get("metadata") or {}).get("rerank_method")
            self.record(
                "eval.local_rerank_metadata",
                payload.get("rerank_applied") is True and method == "local_lexical_fusion",
                f"rerank_applied={payload.get('rerank_applied')} method={method}",
            )
        else:
            self.record("eval.local_rerank_metadata", False, f"status={debug.status_code}")

        exp_json = self.client.get(
            f"/api/eval/runs/{run_id}/export?format=json",
            headers=self.headers(),
        )
        exp_csv = self.client.get(
            f"/api/eval/runs/{run_id}/export?format=csv",
            headers=self.headers(),
        )
        self.record(
            "eval.export_json",
            exp_json.status_code == 200 and "export_version" in exp_json.text,
            f"status={exp_json.status_code} bytes={len(exp_json.content)}",
        )
        self.record(
            "eval.export_csv",
            exp_csv.status_code == 200 and "run_id" in exp_csv.text,
            f"status={exp_csv.status_code} head={exp_csv.text.splitlines()[0] if exp_csv.text else ''}",
        )

    def code_pointers(self) -> None:
        root = Path("E:/Coding/Code/Python/policyflow-ai")
        files = [
            "backend/app/agents/pipeline.py",
            "backend/app/tools/chat_tools.py",
            "backend/app/evals/retrieval_metrics.py",
            "backend/app/mcp/client.py",
        ]
        missing = [f for f in files if not (root / f).exists()]
        self.record("code.pointers_exist", not missing, f"missing={missing}")

    def summary(self) -> int:
        print("\n===== REHEARSAL SUMMARY =====")
        passed = sum(1 for _, ok, _ in self.results if ok)
        failed = [name for name, ok, _ in self.results if not ok]
        print(f"passed={passed}/{len(self.results)}")
        if failed:
            print("failed:")
            for name in failed:
                detail = next(d for n, _, d in self.results if n == name)
                print(f" - {name}: {detail}")
        out = Path("E:/Coding/Code/Python/policyflow-ai/docs/rehearsal-latest.json")
        out.write_text(
            json.dumps(
                [{"name": n, "ok": ok, "detail": d} for n, ok, d in self.results],
                ensure_ascii=False,
                indent=2,
            ),
            encoding="utf-8",
        )
        print(f"wrote {out}")
        return 0 if not failed else 1


def main() -> int:
    r = Rehearsal()
    print("== docs/09 §6 rehearsal ==")
    r.health()
    r.login()
    r.models()
    kb_id = r.indexed_docs()
    r.flow_question()
    r.refuse_question()
    r.mcp_stdio_and_mock()
    r.eval_import_run_export(kb_id)
    r.code_pointers()
    return r.summary()


if __name__ == "__main__":
    raise SystemExit(main())
