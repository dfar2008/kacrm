"""Per-user mail account config (IMAP for receive + SMTP for send).

One row per CRM user, shared username/password, independent host/port
pairs for receive and send. Password is AES-GCM encrypted at rest.
"""
from hermes_cloud.app.mail.store import (
    MailAccount,
    MailStore,
    SqlAlchemyMailStore,
    encrypt_password,
    decrypt_password,
)

__all__ = [
    "MailAccount",
    "MailStore",
    "SqlAlchemyMailStore",
    "encrypt_password",
    "decrypt_password",
]
