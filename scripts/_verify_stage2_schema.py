"""Ad-hoc verification of the Stage 2 expand schema (development helper)."""

import psycopg

DSN = "postgresql://policyflow:policyflow-dev@127.0.0.1:55432/policyflow_test"
SQL = {
    "revision": "select version_num from alembic_version",
    "tables": "select count(*) from pg_tables where schemaname='public'",
    "tenants": "select code, name, status from tenants",
    "policies": (
        "select tablename from pg_policies where policyname='tenant_isolation' "
        "order by tablename"
    ),
    "checks": (
        "select conrelid::regclass::text, conname from pg_constraint "
        "where contype='c' and connamespace='public'::regnamespace order by 1,2"
    ),
    "rls_enabled": (
        "select c.relname, c.relrowsecurity, c.relforcerowsecurity from pg_class c "
        "join pg_policies p on p.tablename = c.relname where c.relkind='r' "
        "group by 1,2,3 order by 1"
    ),
    "users_without_tenant": "select count(*) from users where tenant_id is null",
}

with psycopg.connect(DSN) as conn, conn.cursor() as cur:
    for label, statement in SQL.items():
        cur.execute(statement)
        rows = cur.fetchall()
        if label in {"revision", "tables", "users_without_tenant"}:
            print(f"{label}: {rows[0][0]}")
        else:
            print(f"{label}: {len(rows)}")
            for row in rows:
                print("   ", row)
