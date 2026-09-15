"""Prove on the real development database that the bootstrap admin gains a grant."""

import base64
import json
import urllib.request

from sqlmodel import Session, create_engine, select

from backend.app.db.models import User, UserRole, UserRoleGrant

ENGINE_URL = "sqlite:///./policyflow.db"


def decode_claims(token: str) -> dict:
    """Decode a JWT payload without verifying it (this is a display check)."""
    segment = token.split(".")[1]
    padded = segment + "=" * (-len(segment) % 4)
    return dict(json.loads(base64.urlsafe_b64decode(padded).decode()))


engine = create_engine(ENGINE_URL)
with Session(engine) as session:
    admin = session.exec(select(User).where(User.username == "admin")).first()
    if admin is None:
        raise SystemExit("NO ADMIN ROW")
    legacy = session.exec(select(UserRole).where(UserRole.user_id == admin.id)).all()
    grants = session.exec(
        select(UserRoleGrant).where(UserRoleGrant.user_id == admin.id)
    ).all()
    print(f"admin.tenant_id              = {admin.tenant_id}")
    print(f"legacy user_roles links      = {len(legacy)}")
    print(f"user_role_grants (authority) = {len(grants)}")
    for grant in grants:
        print(
            f"  grant tenant={grant.tenant_id} role_id={grant.role_id} "
            f"revoked_at={grant.revoked_at}"
        )

request = urllib.request.Request(
    "http://127.0.0.1:8000/api/auth/login",
    data=json.dumps({"username": "admin", "password": "123456"}).encode(),
    headers={"Content-Type": "application/json"},
    method="POST",
)
with urllib.request.urlopen(request, timeout=20) as response:
    payload = json.loads(response.read().decode())
claims = decode_claims(payload["access_token"])
print(f"login                        = OK")
print(f"token tenant claim           = {claims.get('tenant_id')}")
print(f"token subject                = {claims.get('sub')}")
