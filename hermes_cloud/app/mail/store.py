"""Mail account storage (IMAP receive + SMTP send, one row per user).

One ``mail_accounts`` row holds the full mailbox credential + separate
connection params for each direction. Providers almost universally use
the same ``username/password`` for receive and send; we only split
``host/port/ssl``.

Passwords are AES-GCM encrypted at rest. The key is derived from the
runtime ``internal_key`` via SHA-256 — rotating the secret invalidates
all stored passwords (user re-enters).

The row is nullable on the SMTP side: a user who only wants inbound
polling can leave ``smtp_host`` empty.
"""
from __future__ import annotations

import base64
import hashlib
import os
from dataclasses import dataclass
from datetime import datetime, timezone
from typing import List, Optional, Protocol

from sqlalchemy import Boolean, DateTime, Integer, String, Text, select
from sqlalchemy.orm import DeclarativeBase, Mapped, Session, mapped_column, sessionmaker

from hermes_cloud.app.db import make_engine


class Base(DeclarativeBase):
    pass


@dataclass
class MailAccount:
    """Read model exposed to the API (no ciphertext leaks out)."""

    id: int
    user_id: int
    username: str

    # Receive (IMAP) side. Always present — the row's raison d'être.
    imap_host: str
    imap_port: int
    imap_use_ssl: bool
    imap_enabled: bool  # whether the poller should pull this box

    # Send (SMTP) side. All nullable: user may configure receive-only.
    smtp_host: Optional[str]
    smtp_port: Optional[int]
    smtp_use_tls: bool

    last_poll_at: Optional[datetime]
    last_error: Optional[str]
    has_password: bool

    # Populated only by code paths that need to actually connect
    # (poller, sender, test-connection endpoint). Never returned via
    # the JSON API.
    password: Optional[str] = None


class MailAccountRow(Base):
    __tablename__ = "mail_accounts"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    user_id: Mapped[int] = mapped_column(Integer, nullable=False, unique=True, index=True)
    username: Mapped[str] = mapped_column(String(255), nullable=False)
    password_ct: Mapped[Optional[str]] = mapped_column(Text, nullable=True)

    imap_host: Mapped[str] = mapped_column(String(255), nullable=False)
    imap_port: Mapped[int] = mapped_column(Integer, nullable=False, default=993)
    imap_use_ssl: Mapped[bool] = mapped_column(Boolean, nullable=False, default=True)
    imap_enabled: Mapped[bool] = mapped_column(Boolean, nullable=False, default=True)

    smtp_host: Mapped[Optional[str]] = mapped_column(String(255), nullable=True)
    smtp_port: Mapped[Optional[int]] = mapped_column(Integer, nullable=True)
    smtp_use_tls: Mapped[bool] = mapped_column(Boolean, nullable=False, default=True)

    last_poll_at: Mapped[Optional[datetime]] = mapped_column(
        DateTime(timezone=True), nullable=True
    )
    last_error: Mapped[Optional[str]] = mapped_column(Text, nullable=True)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False,
        default=lambda: datetime.now(timezone.utc),
    )
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False,
        default=lambda: datetime.now(timezone.utc),
        onupdate=lambda: datetime.now(timezone.utc),
    )


# ---------------------------------------------------------------------------
# AES-GCM encryption
# ---------------------------------------------------------------------------

def _derive_key(secret: str) -> bytes:
    return hashlib.sha256(secret.encode("utf-8")).digest()


def encrypt_password(plaintext: str, secret: str) -> str:
    try:
        from cryptography.hazmat.primitives.ciphers.aead import AESGCM
    except Exception as exc:  # pragma: no cover
        raise RuntimeError(
            "cryptography package required for mail password storage"
        ) from exc
    key = _derive_key(secret)
    nonce = os.urandom(12)
    ct = AESGCM(key).encrypt(nonce, plaintext.encode("utf-8"), None)
    return base64.b64encode(nonce + ct).decode("ascii")


def decrypt_password(ciphertext_b64: str, secret: str) -> Optional[str]:
    try:
        from cryptography.hazmat.primitives.ciphers.aead import AESGCM
    except Exception:
        return None
    try:
        blob = base64.b64decode(ciphertext_b64)
        if len(blob) < 12:
            return None
        nonce, ct = blob[:12], blob[12:]
        key = _derive_key(secret)
        pt = AESGCM(key).decrypt(nonce, ct, None)
        return pt.decode("utf-8")
    except Exception:
        return None


