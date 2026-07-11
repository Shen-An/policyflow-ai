"""Frontend F6 contracts for Skill, Tool log, and MCP administration APIs."""

import json
import sqlite3
from pathlib import Path
from uuid import uuid4

from fastapi import FastAPI
from fastapi.testclient import TestClient
from sqlmodel import Session, select

from backend.app.core.config import Settings
from backend.app.db.init_db import initialize_database
from backend.app.db.models import AuditLog, MCPServer, ToolCallLog
from backend.app.db.session import build_engine
from backend.app.main import create_app


def build_f6_app(tmp_path: Path) -> FastAPI:
    settings = Settings(
        DATABASE_URL=f"sqlite:///{(tmp_path / 'f6-contract.db').as_posix()}",
        LOG_DIR=tmp_path / "logs",
        UPLOAD_DIR=tmp_path / "uploads",
        RAG_WORKSPACE_DIR=tmp_path / "rag",
        SECRET_KEY="f6-encryption-secret",
        BOOTSTRAP_ADMIN_PASSWORD="test-password",
        _env_file=None,
    )
    return create_app(settings)


def login(client: TestClient, username: str, password: str) -> dict[str, str]:
    response = client.post(
        "/api/auth/login",
        json={"username": username, "password": password},
    )
    assert response.status_code == 200
    return {"Authorization": f"Bearer {response.json()['access_token']}"}


def create_user(
    client: TestClient,
    admin_headers: dict[str, str],
    username: str,
    role_codes: list[str],
) -> dict[str, str]:
    departments = client.get("/api/departments", headers=admin_headers).json()["items"]
    department_id = next(item["id"] for item in departments if item["code"] == "hr")
    response = client.post(
        "/api/users",
        headers=admin_headers,
        json={
            "username": username,
            "email": f"{username}@example.com",
            "display_name": username,
            "password": "user-password",
            "department_id": department_id,
            "role_codes": role_codes,
        },
    )
    assert response.status_code == 201
    return login(client, username, "user-password")


def create_mock_mcp(
    client: TestClient,
    admin_headers: dict[str, str],
    name: str = "f6_mock",
) -> dict[str, object]:
    response = client.post(
        "/api/mcp/servers",
        headers=admin_headers,
        json={
            "name": name,
            "type": "mock",
            "integration_mode": "mock",
            "command": "python mock.py --token command-secret",
            "config": {
                "mode": "mock",
                "password": "database-secret",
                "nested": {
                    "authorization": "Bearer nested-secret",
                    "safe": "visible",
                },
            },
            "enabled": True,
        },
    )
    assert response.status_code == 201
    return response.json()


