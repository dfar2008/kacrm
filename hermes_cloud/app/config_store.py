"""ConfigStore — 每个 hermes_cloud 用户（以及每个 profile）的配置存储。

**多-profile 约定**（见 docs/2026-04-15-multi-profile-foundation.md）：

每个 user 有一个默认 profile（id == user_id），以及 0..N 个非默认 profile。
各种配置（model / toolsets / prompt / agents）都是 **per-profile** 的：非默认
profile 不会 fallback 到默认 profile 的配置。

存储设计（零迁移）：
  - 默认 profile 的数据继续存在原来的 ``user_models / user_toolsets /
    user_agents_configs`` 表里（主键 ``user_id``）。
  - 非默认 profile 的数据存在新增的 ``profile_models / profile_toolsets /
    profile_agents_configs`` 表里（主键 ``profile_id``）。
  - ``user_prompt_configs`` + ``profile_prompt_configs`` removed
    2026-05-18: dead code (no UI, no reader, 0 rows).
  - Store API 增加可选 ``profile_id`` 参数：
      * 未提供、或 ``profile_id == user_id`` → 走老表（默认 profile）
      * 其它值 → 走新表（非默认 profile）

老调用点（``set_user_model(user_id=...)``）零改动，自然读写默认 profile。
"""
from __future__ import annotations

from dataclasses import dataclass
import json
from typing import Protocol

from hermes_cloud.app.db import make_engine
from sqlalchemy import String, Text, create_engine
from sqlalchemy.orm import DeclarativeBase, Mapped, Session, mapped_column


@dataclass
class UserModelConfig:
    user_id: str
    model: str | None
    api_key: str | None
    base_url: str | None
    providers_json: str | None = None


@dataclass
class UserToolsetsConfig:
    user_id: str
    toolsets: list[str]


@dataclass
class UserAgentsConfig:
    user_id: str
    system_prompt: str | None
    memory_enabled: bool


def _is_default_profile(user_id: str, profile_id: str | None) -> bool:
    """``profile_id`` 省略或等于 ``user_id`` 都视作默认 profile。"""
    return profile_id is None or profile_id == user_id


class ConfigStore(Protocol):
    def get_user_model(
        self, user_id: str, profile_id: str | None = None
    ) -> UserModelConfig | None: ...
    def set_user_model(
        self,
        *,
        user_id: str,
        model: str | None,
        api_key: str | None,
        base_url: str | None,
        providers_json: str | None = None,
        profile_id: str | None = None,
    ) -> UserModelConfig: ...
    def get_user_toolsets(
        self, user_id: str, profile_id: str | None = None
    ) -> UserToolsetsConfig | None: ...
    def set_user_toolsets(
        self,
        *,
        user_id: str,
        toolsets: list[str],
        profile_id: str | None = None,
    ) -> UserToolsetsConfig: ...
    def get_user_agents_config(
        self, user_id: str, profile_id: str | None = None
    ) -> UserAgentsConfig | None: ...
    def set_user_agents_config(
        self,
        *,
        user_id: str,
        system_prompt: str | None,
        memory_enabled: bool,
        profile_id: str | None = None,
    ) -> UserAgentsConfig: ...


# ---------------------------------------------------------------------------
# In-memory 实现
# ---------------------------------------------------------------------------