# ---------------------------------------------------------------------------
# Store protocol + SQLAlchemy impl
# ---------------------------------------------------------------------------

class MailStore(Protocol):
    def get_for_user(
        self, user_id: int, *, with_password: bool = False,
        secret: Optional[str] = None,
    ) -> Optional[MailAccount]: ...
    def upsert(
        self,
        *,
        user_id: int,
        username: str,
        password: Optional[str],
        imap_host: str,
        imap_port: int,
        imap_use_ssl: bool,
        imap_enabled: bool,
        smtp_host: Optional[str],
        smtp_port: Optional[int],
        smtp_use_tls: bool,
        secret: str,
    ) -> MailAccount: ...
    def delete(self, user_id: int) -> bool: ...
    def list_enabled(self, *, secret: str) -> List[MailAccount]: ...
    def mark_poll_result(self, user_id: int, error: Optional[str]) -> None: ...


def _row_to_account(r: MailAccountRow, *, password: Optional[str] = None) -> MailAccount:
    return MailAccount(
        id=r.id,
        user_id=r.user_id,
        username=r.username,
        imap_host=r.imap_host,
        imap_port=r.imap_port,
        imap_use_ssl=bool(r.imap_use_ssl),
        imap_enabled=bool(r.imap_enabled),
        smtp_host=r.smtp_host,
        smtp_port=r.smtp_port,
        smtp_use_tls=bool(r.smtp_use_tls),
        last_poll_at=r.last_poll_at,
        last_error=r.last_error,
        has_password=bool(r.password_ct),
        password=password,
    )


class SqlAlchemyMailStore:
    def __init__(self, database_url: str) -> None:
        self._engine = make_engine(database_url)
        self._session_factory = sessionmaker(bind=self._engine, expire_on_commit=False)
        Base.metadata.create_all(self._engine)

    def _session(self) -> Session:
        return self._session_factory()

    def get_for_user(
        self, user_id: int, *, with_password: bool = False,
        secret: Optional[str] = None,
    ) -> Optional[MailAccount]:
        with self._session() as sess:
            row = sess.scalar(select(MailAccountRow).where(MailAccountRow.user_id == user_id))
            if row is None:
                return None
            pwd = None
            if with_password and row.password_ct and secret:
                pwd = decrypt_password(row.password_ct, secret)
            return _row_to_account(row, password=pwd)

    def upsert(
        self,
        *,
        user_id: int,
        username: str,
        password: Optional[str],
        imap_host: str,
        imap_port: int,
        imap_use_ssl: bool,
        imap_enabled: bool,
        smtp_host: Optional[str],
        smtp_port: Optional[int],
        smtp_use_tls: bool,
        secret: str,
    ) -> MailAccount:
        with self._session() as sess:
            row = sess.scalar(select(MailAccountRow).where(MailAccountRow.user_id == user_id))
            if row is None:
                row = MailAccountRow(user_id=user_id)
                sess.add(row)
            row.username = username
            row.imap_host = imap_host
            row.imap_port = imap_port
            row.imap_use_ssl = imap_use_ssl
            row.imap_enabled = imap_enabled
            row.smtp_host = smtp_host or None
            row.smtp_port = smtp_port
            row.smtp_use_tls = smtp_use_tls
            if password:
                row.password_ct = encrypt_password(password, secret)
            row.updated_at = datetime.now(timezone.utc)
            sess.commit()
            sess.refresh(row)
            return _row_to_account(row)

    def delete(self, user_id: int) -> bool:
        with self._session() as sess:
            row = sess.scalar(select(MailAccountRow).where(MailAccountRow.user_id == user_id))
            if row is None:
                return False
            sess.delete(row)
            sess.commit()
            return True

    def list_enabled(self, *, secret: str) -> List[MailAccount]:
        """Return IMAP-polling-enabled accounts with password decrypted."""
        with self._session() as sess:
            rows = sess.scalars(
                select(MailAccountRow).where(MailAccountRow.imap_enabled.is_(True))
            ).all()
            out: List[MailAccount] = []
            for r in rows:
                pwd = decrypt_password(r.password_ct, secret) if r.password_ct else None
                if pwd is None:
                    continue
                out.append(_row_to_account(r, password=pwd))
            return out

    def mark_poll_result(self, user_id: int, error: Optional[str]) -> None:
        with self._session() as sess:
            row = sess.scalar(select(MailAccountRow).where(MailAccountRow.user_id == user_id))
            if row is None:
                return
            row.last_poll_at = datetime.now(timezone.utc)
            row.last_error = error
            sess.commit()
