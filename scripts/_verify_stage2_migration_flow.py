"""Ad-hoc end-to-end verification of the Stage 2 migration flow (dev helper).

Exercises: reset schema -> upgrade 001 -> insert unowned legacy rows -> refuse
enforce -> run backfill -> apply enforce -> verify NOT NULL / RLS FORCE /
per-tenant uniqueness / restricted application role.
"""

import subprocess
import sys
from pathlib import Path

import psycopg

ROOT = Path(__file__).resolve().parents[1]
DSN = "postgresql://policyflow:policyflow-dev@127.0.0.1:55432/policyflow_test"
TEST_URL = "postgresql+psycopg://policyflow:policyflow-dev@127.0.0.1:55432/policyflow_test"
PY = sys.executable


def alembic(*args: str) -> subprocess.CompletedProcess[str]:
    return subprocess.run(
        [PY, "-m", "alembic", *args],
        cwd=ROOT,
        capture_output=True,
        text=True,
        env={
            **__import__("os").environ,
            "DATABASE_URL": TEST_URL,
            "ENVIRONMENT": "test",
            "PYTHONIOENCODING": "utf-8",
        },
        check=False,
    )


def reset() -> None:
    with psycopg.connect(DSN, autocommit=True) as conn, conn.cursor() as cur:
        cur.execute("DROP SCHEMA IF EXISTS public CASCADE")
        cur.execute("CREATE SCHEMA public")


def seed_unowned_rows() -> None:
    """Create pre-tenant conversation/message rows that the backfill must own."""
    with psycopg.connect(DSN, autocommit=True) as conn, conn.cursor() as cur:
        cur.execute(
            "INSERT INTO users "
            "(id, tenant_id, username, email, password_hash, display_name, status, "
            " created_at, updated_at) "
            "VALUES ('user-1', NULL, 'legacy-user', 'legacy@example.com', 'x', "
            "        'Legacy User', 'active', now(), now()) "
            "ON CONFLICT DO NOTHING"
        )
        cur.execute(
            "INSERT INTO conversations "
            "(id, tenant_id, user_id, title, channel, status, created_at, updated_at) "
            "VALUES ('conv-1', NULL, 'user-1', 'Legacy chat', 'web', 'active', now(), now()) "
            "ON CONFLICT DO NOTHING"
        )
        cur.execute(
            "INSERT INTO messages "
            "(id, tenant_id, conversation_id, role, content, meta_json, created_at) "
            "VALUES ('msg-1', NULL, 'conv-1', 'user', 'hello', '{}'::json, now()) "
            "ON CONFLICT DO NOTHING"
        )
        cur.execute(
            "INSERT INTO eval_cases "
            "(id, question, category, expected_answer_keywords, "
            " expected_source_documents, expected_chunk_ids, should_answer, enabled, created_at) "
            "VALUES ('case-1', 'legacy question', 'policy', '[]'::json, '[]'::json, "
            "        '[]'::json, true, true, now()) "
            "ON CONFLICT DO NOTHING"
        )


def report(label: str, statement: str) -> None:
    with psycopg.connect(DSN, autocommit=True) as conn, conn.cursor() as cur:
        cur.execute(statement)
        print(f"{label}: {cur.fetchall()}")