class InMemoryConfigStore:
    def __init__(self) -> None:
        # 默认 profile（key: user_id）
        self._models: dict[str, UserModelConfig] = {}
        self._toolsets: dict[str, UserToolsetsConfig] = {}
        self._agents_configs: dict[str, UserAgentsConfig] = {}
        # 非默认 profile（key: profile_id）
        self._profile_models: dict[str, UserModelConfig] = {}
        self._profile_toolsets: dict[str, UserToolsetsConfig] = {}
        self._profile_agents_configs: dict[str, UserAgentsConfig] = {}

    # --- model ---
    def get_user_model(
        self, user_id: str, profile_id: str | None = None
    ) -> UserModelConfig | None:
        if _is_default_profile(user_id, profile_id):
            return self._models.get(user_id)
        return self._profile_models.get(profile_id)

    def set_user_model(
        self,
        *,
        user_id: str,
        model: str | None,
        api_key: str | None,
        base_url: str | None,
        providers_json: str | None = None,
        profile_id: str | None = None,
    ) -> UserModelConfig:
        config = UserModelConfig(
            user_id=user_id,
            model=model,
            api_key=api_key,
            base_url=base_url,
            providers_json=providers_json,
        )
        if _is_default_profile(user_id, profile_id):
            self._models[user_id] = config
        else:
            self._profile_models[profile_id] = config
        return config

    # --- toolsets ---
    def get_user_toolsets(
        self, user_id: str, profile_id: str | None = None
    ) -> UserToolsetsConfig | None:
        if _is_default_profile(user_id, profile_id):
            return self._toolsets.get(user_id)
        return self._profile_toolsets.get(profile_id)

    def set_user_toolsets(
        self,
        *,
        user_id: str,
        toolsets: list[str],
        profile_id: str | None = None,
    ) -> UserToolsetsConfig:
        config = UserToolsetsConfig(user_id=user_id, toolsets=list(toolsets))
        if _is_default_profile(user_id, profile_id):
            self._toolsets[user_id] = config
        else:
            self._profile_toolsets[profile_id] = config
        return config

    # --- agents config ---
    def get_user_agents_config(
        self, user_id: str, profile_id: str | None = None
    ) -> UserAgentsConfig | None:
        if _is_default_profile(user_id, profile_id):
            return self._agents_configs.get(user_id)
        return self._profile_agents_configs.get(profile_id)

    def set_user_agents_config(
        self,
        *,
        user_id: str,
        system_prompt: str | None,
        memory_enabled: bool,
        profile_id: str | None = None,
    ) -> UserAgentsConfig:
        config = UserAgentsConfig(
            user_id=user_id,
            system_prompt=system_prompt,
            memory_enabled=memory_enabled,
        )
        if _is_default_profile(user_id, profile_id):
            self._agents_configs[user_id] = config
        else:
            self._profile_agents_configs[profile_id] = config
        return config


# ---------------------------------------------------------------------------
# SQLAlchemy 实现
# ---------------------------------------------------------------------------

class Base(DeclarativeBase):
    pass


# --- 默认 profile 表（原表，保持不变） ---

class UserModelRow(Base):
    __tablename__ = "user_models"

    user_id: Mapped[str] = mapped_column(String(255), primary_key=True)
    model: Mapped[str | None] = mapped_column(String(255), nullable=True)
    api_key: Mapped[str | None] = mapped_column(Text, nullable=True)
    base_url: Mapped[str | None] = mapped_column(Text, nullable=True)
    providers_json: Mapped[str | None] = mapped_column(Text, nullable=True)


class UserToolsetsRow(Base):
    __tablename__ = "user_toolsets"

    user_id: Mapped[str] = mapped_column(String(255), primary_key=True)
    toolsets_json: Mapped[str] = mapped_column(Text, nullable=False, default="[]")


class UserAgentsConfigRow(Base):
    __tablename__ = "user_agents_configs"

    user_id: Mapped[str] = mapped_column(String(255), primary_key=True)
    system_prompt: Mapped[str | None] = mapped_column(Text, nullable=True)
    memory_enabled: Mapped[str] = mapped_column(String(8), nullable=False, default="true")


# --- 非默认 profile 表（新表） ---

class ProfileModelRow(Base):
    __tablename__ = "profile_models"

    profile_id: Mapped[str] = mapped_column(String(64), primary_key=True)
    user_id: Mapped[str] = mapped_column(String(255), nullable=False, index=True)
    model: Mapped[str | None] = mapped_column(String(255), nullable=True)
    api_key: Mapped[str | None] = mapped_column(Text, nullable=True)
    base_url: Mapped[str | None] = mapped_column(Text, nullable=True)
    providers_json: Mapped[str | None] = mapped_column(Text, nullable=True)


class ProfileToolsetsRow(Base):
    __tablename__ = "profile_toolsets"

    profile_id: Mapped[str] = mapped_column(String(64), primary_key=True)
    user_id: Mapped[str] = mapped_column(String(255), nullable=False, index=True)
    toolsets_json: Mapped[str] = mapped_column(Text, nullable=False, default="[]")


class ProfileAgentsConfigRow(Base):
    __tablename__ = "profile_agents_configs"

    profile_id: Mapped[str] = mapped_column(String(64), primary_key=True)
    user_id: Mapped[str] = mapped_column(String(255), nullable=False, index=True)
    system_prompt: Mapped[str | None] = mapped_column(Text, nullable=True)
    memory_enabled: Mapped[str] = mapped_column(String(8), nullable=False, default="true")