def test_skill_contract_schema_runnable_permissions_and_audit(tmp_path: Path) -> None:
    app = build_f6_app(tmp_path)

    with TestClient(app) as client:
        admin_headers = login(client, "admin", "test-password")
        employee_headers = create_user(client, admin_headers, "f6skillemployee", ["employee"])

        unauthenticated = client.get("/api/skills")
        employee_list = client.get("/api/skills", headers=employee_headers)
        employee_disable = client.post(
            "/api/skills/summary/disable",
            headers=employee_headers,
        )
        invalid_input = client.post(
            "/api/skills/summary/run",
            headers=employee_headers,
            json={"input": {}},
        )
        unimplemented = client.post(
            "/api/skills/knowledge_qa/run",
            headers=employee_headers,
            json={"input": {}},
        )
        disable = client.post(
            "/api/skills/summary/disable",
            headers={**admin_headers, "X-Request-ID": "f6-skill-disable"},
        )
        disabled_run = client.post(
            "/api/skills/summary/run",
            headers=employee_headers,
            json={"input": {"text": "First. Second."}},
        )
        enable = client.post(
            "/api/skills/summary/enable",
            headers=admin_headers,
        )
        successful_run = client.post(
            "/api/skills/summary/run",
            headers={**employee_headers, "X-Request-ID": "f6-skill-run"},
            json={"input": {"text": "First. Second."}},
        )
        missing = client.post(
            "/api/skills/missing/run",
            headers=employee_headers,
            json={"input": {}},
        )

    assert unauthenticated.status_code == 401
    assert employee_list.status_code == 200
    skills = {item["name"]: item for item in employee_list.json()["items"]}
    assert len(skills) == 7
    assert {name for name, item in skills.items() if item["implemented"]} == {
        "process_checklist",
        "policy_compare",
        "summary",
    }
    assert skills["summary"]["runnable"] is True
    assert "properties" in skills["summary"]["input_schema"]
    assert skills["knowledge_qa"]["runnable"] is False
    assert skills["knowledge_qa"]["input_schema"] == {}
    assert employee_disable.status_code == 403
    assert invalid_input.status_code == 422
    assert invalid_input.json()["error"]["code"] == "SKILL_INPUT_INVALID"
    assert unimplemented.status_code == 501
    assert unimplemented.json()["error"]["code"] == "SKILL_NOT_IMPLEMENTED"
    assert disable.status_code == 200
    assert disable.json()["enabled"] is False
    assert disable.json()["runnable"] is False
    assert disabled_run.status_code == 409
    assert enable.status_code == 200
    assert successful_run.status_code == 200
    assert successful_run.json()["audit_id"]
    assert successful_run.json()["request_id"] == "f6-skill-run"
    assert missing.status_code == 404

    with Session(app.state.engine) as session:
        audits = session.exec(
            select(AuditLog).where(
                AuditLog.action.in_(["skill.enable", "skill.disable", "skill.run"])
            )
        ).all()

    assert {item.action for item in audits} == {
        "skill.enable",
        "skill.disable",
        "skill.run",
    }
    run_audit = next(item for item in audits if item.action == "skill.run")
    assert run_audit.id == successful_run.json()["audit_id"]
    assert run_audit.request_id == "f6-skill-run"


def test_tool_log_contract_redaction_pagination_detail_and_permissions(
    tmp_path: Path,
) -> None:
    app = build_f6_app(tmp_path)

    with TestClient(app) as client:
        admin_headers = login(client, "admin", "test-password")
        employee_headers = create_user(client, admin_headers, "f6toolemployee", ["employee"])
        server = create_mock_mcp(client, admin_headers)

        unauthenticated = client.get("/api/tool-call-logs")
        forbidden = client.get("/api/tool-call-logs", headers=employee_headers)
        success = client.post(
            "/api/tools/mcp.call/run",
            headers={**admin_headers, "X-Request-ID": "f6-tool-success"},
            json={
                "input": {
                    "server_id": server["id"],
                    "tool_name": "mcp.email.create_draft",
                    "arguments": {
                        "subject": "Travel",
                        "password": "tool-password",
                        "nested": {"api_key": "tool-api-key", "safe": "visible"},
                    },
                }
            },
        )
        failed = client.post(
            "/api/tools/mcp.call/run",
            headers={**admin_headers, "X-Request-ID": "f6-tool-failed"},
            json={
                "input": {
                    "server_id": server["id"],
                    "tool_name": "mcp.missing",
                    "arguments": {"authorization": "Bearer failure-secret"},
                }
            },
        )
        first_page = client.get(
            "/api/tool-call-logs?tool_name=mcp.call&page=1&page_size=1",
            headers=admin_headers,
        )
        successful_logs = client.get(
            "/api/tool-call-logs?status=success",
            headers=admin_headers,
        )
        failed_logs = client.get(
            "/api/tool-call-logs?status=failed",
            headers=admin_headers,
        )
        detail = client.get(
            f"/api/tool-call-logs/{success.json()['call_log_id']}",
            headers=admin_headers,
        )
        missing = client.get(
            f"/api/tool-call-logs/{uuid4()}",
            headers=admin_headers,
        )
        malformed = client.get(
            "/api/tool-call-logs/not-a-uuid",
            headers=admin_headers,
        )
        invalid_page = client.get(
            "/api/tool-call-logs?page=0",
            headers=admin_headers,
        )

    assert unauthenticated.status_code == 401
    assert forbidden.status_code == 403
    assert success.status_code == 200
    assert success.json()["request_id"] == "f6-tool-success"
    assert failed.status_code == 404
    assert first_page.status_code == 200
    assert first_page.json()["total"] == 2
    assert first_page.json()["page"] == 1
    assert first_page.json()["page_size"] == 1
    assert len(first_page.json()["items"]) == 1
    assert successful_logs.json()["total"] == 1
    assert failed_logs.json()["total"] == 1
    assert "failure-secret" not in failed_logs.text
    assert failed_logs.json()["items"][0]["error_message"] == (
        "MCP_TOOL_NOT_FOUND: MCP mock tool not found"
    )
    assert detail.status_code == 200
    assert detail.json()["request_id"] == "f6-tool-success"
    assert detail.json()["conversation_id"] is None
    assert detail.json()["input_summary"]["arguments"]["password"] == "[REDACTED]"
    assert detail.json()["input_summary"]["arguments"]["nested"] == {
        "api_key": "[REDACTED]",
        "safe": "visible",
    }
    assert detail.json()["output_summary"]["input"]["password"] == "[REDACTED]"
    assert detail.json()["output_summary"]["input"]["nested"]["api_key"] == "[REDACTED]"
    assert missing.status_code == 404
    assert malformed.status_code == 422
    assert invalid_page.status_code == 422

    with Session(app.state.engine) as session:
        logs = session.exec(select(ToolCallLog)).all()

    serialized_logs = json.dumps(
        [item.model_dump(mode="json") for item in logs],
        ensure_ascii=False,
    )
    assert "tool-password" not in serialized_logs
    assert "tool-api-key" not in serialized_logs
    assert "failure-secret" not in serialized_logs


