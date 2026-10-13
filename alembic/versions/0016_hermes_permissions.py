"""Hermes-native permission modules + skill registry.

Extends the CRM-only permission model to cover Hermes's own surfaces
(agents, chat, approvals, analytics, mail, touchpoints, admin*) and
prepares the ``collections`` table to double as the skill registry.

Schema changes
--------------
* ``collections`` gains:
  - ``kind VARCHAR(32) NOT NULL DEFAULT 'crm'`` — bucket used by the
    role-permission UI to group modules into tabs (crm / hermes / admin
    / skill).
  - ``description TEXT NULL`` — longer blurb displayed in the UI,
    especially useful for skills where the frontmatter ``description:``
    is the short summary.
  - ``metadata JSONB NOT NULL DEFAULT '{}'`` — skill-only: stashes
    ``category``, ``platforms``, ``source_path`` etc. so the SkillsTab
    can render without re-walking the filesystem.

Data changes
------------
* New ``permissions`` row: ``('use', 'use', 'Use')`` — the only action a
  skill module carries, and a clean verb for non-CRUD Hermes modules
  (e.g. ``chat.use``, ``analytics.use``).
* 10 new ``collections`` rows (kind='hermes' and kind='admin'):
  ``agents, chat, approvals, analytics, mail, touchpoints,
  admin_users, admin_departments, admin_roles, admin_skills``.
* Role backfill: every existing role gets reasonable defaults on the
  new modules so live users don't lose access after deploy. Details in
  :func:`_backfill_role_grants`. Admins short-circuit anyway; this only
  matters for non-admin roles.

Revision ID: 0016
Revises: 0015
Create Date: 2026-04-17
"""
from alembic import op


revision = "0016"
down_revision = "0015"


# ── New Hermes-native modules (not skills) ────────────────────────────
# (name, title, kind, description)
HERMES_MODULES = [
    ("agents",             "Agents",      "hermes", "管理 AI agent 账户与 profile"),
    ("chat",               "Chat",        "hermes", "AI 对话收件箱"),
    ("approvals",          "Approvals",   "hermes", "工作流审批"),
    ("analytics",          "Analytics",   "hermes", "用量统计"),
    ("mail",               "Mail",        "hermes", "个人邮箱 IMAP/SMTP 配置"),
    ("touchpoints",        "Touchpoints", "hermes", "触点（微信/电话/备忘）"),
    ("admin_users",        "用户管理",     "admin",  "管理团队成员账号"),
    ("admin_departments",  "部门管理",     "admin",  "管理组织架构"),
    ("admin_roles",        "角色权限",     "admin",  "管理角色与权限矩阵"),
    ("admin_skills",       "技能管理",     "admin",  "管理可分配的 agent 技能"),
]


# ── Role backfill defaults ────────────────────────────────────────────
# Matches the intent of the CRM seed: keep it narrow, expand via UI.
# "use" is sufficient for chat/analytics/approvals etc. because they
# don't carry CRUD semantics. Agents/mail/touchpoints use CRUD verbs.
# Admin modules default to index+view for Accounting (audit) and nothing
# for Support/Sales.
HERMES_ROLE_GRANTS = {
    "General Manager": {  # all actions on everything Hermes-native
        "agents":            ["index", "view", "create", "update", "delete", "use"],
        "chat":              ["index", "view", "use"],
        "approvals":         ["index", "view", "update", "use"],
        "analytics":         ["index", "view", "use"],
        "mail":              ["index", "view", "update"],
        "touchpoints":       ["index", "view", "create", "update", "delete", "use"],
        "admin_users":       ["index", "view", "create", "update", "delete"],
        "admin_departments": ["index", "view", "create", "update", "delete"],
        "admin_roles":       ["index", "view", "create", "update", "delete"],
        "admin_skills":      ["index", "view", "update"],
    },
    "Sales Manager": {
        "agents":            ["index", "view", "create", "update", "use"],
        "chat":              ["index", "view", "use"],
        "approvals":         ["index", "view", "update", "use"],
        "analytics":         ["index", "view"],
        "mail":              ["index", "view", "update"],
        "touchpoints":       ["index", "view", "create", "update", "use"],
    },
    "Sales": {
        "agents":            ["index", "view", "use"],
        "chat":              ["index", "view", "use"],
        "approvals":         ["index", "view", "use"],
        "mail":              ["index", "view", "update"],
        "touchpoints":       ["index", "view", "create", "use"],
    },
    "Accounting": {
        "agents":            ["index", "view"],
        "chat":              ["index", "view", "use"],
        "approvals":         ["index", "view", "update", "use"],
        "analytics":         ["index", "view"],
        "admin_users":       ["index", "view"],  # read-only audit
        "admin_departments": ["index", "view"],
    },
    "Support": {
        "agents":            ["index", "view", "use"],
        "chat":              ["index", "view", "use"],
        "approvals":         ["index", "view", "use"],
        "touchpoints":       ["index", "view", "create", "use"],
    },
}