class SqlAlchemyConfigStore:
    def __init__(self, database_url: str) -> None:
        self._engine = make_engine(database_url)
        Base.metadata.create_all(self._engine)
        self._migrate_schema()

    def _migrate_schema(self) -> None:
        """应用历史累积的附加列迁移（仅旧表；新的 profile_* 表由 create_all 保证）。"""
        from sqlalchemy import text, inspect as sa_inspect
        with self._engine.connect() as conn:
            insp = sa_inspect(self._engine)
            um_cols = {c["name"] for c in insp.get_columns("user_models")}
            if "providers_json" not in um_cols:
                conn.execute(text("ALTER TABLE user_models ADD COLUMN providers_json TEXT"))
                conn.commit()

    # -------------------- model --------------------

    @staticmethod
    def _to_model_config(row) -> UserModelConfig:
        return UserModelConfig(
            user_id=row.user_id,
            model=row.model,
            api_key=row.api_key,
            base_url=row.base_url,
            providers_json=row.providers_json,
        )

    def get_user_model(
        self, user_id: str, profile_id: str | None = None
    ) -> UserModelConfig | None:
        with Session(self._engine) as db:
            if _is_default_profile(user_id, profile_id):
                row = db.get(UserModelRow, user_id)
            else:
                row = db.get(ProfileModelRow, profile_id)
            return self._to_model_config(row) if row is not None else None

    def set_user_model(
        self,
        *,
        user_id: str,
        model: str | None,
        api_key: str | None,
        base_url: str | None,
        providers_json: str | None = None,
        profile_id: str | None = None,
    ) -> UserModelConfig:
        with Session(self._engine) as db:
            if _is_default_profile(user_id, profile_id):
                row = db.get(UserModelRow, user_id)
                if row is None:
                    row = UserModelRow(user_id=user_id)
                    db.add(row)
            else:
                row = db.get(ProfileModelRow, profile_id)
                if row is None:
                    row = ProfileModelRow(profile_id=profile_id, user_id=user_id)
                    db.add(row)
                else:
                    row.user_id = user_id
            row.model = model
            row.api_key = api_key
            row.base_url = base_url
            row.providers_json = providers_json
            db.commit()
            db.refresh(row)
            return self._to_model_config(row)

    # -------------------- toolsets --------------------

    @staticmethod
    def _to_toolsets_config(row) -> UserToolsetsConfig:
        return UserToolsetsConfig(
            user_id=row.user_id,
            toolsets=list(json.loads(row.toolsets_json or "[]")),
        )

    def get_user_toolsets(
        self, user_id: str, profile_id: str | None = None
    ) -> UserToolsetsConfig | None:
        with Session(self._engine) as db:
            if _is_default_profile(user_id, profile_id):
                row = db.get(UserToolsetsRow, user_id)
            else:
                row = db.get(ProfileToolsetsRow, profile_id)
            return self._to_toolsets_config(row) if row is not None else None

    def set_user_toolsets(
        self,
        *,
        user_id: str,
        toolsets: list[str],
        profile_id: str | None = None,
    ) -> UserToolsetsConfig:
        with Session(self._engine) as db:
            if _is_default_profile(user_id, profile_id):
                row = db.get(UserToolsetsRow, user_id)
                if row is None:
                    row = UserToolsetsRow(user_id=user_id)
                    db.add(row)
            else:
                row = db.get(ProfileToolsetsRow, profile_id)
                if row is None:
                    row = ProfileToolsetsRow(profile_id=profile_id, user_id=user_id)
                    db.add(row)
                else:
                    row.user_id = user_id
            row.toolsets_json = json.dumps(list(toolsets))
            db.commit()
            db.refresh(row)
            return self._to_toolsets_config(row)

    # -------------------- agents config --------------------

    @staticmethod
    def _to_agents_config(row) -> UserAgentsConfig:
        return UserAgentsConfig(
            user_id=row.user_id,
            system_prompt=row.system_prompt,
            memory_enabled=row.memory_enabled.lower() != "false",
        )

    def get_user_agents_config(
        self, user_id: str, profile_id: str | None = None
    ) -> UserAgentsConfig | None:
        with Session(self._engine) as db:
            if _is_default_profile(user_id, profile_id):
                row = db.get(UserAgentsConfigRow, user_id)
            else:
                row = db.get(ProfileAgentsConfigRow, profile_id)
            return self._to_agents_config(row) if row is not None else None

    def set_user_agents_config(
        self,
        *,
        user_id: str,
        system_prompt: str | None,
        memory_enabled: bool,
        profile_id: str | None = None,
    ) -> UserAgentsConfig:
        with Session(self._engine) as db:
            if _is_default_profile(user_id, profile_id):
                row = db.get(UserAgentsConfigRow, user_id)
                if row is None:
                    row = UserAgentsConfigRow(user_id=user_id)
                    db.add(row)
            else:
                row = db.get(ProfileAgentsConfigRow, profile_id)
                if row is None:
                    row = ProfileAgentsConfigRow(profile_id=profile_id, user_id=user_id)
                    db.add(row)
                else:
                    row.user_id = user_id
            row.system_prompt = system_prompt
            row.memory_enabled = "true" if memory_enabled else "false"
            db.commit()
            db.refresh(row)
            return self._to_agents_config(row)