def test_mcp_contract_encrypted_storage_edit_health_and_audit(tmp_path: Path) -> None:
    app = build_f6_app(tmp_path)

    with TestClient(app) as client:
        admin_headers = login(client, "admin", "test-password")
        employee_headers = create_user(client, admin_headers, "f6mcpemployee", ["employee"])

        unauthenticated = client.get("/api/mcp/servers")
        forbidden = client.get("/api/mcp/servers", headers=employee_headers)
        invalid_endpoint = client.post(
            "/api/mcp/servers",
            headers=admin_headers,
            json={
                "name": "invalid_http",
                "type": "external",
                "integration_mode": "http",
                "endpoint": "https://user:password@example.com/mcp?token=secret",
            },
        )
        mock_server = create_mock_mcp(client, admin_headers)
        server_id = str(mock_server["id"])
        before_health = client.get("/api/mcp/servers", headers=admin_headers)
        health = client.post(
            f"/api/mcp/servers/{server_id}/health-check",
            headers={**admin_headers, "X-Request-ID": "f6-mcp-health"},
        )
        after_health = client.get("/api/mcp/servers", headers=admin_headers)
        update = client.patch(
            f"/api/mcp/servers/{server_id}",
            headers={**admin_headers, "X-Request-ID": "f6-mcp-update"},
            json={
                "name": "f6_mock_updated",
                "config": {
                    "mode": "mock",
                    "secret": "updated-secret",
                    "safe": "updated-visible",
                },
                "enabled": False,
            },
        )
        external = client.post(
            "/api/mcp/servers",
            headers=admin_headers,
            json={
                "name": "external_http",
                "type": "external",
                "integration_mode": "http",
                "endpoint": "https://mcp.example.com/api",
                "config": {"api_key": "external-secret"},
                "enabled": False,
            },
        )
        external_health = client.post(
            f"/api/mcp/servers/{external.json()['id']}/health-check",
            headers=admin_headers,
        )
        duplicate = client.post(
            "/api/mcp/servers",
            headers=admin_headers,
            json={
                "name": "f6_mock_updated",
                "type": "mock",
                "integration_mode": "mock",
            },
        )
        missing_update = client.put(
            f"/api/mcp/servers/{uuid4()}",
            headers=admin_headers,
            json={"enabled": True},
        )
        malformed_update = client.patch(
            "/api/mcp/servers/not-a-uuid",
            headers=admin_headers,
            json={"enabled": True},
        )
        openapi = client.get("/openapi.json").json()

    assert unauthenticated.status_code == 401
    assert forbidden.status_code == 403
    assert invalid_endpoint.status_code == 422
    assert mock_server["type"] == "mock"
    assert mock_server["integration_mode"] == "mock"
    assert mock_server["command_configured"] is True
    assert "command" not in mock_server
    assert "config" not in mock_server
    assert mock_server["config_summary"]["password"] == "[REDACTED]"
    assert mock_server["config_summary"]["nested"] == {
        "authorization": "[REDACTED]",
        "safe": "visible",
    }
    listed_before = next(
        item for item in before_health.json()["items"] if item["id"] == server_id
    )
    assert listed_before["tools"] == []
    assert health.status_code == 200
    assert health.json()["health_status"] == "healthy"
    assert health.json()["checked_at"]
    assert health.json()["error_code"] is None
    assert "mcp.email.create_draft" in health.json()["tools"]
    listed_after = next(
        item for item in after_health.json()["items"] if item["id"] == server_id
    )
    assert listed_after["tools"] == health.json()["tools"]
    assert listed_after["last_checked_at"] == health.json()["checked_at"]
    assert update.status_code == 200
    assert update.json()["name"] == "f6_mock_updated"
    assert update.json()["enabled"] is False
    assert update.json()["health_status"] == "unknown"
    assert update.json()["config_summary"] == {
        "mode": "mock",
        "secret": "[REDACTED]",
        "safe": "updated-visible",
    }
    assert external.status_code == 201
    assert external.json()["endpoint"] == "https://mcp.example.com/api"
    assert external.json()["config_summary"]["api_key"] == "[REDACTED]"
    assert external_health.status_code == 200
    assert external_health.json()["health_status"] == "unhealthy"
    assert external_health.json()["error_code"] == "MCP_EXTERNAL_NOT_CONFIGURED"
    assert external_health.json()["error_message"]
    assert duplicate.status_code == 409
    assert missing_update.status_code == 404
    assert malformed_update.status_code == 422
    assert "put" in openapi["paths"]["/api/mcp/servers/{server_id}"]
    assert "patch" in openapi["paths"]["/api/mcp/servers/{server_id}"]
    assert "/api/tool-call-logs/{log_id}" in openapi["paths"]

    with Session(app.state.engine) as session:
        stored_mock = session.get(MCPServer, server_id)
        stored_external = session.get(MCPServer, external.json()["id"])
        audits = session.exec(
            select(AuditLog).where(
                AuditLog.action.in_(
                    [
                        "mcp.server.create",
                        "mcp.server.update",
                        "mcp.server.health_check",
                    ]
                )
            )
        ).all()

    assert stored_mock is not None
    assert stored_external is not None
    stored_payload = json.dumps(
        {
            "mock_command": stored_mock.command,
            "mock_config": stored_mock.config,
            "external_config": stored_external.config,
        },
        ensure_ascii=False,
    )
    for secret in (
        "command-secret",
        "database-secret",
        "nested-secret",
        "updated-secret",
        "external-secret",
    ):
        assert secret not in stored_payload
    assert stored_mock.command.startswith("enc:v1:")
    assert {item.action for item in audits} == {
        "mcp.server.create",
        "mcp.server.update",
        "mcp.server.health_check",
    }
    assert next(
        item for item in audits if item.action == "mcp.server.update"
    ).request_id == "f6-mcp-update"


