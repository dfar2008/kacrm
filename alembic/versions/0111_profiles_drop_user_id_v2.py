"""profiles 落实 v2 §简化原则:去掉 user_id 列,关联改为 id == users.username。

背景:0008 建 profiles 时 user_id 是 BIGINT NOT NULL REFERENCES users(id),并带
idx_profiles_user_id / idx_profiles_one_default_per_user / idx_profiles_user_slug
三个按 user_id 的索引。但 v2 把模型简化成「1 user = 1 profile」、profile.id 直接
就是 users.username,读路径全部走 `JOIN users u ON u.username = p.id`,代码侧
(crm_profile_store)已无 user_id 列的认知。

漂移事故:dev 库被手工改成了 v2 形态(无 user_id),但一直没有对应迁移。任何
干净 `alembic upgrade head`(prod 手动升库)都会让 user_id NOT NULL 复活 →
ensure_default_profile 不写 user_id 的 INSERT 违反 NOT NULL → 建用户 500
(正是 commit 01d1db7 修过的同一个坑)。本迁移把 schema 收敛到代码要求的 v2 形态,
让任意环境重建后保持一致。

幂等:列/索引都用 IF EXISTS / IF NOT EXISTS。
"""
from alembic import op

revision = "0111"
down_revision = "0110"
branch_labels = None
depends_on = None


def upgrade() -> None:
    # 1) 先删按 user_id 的索引(drop column CASCADE 也会带走,但显式更清楚)。
    op.execute("DROP INDEX IF EXISTS idx_profiles_user_slug;")
    op.execute("DROP INDEX IF EXISTS idx_profiles_one_default_per_user;")
    op.execute("DROP INDEX IF EXISTS idx_profiles_user_id;")
    # 2) 删 user_id 列(CASCADE 一并去掉指向 users(id) 的外键)。
    op.execute("ALTER TABLE profiles DROP COLUMN IF EXISTS user_id CASCADE;")
    # 3) v2 约定索引:id 即 username,默认 profile 唯一(dev 库现状)。
    op.execute(
        """
        CREATE UNIQUE INDEX IF NOT EXISTS idx_profiles_id_default
            ON profiles (id) WHERE is_default = TRUE AND is_active = TRUE
        """
    )


def downgrade() -> None:
    # 还原到 0008 的 user_id 形态。已存在 profile 行的 user_id 无法凭空恢复
    # (关联信息只剩 id==username),所以列以 NULL 加回、不强制 NOT NULL,避免
    # 既有数据让 downgrade 失败;FK 同样不强制(数据可能对不上)。
    op.execute("DROP INDEX IF EXISTS idx_profiles_id_default;")
    op.execute("ALTER TABLE profiles ADD COLUMN IF NOT EXISTS user_id BIGINT;")
    op.execute(
        "CREATE INDEX IF NOT EXISTS idx_profiles_user_id ON profiles(user_id);"
    )
