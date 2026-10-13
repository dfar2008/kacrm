from __future__ import annotations

import bcrypt
from dataclasses import dataclass
from datetime import datetime, timezone
import hashlib
import hmac
import secrets
from typing import Protocol

from hermes_cloud.app.db import make_engine
from sqlalchemy import Boolean, DateTime, String, create_engine, select
from sqlalchemy.orm import DeclarativeBase, Mapped, Session, mapped_column


def _hash_token(token: str) -> str:
    return hashlib.sha256(token.encode("utf-8")).hexdigest()


def _hash_password(password: str) -> str:
    return bcrypt.hashpw(password.encode("utf-8"), bcrypt.gensalt()).decode("utf-8")


def _verify_password(password: str, password_hash: str) -> bool:
    try:
        return bcrypt.checkpw(password.encode("utf-8"), password_hash.encode("utf-8"))
    except Exception:
        return False


def _generate_api_key() -> str:
    return secrets.token_hex(24)


@dataclass
class ApiKeyRecord:
    token_hash: str
    user_id: str
    kind: str
    disabled: bool = False


@dataclass
class UserAccount:
    id: str
    email: str
    password_hash: str
    is_active: bool
    created_at: datetime
    api_key: str
    plan: str = "free"
    is_admin: bool = False


class AuthStore(Protocol):
    def seed_user_key(self, *, token: str, user_id: str) -> None: ...
    def seed_admin_key(self, *, token: str, admin_id: str) -> None: ...
    def authenticate_user(self, token: str) -> str | None: ...
    def authenticate_admin(self, token: str) -> str | None: ...
    def has_user_keys(self) -> bool: ...
    def has_admin_keys(self) -> bool: ...
    def list_user_ids(self) -> list[str]: ...
    def authenticate_with_password(self, email: str, password: str) -> UserAccount | None: ...