def test_existing_sqlite_schema_is_migrated_and_mcp_secrets_are_protected(
    tmp_path: Path,
) -> None:
    database_path = tmp_path / "legacy.db"
    connection = sqlite3.connect(database_path)
    connection.executescript(
        """
        CREATE TABLE audit_logs (
            id VARCHAR(36) PRIMARY KEY,
            actor_id VARCHAR(36),
            action VARCHAR(100),
            target_type VARCHAR(100),
            target_id VARCHAR(36),
            detail JSON NOT NULL,
            ip_address VARCHAR(64),
            created_at DATETIME NOT NULL
        );
        CREATE TABLE eval_runs (
            id VARCHAR(36) PRIMARY KEY,
            name VARCHAR(255),
            status VARCHAR(20),
            total_cases INTEGER,
            started_at DATETIME,
            finished_at DATETIME,
            metrics JSON NOT NULL,
            config_snapshot JSON NOT NULL,
            created_by VARCHAR(36),
            created_at DATETIME NOT NULL
        );
        CREATE TABLE eval_results (
            id VARCHAR(36) PRIMARY KEY,
            eval_run_id VARCHAR(36),
            eval_case_id VARCHAR(36),
            retrieval_eval_item_id VARCHAR(36),
            question TEXT,
            answer TEXT,
            retrieved_sources JSON NOT NULL,
            retrieval_metrics JSON,
            ragas_metrics JSON,
            score FLOAT,
            passed BOOLEAN,
            error_message TEXT,
            latency_ms INTEGER,
            created_at DATETIME NOT NULL
        );
        CREATE TABLE tool_call_logs (
            id VARCHAR(36) PRIMARY KEY,
            conversation_id VARCHAR(36),
            agent_name VARCHAR(100),
            tool_name VARCHAR(100),
            user_id VARCHAR(36),
            input_summary JSON NOT NULL,
            output_summary JSON NOT NULL,
            status VARCHAR(20),
            error_message TEXT,
            latency_ms INTEGER,
            created_at DATETIME NOT NULL
        );
        CREATE TABLE mcp_servers (
            id VARCHAR(36) PRIMARY KEY,
            name VARCHAR(100) UNIQUE,
            command VARCHAR(500),
            config JSON NOT NULL,
            enabled BOOLEAN,
            health_status VARCHAR(20),
            last_checked_at DATETIME,
            created_at DATETIME NOT NULL,
            updated_at DATETIME NOT NULL
        );
        INSERT INTO tool_call_logs (
            id, agent_name, tool_name, input_summary, output_summary,
            status, error_message, latency_ms, created_at
        ) VALUES (
            'legacy-tool-log', 'manual', 'legacy.tool',
            '{"password":"legacy-tool-password"}',
            '{"nested":{"authorization":"legacy-tool-token"}}',
            'failed', 'legacy-tool-secret-error', 1, CURRENT_TIMESTAMP
        );
        INSERT INTO mcp_servers (
            id, name, command, config, enabled, health_status, created_at, updated_at
        ) VALUES (
            'legacy-mcp', 'legacy', 'python legacy.py --token legacy-command-secret',
            '{"mode":"mock","password":"legacy-password"}',
            1, 'unknown', CURRENT_TIMESTAMP, CURRENT_TIMESTAMP
        );
        """
    )
    connection.commit()
    connection.close()
    settings = Settings(
        DATABASE_URL=f"sqlite:///{database_path.as_posix()}",
        LOG_DIR=tmp_path / "logs",
        SECRET_KEY="legacy-migration-secret",
        _env_file=None,
    )
    engine = build_engine(settings.DATABASE_URL)

    initialize_database(engine, settings)

    connection = sqlite3.connect(database_path)
    columns = {
        table: {
            row[1]
            for row in connection.execute(f"PRAGMA table_info({table})").fetchall()
        }
        for table in (
            "audit_logs",
            "eval_runs",
            "eval_results",
            "tool_call_logs",
            "mcp_servers",
        )
    }
    stored_command, stored_config = connection.execute(
        "SELECT command, config FROM mcp_servers WHERE id = 'legacy-mcp'"
    ).fetchone()
    stored_tool_input, stored_tool_output, stored_tool_error = connection.execute(
        """
        SELECT input_summary, output_summary, error_message
        FROM tool_call_logs WHERE id = 'legacy-tool-log'
        """
    ).fetchone()
    connection.close()

    assert "request_id" in columns["audit_logs"]
    assert {"error_summary", "request_id"} <= columns["eval_runs"]
    assert {"answer_metrics", "type_statuses"} <= columns["eval_results"]
    assert "request_id" in columns["tool_call_logs"]
    assert {
        "server_type",
        "integration_mode",
        "endpoint",
        "tools",
        "last_error_code",
        "last_error_message",
    } <= columns["mcp_servers"]
    assert stored_command.startswith("enc:v1:")
    assert "legacy-command-secret" not in stored_command
    assert "legacy-password" not in stored_config
    assert "legacy-tool-password" not in stored_tool_input
    assert "legacy-tool-token" not in stored_tool_output
    assert stored_tool_error == "Legacy tool execution failed"