def main() -> int:
    print("== 1. reset + upgrade 001 ==")
    reset()
    result = alembic("upgrade", "001")
    print("   rc:", result.returncode, result.stderr.strip().splitlines()[-1:])

    print("== 2. seed unowned legacy rows ==")
    seed_unowned_rows()
    report("   null-tenant rows", "SELECT count(*) FROM conversations WHERE tenant_id IS NULL")

    print("== 3. enforce must REFUSE before the backfill ==")
    refused = alembic("upgrade", "head")
    print("   rc:", refused.returncode)
    detail = (refused.stderr or "") + (refused.stdout or "")
    marker = [line for line in detail.splitlines() if "enforce refused" in line or "RuntimeError" in line]
    print("   refusal evidence:", marker[:2] or "NONE (unexpected)")

    print("== 4. run the backfill ==")
    backfill = subprocess.run(
        [PY, "-m", "migrations.backfill_legacy_tenant"],
        cwd=ROOT,
        capture_output=True,
        text=True,
        env={
            **__import__("os").environ,
            "DATABASE_URL": TEST_URL,
            "ENVIRONMENT": "test",
            "PYTHONIOENCODING": "utf-8",
        },
        check=False,
    )
    print("   rc:", backfill.returncode)
    print("   tail:", (backfill.stdout or "").strip().splitlines()[-4:])
    if backfill.returncode != 0:
        print("   stderr:", (backfill.stderr or "").strip().splitlines()[-6:])

    print("== 5. ledger reconciliation ==")
    report(
        "   ledger",
        "SELECT table_name, status, source_count, target_count, failure_count "
        "FROM migration_backfill_state WHERE status <> 'completed'",
    )

    print("== 6. apply enforce ==")
    enforced = alembic("upgrade", "head")
    print("   rc:", enforced.returncode)
    if enforced.returncode != 0:
        print("   stderr:", (enforced.stderr or "").strip().splitlines()[-8:])
        return 1

    report("   revision", "SELECT version_num FROM alembic_version")
    report(
        "   nullable tenant_id columns",
        "SELECT table_name FROM information_schema.columns "
        "WHERE table_schema='public' AND column_name='tenant_id' AND is_nullable='YES'",
    )
    report(
        "   tenant-scoped tables without ENABLED+FORCED RLS",
        "SELECT c.relname FROM pg_class c JOIN pg_namespace n ON n.oid=c.relnamespace "
        "WHERE n.nspname='public' AND c.relkind='r' "
        "AND EXISTS (SELECT 1 FROM information_schema.columns col "
        "            WHERE col.table_schema='public' AND col.table_name=c.relname "
        "              AND col.column_name='tenant_id') "
        "AND NOT (c.relrowsecurity AND c.relforcerowsecurity)",
    )
    report(
        "   tenant-scoped tables without an RLS policy",
        "SELECT c.relname FROM pg_class c JOIN pg_namespace n ON n.oid=c.relnamespace "
        "WHERE n.nspname='public' AND c.relkind='r' "
        "AND EXISTS (SELECT 1 FROM information_schema.columns col "
        "            WHERE col.table_schema='public' AND col.table_name=c.relname "
        "              AND col.column_name='tenant_id') "
        "AND NOT EXISTS (SELECT 1 FROM pg_policies p "
        "                WHERE p.schemaname='public' AND p.tablename=c.relname)",
    )
    report(
        "   per-tenant unique",
        "SELECT conname FROM pg_constraint WHERE contype='u' AND conname LIKE 'uq_%_tenant_%' "
        "ORDER BY conname",
    )
    report(
        "   app role flags",
        "SELECT rolname, rolbypassrls, rolsuper FROM pg_roles WHERE rolname='policyflow_app'",
    )

    print("== 7. RLS actually constrains the application role ==")
    legacy = "00000000-0000-0000-0000-000000000001"
    other = "00000000-0000-0000-0000-0000000000ff"
    with psycopg.connect(DSN, autocommit=True) as conn, conn.cursor() as cur:
        cur.execute("SET ROLE policyflow_app")
        cur.execute("SELECT current_user")
        print("   current_user:", cur.fetchone()[0])

        def count(tenant: str | None) -> int:
            # ``SET`` cannot take a bind parameter, and ``SET LOCAL`` is a no-op
            # under autocommit (which would make every count read 0 for the
            # wrong reason), so the session GUC is driven through set_config.
            cur.execute(
                "SELECT set_config('policyflow.tenant_id', %s, false)",
                (tenant or "",),
            )
            cur.execute("SELECT count(*) FROM conversations")
            return cur.fetchone()[0]

        print("   as legacy tenant (expect 1):", count(legacy))
        print("   as an unrelated tenant (expect 0):", count(other))
        print("   with no tenant set (expect 0):", count(None))
        cur.execute("RESET ROLE")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