def upgrade() -> None:
    # 1. Schema additions — ignore-if-exists for re-runnable deploy.
    op.execute("""
        ALTER TABLE collections
            ADD COLUMN IF NOT EXISTS kind VARCHAR(32) NOT NULL DEFAULT 'crm'
    """)
    op.execute("""
        ALTER TABLE collections
            ADD COLUMN IF NOT EXISTS description TEXT
    """)
    op.execute("""
        ALTER TABLE collections
            ADD COLUMN IF NOT EXISTS metadata JSONB NOT NULL DEFAULT '{}'::jsonb
    """)
    op.execute("CREATE INDEX IF NOT EXISTS collections_kind_idx ON collections (kind)")

    # 2. New 'use' permission code.
    op.execute("""
        INSERT INTO permissions (name, code, description)
        VALUES ('use', 'use', 'Use / invoke a Hermes capability or skill')
        ON CONFLICT (code) DO NOTHING
    """)

    # 3. Insert Hermes-native modules (idempotent via ON CONFLICT).
    for name, title, kind, desc in HERMES_MODULES:
        op.execute(
            f"""
            INSERT INTO collections (name, title, kind, description)
            VALUES ('{_esc(name)}', '{_esc(title)}', '{_esc(kind)}', '{_esc(desc)}')
            ON CONFLICT (name) DO UPDATE
              SET title = EXCLUDED.title,
                  kind = EXCLUDED.kind,
                  description = COALESCE(collections.description, EXCLUDED.description)
            """
        )

    # 4. Backfill role grants on the new modules.
    _backfill_role_grants()


def downgrade() -> None:
    for name, _, _, _ in HERMES_MODULES:
        op.execute(f"DELETE FROM collections WHERE name = '{_esc(name)}'")
    op.execute("DROP INDEX IF EXISTS collections_kind_idx")
    op.execute("ALTER TABLE collections DROP COLUMN IF EXISTS metadata")
    op.execute("ALTER TABLE collections DROP COLUMN IF EXISTS description")
    op.execute("ALTER TABLE collections DROP COLUMN IF EXISTS kind")
    op.execute("DELETE FROM permissions WHERE code = 'use'")


def _esc(s: str) -> str:
    """Single-quote escape for inline SQL (migration values are trusted)."""
    return s.replace("'", "''")


def _backfill_role_grants() -> None:
    """Insert default grants for existing roles on new Hermes modules.

    Idempotent: uses ``ON CONFLICT DO NOTHING`` on the (role_id,
    permission_id, module_id) composite PK. Won't overwrite anything an
    admin has already tweaked through the UI.
    """
    connection = op.get_bind()
    roles = {r.name: r.id for r in connection.execute(
        __import__('sqlalchemy').text("SELECT id, name FROM roles")
    )}
    perms = {r.code: r.id for r in connection.execute(
        __import__('sqlalchemy').text("SELECT id, code FROM permissions")
    )}
    mods = {r.name: r.id for r in connection.execute(
        __import__('sqlalchemy').text("SELECT id, name FROM collections")
    )}

    for role_name, module_grants in HERMES_ROLE_GRANTS.items():
        role_id = roles.get(role_name)
        if role_id is None:
            continue
        for module_name, codes in module_grants.items():
            module_id = mods.get(module_name)
            if module_id is None:
                continue
            for code in codes:
                perm_id = perms.get(code)
                if perm_id is None:
                    continue
                op.execute(
                    f"""
                    INSERT INTO role_permissions (role_id, permission_id, module_id, has_permission)
                    VALUES ({role_id}, {perm_id}, {module_id}, TRUE)
                    ON CONFLICT (role_id, permission_id, module_id) DO NOTHING
                    """
                )