class InMemoryAuthStore:
    def __init__(self) -> None:
        self._user_keys: dict[str, ApiKeyRecord] = {}
        self._admin_keys: dict[str, ApiKeyRecord] = {}
        # Password-based accounts
        self._accounts: dict[str, UserAccount] = {}  # user_id -> UserAccount
        self._by_email: dict[str, UserAccount] = {}  # email -> UserAccount

    def seed_user_key(self, *, token: str, user_id: str) -> None:
        token_hash = _hash_token(token)
        if token_hash not in self._user_keys:
            self._user_keys[token_hash] = ApiKeyRecord(token_hash=token_hash, user_id=user_id, kind="user")

    def seed_admin_key(self, *, token: str, admin_id: str) -> None:
        token_hash = _hash_token(token)
        if token_hash not in self._admin_keys:
            self._admin_keys[token_hash] = ApiKeyRecord(token_hash=token_hash, user_id=admin_id, kind="admin")

    def authenticate_user(self, token: str) -> str | None:
        token_hash = _hash_token(token)
        record = self._user_keys.get(token_hash)
        if record is None or record.disabled:
            return None
        if not hmac.compare_digest(record.token_hash, token_hash):
            return None
        return record.user_id

    def authenticate_admin(self, token: str) -> str | None:
        token_hash = _hash_token(token)
        record = self._admin_keys.get(token_hash)
        if record is None or record.disabled:
            return None
        if not hmac.compare_digest(record.token_hash, token_hash):
            return None
        return record.user_id

    def has_user_keys(self) -> bool:
        return bool(self._user_keys)

    def has_admin_keys(self) -> bool:
        return bool(self._admin_keys)

    def list_user_ids(self) -> list[str]:
        return sorted({record.user_id for record in self._user_keys.values()})

    # ------------------------------------------------------------------
    # Password-based account methods
    # ------------------------------------------------------------------

    def register_user(self, email: str, password: str, *, is_admin: bool = False) -> UserAccount:
        email = email.strip().lower()
        if email in self._by_email:
            raise ValueError(f"Email already registered: {email}")
        user_id = secrets.token_hex(16)
        api_key = _generate_api_key()
        password_hash = _hash_password(password)
        now = datetime.now(timezone.utc)
        account = UserAccount(
            id=user_id,
            email=email,
            password_hash=password_hash,
            is_active=True,
            created_at=now,
            api_key=api_key,
            plan="free",
            is_admin=is_admin,
        )
        self._accounts[user_id] = account
        self._by_email[email] = account
        # Also register the api_key so bearer auth works
        self.seed_user_key(token=api_key, user_id=user_id)
        if is_admin:
            self.seed_admin_key(token=api_key, admin_id=user_id)
        return account

    def authenticate_with_password(self, email: str, password: str) -> UserAccount | None:
        email = email.strip().lower()
        account = self._by_email.get(email)
        if account is None or not account.is_active:
            return None
        if not _verify_password(password, account.password_hash):
            return None
        return account

    def list_users(self) -> list[UserAccount]:
        return sorted(self._accounts.values(), key=lambda a: a.created_at)

    def get_user(self, user_id: str) -> UserAccount | None:
        return self._accounts.get(user_id)

    def update_user(self, user_id: str, *, is_active: bool | None = None, plan: str | None = None, is_admin: bool | None = None) -> UserAccount | None:
        account = self._accounts.get(user_id)
        if account is None:
            return None
        if is_active is not None:
            account.is_active = is_active
        if plan is not None:
            account.plan = plan
        if is_admin is not None:
            account.is_admin = is_admin
        return account

    def delete_user(self, user_id: str) -> bool:
        account = self._accounts.pop(user_id, None)
        if account is None:
            return False
        self._by_email.pop(account.email, None)
        # Remove api keys
        token_hash = _hash_token(account.api_key)
        self._user_keys.pop(token_hash, None)
        self._admin_keys.pop(token_hash, None)
        return True

    def reset_password(self, user_id: str, new_password: str) -> bool:
        account = self._accounts.get(user_id)
        if account is None:
            return False
        account.password_hash = _hash_password(new_password)
        return True

    def create_user_by_admin(self, email: str, password: str, plan: str = "free", *, is_admin: bool = False) -> UserAccount:
        account = self.register_user(email, password, is_admin=is_admin)
        account.plan = plan
        return account


class Base(DeclarativeBase):
    pass


class UserRow(Base):
    __tablename__ = "users"

    user_id: Mapped[str] = mapped_column(String(255), primary_key=True)


class AdminRow(Base):
    __tablename__ = "admins"

    admin_id: Mapped[str] = mapped_column(String(255), primary_key=True)


class UserApiKeyRow(Base):
    __tablename__ = "user_api_keys"

    token_hash: Mapped[str] = mapped_column(String(64), primary_key=True)
    user_id: Mapped[str] = mapped_column(String(255), nullable=False, index=True)
    disabled: Mapped[bool] = mapped_column(Boolean, nullable=False, default=False)


class AdminApiKeyRow(Base):
    __tablename__ = "admin_api_keys"

    token_hash: Mapped[str] = mapped_column(String(64), primary_key=True)
    admin_id: Mapped[str] = mapped_column(String(255), nullable=False, index=True)
    disabled: Mapped[bool] = mapped_column(Boolean, nullable=False, default=False)


class UserAccountRow(Base):
    __tablename__ = "user_accounts"

    id: Mapped[str] = mapped_column(String(64), primary_key=True)
    email: Mapped[str] = mapped_column(String(320), unique=True, nullable=False, index=True)
    password_hash: Mapped[str] = mapped_column(String(255), nullable=False)
    is_active: Mapped[bool] = mapped_column(Boolean, nullable=False, default=True)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    api_key: Mapped[str] = mapped_column(String(64), nullable=False)
    plan: Mapped[str] = mapped_column(String(64), nullable=False, default="free")
    is_admin: Mapped[bool] = mapped_column(Boolean, nullable=False, default=False)


