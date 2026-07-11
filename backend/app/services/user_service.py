"""User management and serialization services."""

from sqlalchemy.exc import IntegrityError
from sqlalchemy.sql import or_
from sqlmodel import Session, col, select

from backend.app.core.exceptions import ConflictError, NotFoundError
from backend.app.core.security import hash_password
from backend.app.db.models import Department, Role, User, UserRole, utc_now
from backend.app.schemas.user import DepartmentRead, UserCreate, UserListResponse, UserRead


def get_user_by_username(session: Session, username: str) -> User | None:
    return session.exec(select(User).where(User.username == username)).first()


def get_user_by_id(session: Session, user_id: str) -> User:
    user = session.get(User, user_id)
    if user is None:
        raise NotFoundError("User not found", {"user_id": user_id})
    return user


def get_user_role_codes(session: Session, user_id: str) -> list[str]:
    links = session.exec(select(UserRole).where(UserRole.user_id == user_id)).all()
    roles = [session.get(Role, link.role_id) for link in links]
    return sorted(role.code for role in roles if role is not None)


def to_user_read(session: Session, user: User) -> UserRead:
    department = session.get(Department, user.department_id) if user.department_id else None
    department_read = (
        DepartmentRead(id=department.id, name=department.name) if department is not None else None
    )
    return UserRead(
        id=user.id,
        username=user.username,
        email=user.email,
        display_name=user.display_name,
        department=department_read,
        roles=get_user_role_codes(session, user.id),
        status=user.status,
        created_at=user.created_at,
        updated_at=user.updated_at,
    )


def _resolve_roles(session: Session, role_codes: list[str]) -> list[Role]:
    roles = session.exec(select(Role).where(col(Role.code).in_(role_codes))).all()
    found_codes = {role.code for role in roles}
    missing_codes = sorted(set(role_codes) - found_codes)
    if missing_codes:
        raise NotFoundError("Role not found", {"role_codes": missing_codes})
    return list(roles)


def create_user(session: Session, data: UserCreate) -> UserRead:
    existing_username = session.exec(
        select(User).where(User.username == data.username)
    ).first()
    if existing_username is not None:
        raise ConflictError("USER_USERNAME_EXISTS", "Username already exists")

    existing_email = session.exec(select(User).where(User.email == data.email)).first()
    if existing_email is not None:
        raise ConflictError("USER_EMAIL_EXISTS", "Email already exists")

    if data.department_id is not None and session.get(Department, data.department_id) is None:
        raise NotFoundError("Department not found", {"department_id": data.department_id})

    roles = _resolve_roles(session, data.role_codes)
    user = User(
        username=data.username,
        email=data.email,
        password_hash=hash_password(data.password),
        display_name=data.display_name,
        department_id=data.department_id,
    )
    session.add(user)
    try:
        session.flush()
        for role in roles:
            session.add(UserRole(user_id=user.id, role_id=role.id))
        session.commit()
    except IntegrityError as exc:
        session.rollback()
        raise ConflictError("USER_ALREADY_EXISTS", "User already exists") from exc
    session.refresh(user)
    return to_user_read(session, user)


def list_users(
    session: Session,
    page: int,
    page_size: int,
    keyword: str | None = None,
) -> UserListResponse:
    statement = select(User)
    if keyword:
        pattern = f"%{keyword.strip()}%"
        statement = statement.where(
            or_(
                col(User.username).ilike(pattern),
                col(User.email).ilike(pattern),
                col(User.display_name).ilike(pattern),
            )
        )
    all_users = session.exec(statement.order_by(col(User.created_at).desc())).all()
    start = (page - 1) * page_size
    users = all_users[start : start + page_size]
    return UserListResponse(
        items=[to_user_read(session, user) for user in users],
        total=len(all_users),
        page=page,
        page_size=page_size,
    )


def update_user_roles(session: Session, user_id: str, role_codes: list[str]) -> UserRead:
    user = get_user_by_id(session, user_id)
    roles = _resolve_roles(session, role_codes)
    existing_links = session.exec(select(UserRole).where(UserRole.user_id == user_id)).all()
    for link in existing_links:
        session.delete(link)
    for role in roles:
        session.add(UserRole(user_id=user_id, role_id=role.id))
    user.updated_at = utc_now()
    session.add(user)
    session.commit()
    session.refresh(user)
    return to_user_read(session, user)