class SqlAlchemyAuthStore:
    def __init__(self, database_url: str) -> None:
        self._engine = make_engine(database_url)
        Base.metadata.create_all(self._engine)
        # Add columns that may be missing from pre-existing databases (schema migration).
        self._migrate_schema()

    def _migrate_schema(self) -> None:
        """Apply additive schema migrations for columns added after initial deploy."""
        from sqlalchemy import text, inspect as sa_inspect
        with self._engine.connect() as conn:
            insp = sa_inspect(self._engine)
            ua_cols = {c["name"] for c in insp.get_columns("user_accounts")}
            if "is_admin" not in ua_cols:
                conn.execute(text("ALTER TABLE user_accounts ADD COLUMN is_admin BOOLEAN NOT NULL DEFAULT 0"))
                conn.commit()

    def seed_user_key(self, *, token: str, user_id: str) -> None:
        token_hash = _hash_token(token)
        with Session(self._engine) as db:
            user = db.get(UserRow, user_id)
            if user is None:
                db.add(UserRow(user_id=user_id))
            row = db.get(UserApiKeyRow, token_hash)
            if row is None:
                db.add(UserApiKeyRow(token_hash=token_hash, user_id=user_id, disabled=False))
            db.commit()

    def seed_admin_key(self, *, token: str, admin_id: str) -> None:
        token_hash = _hash_token(token)
        with Session(self._engine) as db:
            admin = db.get(AdminRow, admin_id)
            if admin is None:
                db.add(AdminRow(admin_id=admin_id))
            row = db.get(AdminApiKeyRow, token_hash)
            if row is None:
                db.add(AdminApiKeyRow(token_hash=token_hash, admin_id=admin_id, disabled=False))
            db.commit()

    def authenticate_user(self, token: str) -> str | None:
        token_hash = _hash_token(token)
        with Session(self._engine) as db:
            row = db.get(UserApiKeyRow, token_hash)
            if row is not None and not row.disabled and hmac.compare_digest(row.token_hash, token_hash):
                return row.user_id
            # Admin keys also count as valid user keys (admins can call all user endpoints).
            admin_row = db.get(AdminApiKeyRow, token_hash)
            if admin_row is not None and not admin_row.disabled and hmac.compare_digest(admin_row.token_hash, token_hash):
                return admin_row.admin_id
            return None

    def authenticate_admin(self, token: str) -> str | None:
        token_hash = _hash_token(token)
        with Session(self._engine) as db:
            row = db.get(AdminApiKeyRow, token_hash)
            if row is None or row.disabled:
                return None
            if not hmac.compare_digest(row.token_hash, token_hash):
                return None
            return row.admin_id

    def has_user_keys(self) -> bool:
        with Session(self._engine) as db:
            return db.scalar(select(UserApiKeyRow.token_hash).limit(1)) is not None

    def has_admin_keys(self) -> bool:
        with Session(self._engine) as db:
            return db.scalar(select(AdminApiKeyRow.token_hash).limit(1)) is not None

    def list_user_ids(self) -> list[str]:
        with Session(self._engine) as db:
            rows = db.scalars(select(UserRow.user_id).order_by(UserRow.user_id.asc())).all()
            return list(rows)

    # ------------------------------------------------------------------
    # Password-based account methods
    # ------------------------------------------------------------------

    def _row_to_account(self, row: UserAccountRow) -> UserAccount:
        return UserAccount(
            id=row.id,
            email=row.email,
            password_hash=row.password_hash,
            is_active=row.is_active,
            created_at=row.created_at,
            api_key=row.api_key,
            plan=row.plan,
            is_admin=row.is_admin,
        )

    def register_user(self, email: str, password: str, *, is_admin: bool = False) -> UserAccount:
        email = email.strip().lower()
        with Session(self._engine) as db:
            existing = db.scalar(select(UserAccountRow).where(UserAccountRow.email == email))
            if existing is not None:
                raise ValueError(f"Email already registered: {email}")
            user_id = secrets.token_hex(16)
            api_key = _generate_api_key()
            password_hash = _hash_password(password)
            now = datetime.now(timezone.utc)
            row = UserAccountRow(
                id=user_id,
                email=email,
                password_hash=password_hash,
                is_active=True,
                created_at=now,
                api_key=api_key,
                plan="free",
                is_admin=is_admin,
            )
            db.add(row)
            # Also add user_id to users table and api_key to user_api_keys
            user_row = db.get(UserRow, user_id)
            if user_row is None:
                db.add(UserRow(user_id=user_id))
            token_hash = _hash_token(api_key)
            key_row = db.get(UserApiKeyRow, token_hash)
            if key_row is None:
                db.add(UserApiKeyRow(token_hash=token_hash, user_id=user_id, disabled=False))
            if is_admin:
                admin_row = db.get(AdminRow, user_id)
                if admin_row is None:
                    db.add(AdminRow(admin_id=user_id))
                admin_token_hash = _hash_token(api_key)
                admin_key_row = db.get(AdminApiKeyRow, admin_token_hash)
                if admin_key_row is None:
                    db.add(AdminApiKeyRow(token_hash=admin_token_hash, admin_id=user_id, disabled=False))
            db.commit()
            db.refresh(row)
            return self._row_to_account(row)

    def authenticate_with_password(self, email: str, password: str) -> UserAccount | None:
        email = email.strip().lower()
        with Session(self._engine) as db:
            row = db.scalar(select(UserAccountRow).where(UserAccountRow.email == email))
            if row is None or not row.is_active:
                return None
            if not _verify_password(password, row.password_hash):
                return None
            return self._row_to_account(row)

    def list_users(self) -> list[UserAccount]:
        with Session(self._engine) as db:
            rows = db.scalars(select(UserAccountRow).order_by(UserAccountRow.created_at.asc())).all()
            return [self._row_to_account(r) for r in rows]

    def get_user(self, user_id: str) -> UserAccount | None:
        with Session(self._engine) as db:
            row = db.get(UserAccountRow, user_id)
            if row is None:
                return None
            return self._row_to_account(row)

    def update_user(self, user_id: str, *, is_active: bool | None = None, plan: str | None = None, is_admin: bool | None = None) -> UserAccount | None:
        with Session(self._engine) as db:
            row = db.get(UserAccountRow, user_id)
            if row is None:
                return None
            if is_active is not None:
                row.is_active = is_active
            if plan is not None:
                row.plan = plan
            if is_admin is not None:
                row.is_admin = is_admin
            db.commit()
            db.refresh(row)
            return self._row_to_account(row)

    def delete_user(self, user_id: str) -> bool:
        with Session(self._engine) as db:
            row = db.get(UserAccountRow, user_id)
            if row is None:
                return False
            # Remove api keys
            token_hash = _hash_token(row.api_key)
            key_row = db.get(UserApiKeyRow, token_hash)
            if key_row is not None:
                db.delete(key_row)
            admin_key_row = db.get(AdminApiKeyRow, token_hash)
            if admin_key_row is not None:
                db.delete(admin_key_row)
            user_row = db.get(UserRow, user_id)
            if user_row is not None:
                db.delete(user_row)
            admin_row = db.get(AdminRow, user_id)
            if admin_row is not None:
                db.delete(admin_row)
            db.delete(row)
            db.commit()
            return True

    def reset_password(self, user_id: str, new_password: str) -> bool:
        with Session(self._engine) as db:
            row = db.get(UserAccountRow, user_id)
            if row is None:
                return False
            row.password_hash = _hash_password(new_password)
            db.commit()
            return True

    def create_user_by_admin(self, email: str, password: str, plan: str = "free", *, is_admin: bool = False) -> UserAccount:
        account = self.register_user(email, password, is_admin=is_admin)
        if plan != "free":
            account = self.update_user(account.id, plan=plan) or account
        return account
