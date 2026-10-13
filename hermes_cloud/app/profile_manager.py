"""
ProfileManager — per-profile hermes gateway process lifecycle.

Each hermes_cloud profile gets its own `hermes gateway` process running on a
dynamically allocated port. The cloud layer acts as a thin auth + routing
proxy that forwards /v1/* requests to the right process.

Architecture:
    Client → Hermes Cloud (auth + routing) → hermes gateway (per-profile process)
                                                    ↓
                                          ~/.hermes/profiles/{profile_id}/
                                              skills/  memory/  config.yaml

**Default-profile convention.** A user's default profile has
``profile.id == user.id``. This means the on-disk directory at
``~/.hermes/profiles/{user_id}/`` is simultaneously the default profile's
directory — existing users don't need any file migration. Non-default profiles
get generated IDs and live at ``~/.hermes/profiles/{generated_id}/``.
"""
from __future__ import annotations

import asyncio
import json
import logging
import os
import time
from dataclasses import dataclass, field
from pathlib import Path
from typing import Callable, Optional

import httpx
import yaml

logger = logging.getLogger(__name__)

_HEALTH_POLL_INTERVAL = 0.5   # seconds between health-check polls
# Spawn health window. 30s was marginal: a gateway boot re-applies hermes
# patches + starts platform adapters and measures 30-40s under load (prod
# deploy waits 90s for the same reason) — eager/keepalive spawns flaked at
# exactly 30s (2026-07-07). Env-overridable for tests.
_SPAWN_HEALTH_TIMEOUT = float(os.environ.get("HERMES_CLOUD_SPAWN_HEALTH_TIMEOUT", "90"))

# ── scale-to-zero (②a) ────────────────────────────────────────────────────
# 云端多租户资源模型:空闲的 per-profile 网关子进程自动休眠(进程退出、
# 释放 RAM/CPU),下次有请求/入站时 _ensure_running_locked 按需重生(唤醒)。
#
# 为什么在 ProfileManager 层做,而不是直接开 hermes 的 HERMES_SCALE_TO_ZERO:
# hermes/gateway/scale_to_zero.py 的 watcher 只在「messaging 为 relay-only/无
# + 注册了 wakeUrl」时 arm(Fly.io 拓扑),且 arm 后也只是 go_dormant() 关掉
# relay socket、进程留活,靠 Fly autostop:"suspend" 冻结机器。云端网关是直连
# (Feishu/DingTalk WS + CRM API server,无 relay、无 wakeUrl),那套 watcher
# 永远不会 arm;裸机 prod 也没有 Fly 来冻结进程。所以真正能「scale to zero」
# 的杠杆在这一层:空闲判定 + SIGTERM(带 drain)让子进程退出 + 按需重生。
#
# 开关:HERMES_CLOUD_SCALE_TO_ZERO(truthy 启用,dev+prod 都开)。
# 空闲阈值:HERMES_CLOUD_IDLE_TIMEOUT_MINUTES(默认 5 分钟)。
# ── multiplex (B2) ─────────────────────────────────────────────────────────
# 云端拓扑开关:关(缺省/falsey)= 现有「每 profile 一个 hermes 网关子进程 +
# 按端口反代」路径,逐字节不变(prod 在跑的路径);开 = 「一个 mux 网关服务全部
# profile + 按 /p/{profile}/ 前缀路由」。全部新逻辑 gated 在此环境变量,开关关时
# 不触发任何 mux 代码。见 docs/superpowers/specs/2026-07-24-multiplex-B2-*.md。
_CLOUD_MULTIPLEX_ENV = "HERMES_CLOUD_MULTIPLEX"

_CLOUD_SCALE_TO_ZERO_ENV = "HERMES_CLOUD_SCALE_TO_ZERO"
_CLOUD_IDLE_TIMEOUT_ENV = "HERMES_CLOUD_IDLE_TIMEOUT_MINUTES"
_DEFAULT_CLOUD_IDLE_TIMEOUT_MINUTES = 5.0
# 休眠 reaper 的巡检周期(秒)。取阈值的一小段,但不低于 30s / 不高于 60s,
# 避免空转唤醒过密或反应太慢。
_REAPER_MIN_INTERVAL = 30.0
_REAPER_MAX_INTERVAL = 60.0
_TRUTHY = {"1", "true", "yes", "on"}
# 显式关闭 multiplex 的取值(紧急 opt-out)。multiplex 现为默认拓扑,只有
# 明确设成这些值才回退到「每 profile 一进程」的非 multiplex 老路径。
_FALSEY = {"0", "false", "no", "off"}

# 网关 SIGTERM 后优雅 drain 在途 agent 回合的默认预算(秒)。种进每个 profile
# 的 config.yaml agent.restart_drain_timeout,让 hermes stop() 走 _drain_active_
# agents(该值)而不是立刻打断在途回合。ProfileManager 停子进程时也按此值 +
# 余量等待,再回落 SIGKILL。prod 的 systemd TimeoutStopSec 必须 ≥ 此值 + 余量。
_DEFAULT_RESTART_DRAIN_TIMEOUT = 30.0


def cloud_scale_to_zero_enabled(environ: Optional[dict] = None) -> bool:
    """云端 scale-to-zero 总开关(HERMES_CLOUD_SCALE_TO_ZERO)。

    缺省/空/falsey → 关闭(fail-safe:不开时行为与今天完全一致,子进程常驻)。
    """
    env = environ if environ is not None else os.environ
    return str(env.get(_CLOUD_SCALE_TO_ZERO_ENV, "")).strip().lower() in _TRUTHY


def cloud_idle_timeout_seconds(environ: Optional[dict] = None) -> float:
    """空闲阈值(秒),来自 HERMES_CLOUD_IDLE_TIMEOUT_MINUTES(默认 5 分钟)。

    非数值/非正值一律回落默认——阈值≤0 会让刚起的进程被立刻回收,绝非本意。
    """
    env = environ if environ is not None else os.environ
    raw = env.get(_CLOUD_IDLE_TIMEOUT_ENV, "")
    try:
        minutes = float(raw)
    except (TypeError, ValueError):
        minutes = _DEFAULT_CLOUD_IDLE_TIMEOUT_MINUTES
    if minutes <= 0:
        minutes = _DEFAULT_CLOUD_IDLE_TIMEOUT_MINUTES
    return minutes * 60.0


def _apply_user_char_limit(existing: dict) -> dict:
    """CRM 产品把 USER.md 预算从默认 1375 提到 2750 —— 给「话术风格画像」
    留约 2000 字、其余 750 给通用用户画像。hermes 配置深合并,只写此键。
    """
    mem = existing.get("memory")
    if not isinstance(mem, dict):
        mem = {}
    mem["user_char_limit"] = 2750
    existing["memory"] = mem
    return existing


@dataclass
class _ProcessEntry:
    profile_id: str
    user_id: str
    port: int
    process: Optional[asyncio.subprocess.Process] = None
    pid: Optional[int] = None  # populated when adopted; else read from .process
    last_active: float = field(default_factory=time.monotonic)
    log_file: object = None   # open file handle for gateway.log; None for adopted

    @property
    def is_running(self) -> bool:
        # Charter v2 §一系统 — uvicorn restart MUST NOT kill the IM
        # long-connection chain. When this entry was adopted from a
        # surviving subprocess (process=None, pid set), liveness is
        # checked via the OS rather than .returncode.
        if self.process is not None:
            return self.process.returncode is None
        if self.pid is not None:
            try:
                os.kill(self.pid, 0)
                return True
            except (ProcessLookupError, PermissionError):
                return False
        return False

    @property
    def effective_pid(self) -> Optional[int]:
        if self.process is not None:
            return self.process.pid
        return self.pid

    def touch(self) -> None:
        self.last_active = time.monotonic()

    def idle_seconds(self) -> float:
        return time.monotonic() - self.last_active


def _home_channel_env_overrides(platforms: object) -> dict[str, str]:
    """Project structured ``platforms.<chan>.home_channel`` into the
    ``*_HOME_CHANNEL`` env vars hermes' cron scheduler reads.

    The per-profile-bot model stores the cron-delivery target in
    config.yaml under ``platforms.<chan>.home_channel`` (written at
    QR-bind time by ``channel_config.set_home_channel``). Hermes'
    scheduler, however, resolves the target from ``*_HOME_CHANNEL`` env
    vars only — it does not read the structured field. The cloud
    therefore projects the structured value into env at spawn, the same
    bridging pattern used for provider API keys.

    Reuses hermes' own platform->env-var map so non-standard names
    (matrix -> MATRIX_HOME_ROOM, email -> EMAIL_HOME_ADDRESS) stay
    correct. Returns ``{}`` on any shape/import problem — a missing
    bridge degrades to "cron skips that channel", never a spawn crash.
    """
    if not isinstance(platforms, dict):
        return {}
    try:
        from cron.scheduler_delivery import _HOME_TARGET_ENV_VARS
    except Exception as exc:
        # Loud, not silent: if hermes renames/moves this symbol, the bridge
        # would no-op and cron would silently stop delivering again — the
        # exact failure mode this fix exists to kill. Surface it in logs.
        logger.warning(
            "home_channel env bridge disabled: cannot import "
            "cron.scheduler_delivery._HOME_TARGET_ENV_VARS (%s)", exc,
        )
        return {}
    overrides: dict[str, str] = {}
    for channel, entry in platforms.items():
        if not isinstance(entry, dict):
            continue
        home = entry.get("home_channel")
        if not isinstance(home, dict):
            continue
        chat_id = str(home.get("chat_id") or "").strip()
        if not chat_id:
            continue
        env_var = _HOME_TARGET_ENV_VARS.get(str(channel).lower())
        if not env_var:
            continue
        overrides[env_var] = chat_id
        thread_id = str(home.get("thread_id") or "").strip()
        if thread_id:
            overrides[f"{env_var}_THREAD_ID"] = thread_id
    return overrides


def home_channel_env_for_profile(profile_dir: "Path") -> dict[str, str]:
    """Read a profile's config.yaml and project its channel home_channels
    into ``*_HOME_CHANNEL`` env overrides. Thin I/O wrapper around
    ``_home_channel_env_overrides`` — see that function for rationale.
    """
    from hermes_cloud.app.channel_config import read_profile_config

    platforms = read_profile_config(profile_dir).get("platforms")
    return _home_channel_env_overrides(platforms)


_LEGACY_TOP_LEVEL_CHANNEL_KEYS = (
    "FEISHU_HOME_CHANNEL", "WEIXIN_HOME_CHANNEL", "WECHAT_HOME_CHANNEL",
    "FEISHU_HOME_CHANNEL_NAME", "FEISHU_HOME_CHANNEL_THREAD_ID",
    "WEIXIN_TOKEN", "WEIXIN_ACCOUNT_ID", "WEIXIN_BASE_URL", "WEIXIN_CDN_BASE_URL",
)


def _scrub_legacy_channel_residue(cfg: dict) -> tuple[bool, dict]:
    """Remove inert legacy channel residue from a profile config dict.

    Returns ``(changed, cfg)``. Idempotent. Removes:
    - top-level dead ``*_HOME_CHANNEL`` / ``WEIXIN_*`` scalars (never read
      as env; the live home_channel lives under platforms.<chan>);
    - the redundant ``platforms.weixin.extra.user_id`` (duplicate of the
      weixin home_channel chat_id; adapter does not depend on it).

    Uses a precise allowlist — never wildcard-deletes.
    """
    if not isinstance(cfg, dict):
        return False, cfg
    changed = False
    for key in _LEGACY_TOP_LEVEL_CHANNEL_KEYS:
        if key in cfg:
            del cfg[key]
            changed = True
    platforms = cfg.get("platforms")
    if isinstance(platforms, dict):
        weixin = platforms.get("weixin")
        if isinstance(weixin, dict):
            extra = weixin.get("extra")
            if isinstance(extra, dict) and "user_id" in extra:
                del extra["user_id"]
                changed = True
    return changed, cfg


class ProfileManager:
    """
    Manages one `hermes gateway` process per profile.

    Call `await profile_manager.startup()` from your FastAPI lifespan and
    `await profile_manager.shutdown()` on teardown.
    """

    def __init__(
        self,
        *,
        internal_key: str,
        port_range_start: int = 9100,
        port_range_end: int = 9199,
        idle_timeout: float = 0,  # kept for signature compat; no longer used
        hermes_bin: str = "hermes",
        hermes_home_root: Optional[Path] = None,
        agent_event_url: Optional[str] = None,
        interaction_log_url: Optional[str] = None,
        user_id_resolver: Optional[Callable[[str], Optional[str]]] = None,
    ) -> None:
        self._internal_key = internal_key
        self._hermes_bin = hermes_bin
        self._hermes_home = hermes_home_root or (Path.home() / ".hermes")
        # Base URL for the gateway → cloud touchpoint tap. Email and
        # WeChat platforms POST every inbound (and, for email, every
        # outbound) message to ``{interaction_log_url}/api/touchpoints/
        # internal/ingest/<source>`` so the SPA ChatBox can show the
        # conversation. Auth: HERMES_CLOUD_INTERNAL_KEY.
        self._interaction_log_url = interaction_log_url
        # Authoritative profile_id → cloud user_id lookup. Eager-spawn
        # at startup has no other way to learn the real cloud user_id
        # (the cloud_subprocess.json registry can be stale/poisoned),
        # and passing the wrong value breaks CRM_USER_ID — crm_tools
        # needs an integer cloud user_id or it registers nothing.
        self._user_id_resolver = user_id_resolver
        # When set, hermes-patches/09 makes hermes_state.append_message
        # POST every agent message to this URL (cloud's
        # /internal/agent/event) so it lands in the canonical
        # interactions table. Charter §一定律 1 + 原语 ①.
        self._agent_event_url = agent_event_url

        # Port pool
        self._free_ports: set[int] = set(range(port_range_start, port_range_end + 1))
        # Running processes keyed by profile_id
        self._processes: dict[str, _ProcessEntry] = {}
        # Per-profile asyncio locks to prevent double-start races
        self._profile_locks: dict[str, asyncio.Lock] = {}
        # ②a scale-to-zero:后台 idle reaper 任务句柄(startup 起、shutdown 停)
        self._reaper_task: Optional[asyncio.Task] = None
        # Resident keepalive (2026-07-07 feishu-deaf incident): profiles that
        # MUST stay resident (cron ticker / persistent IM WebSocket lives in
        # the gateway process) get revived if their process dies. Manual
        # stops are honored for a grace window so keepalive doesn't fight a
        # deliberate stop_profile().
        self._keepalive_task: Optional[asyncio.Task] = None
        self._manual_stops: dict[str, float] = {}

    # ------------------------------------------------------------------
    # Lifecycle
    # ------------------------------------------------------------------

    async def startup(self) -> None:
        """Start the ProfileManager (process watchdog).

        Charter v2 §一系统 — adopt any per-profile hermes subprocess
        that survived a uvicorn restart. Without this, profile_manager
        forgets the existing subprocess on each cloud reboot, then
        spawns a duplicate on next dispatch — the duplicate races for
        the same iLink WebSocket session and kicks the survivor off
        the iLink server (errcode=-14 session timeout). With adoption,
        cloud restart is transparent to the IM long-connection chain.

        After adoption, eagerly spawn subprocesses for any profile that
        has cron jobs configured but no running subprocess. The cron
        ticker lives INSIDE each profile's gateway process — if the
        profile is dormant, its scheduled jobs (daily-coach,
        weekly-digest, etc.) silently never fire. Eager-spawn keeps
        every profile-with-crons alive 24/7 so its schedules tick.
        """
        adopted = await self._adopt_existing_subprocesses()
        eager = await self._eager_spawn_profiles_with_crons()
        # ②a scale-to-zero:启动空闲 reaper(仅当云端开关打开)。
        if cloud_scale_to_zero_enabled() and self._reaper_task is None:
            self._reaper_task = asyncio.create_task(self._idle_reaper_loop())
        # Resident keepalive — always on (unless env kill-switch). The idle
        # reaper only ever KILLS; nothing revived a resident profile whose
        # gateway died mid-flight (crash, stray kill, orphan cleanup). The
        # 2026-07-07 incident: admin's gateway got killed during orphan
        # cleanup; its Feishu WebSocket listener lives IN that process, so
        # inbound feishu messages fell into the void until a manual SPA
        # request happened to respawn it. Keepalive bounds that deafness to
        # one check interval.
        if (
            not os.environ.get("HERMES_CLOUD_NO_RESIDENT_KEEPALIVE")
            and self._keepalive_task is None
        ):
            self._keepalive_task = asyncio.create_task(
                self._resident_keepalive_loop()
            )
        logger.info(
            "ProfileManager started: %d ports available (%d–%d), adopted=%d, "
            "eager_spawned=%d, scale_to_zero=%s (idle_timeout=%.0fs)",
            len(self._free_ports),
            min(self._free_ports) if self._free_ports else 0,
            max(self._free_ports) if self._free_ports else 0,
            adopted,
            eager,
            "on" if cloud_scale_to_zero_enabled() else "off",
            cloud_idle_timeout_seconds(),
        )

    async def _eager_spawn_profiles_with_crons(self) -> int:
        """Spawn a hermes subprocess for any profile whose ``cron/jobs.json``
        has at least one enabled job AND who isn't already running.

        Disabled via ``HERMES_CLOUD_NO_EAGER_CRON_SPAWN=1`` (e.g. tests
        or multi-tenant deployments that prefer lazy spawn + a
        separate cron-only worker process)."""
        if os.environ.get("HERMES_CLOUD_NO_EAGER_CRON_SPAWN"):
            return 0
        profiles_root = self._hermes_home / "profiles"
        if not profiles_root.exists():
            return 0
        spawned = 0
        for child in profiles_root.iterdir():
            if not child.is_dir():
                continue
            profile_id = child.name
            if profile_id in self._processes:
                continue  # already running / adopted
            # Residency = enabled crons OR persistent inbound IM channel
            # (2026-07-07: the boot eager-spawn only knew about crons, so an
            # IM-bound profile whose surviving process later died had nothing
            # to bring its WebSocket listener back — see
            # _resident_keepalive_loop for the mid-flight half of the fix).
            if not self._profile_must_stay_resident(profile_id):
                continue
            user_id = self._resolve_spawn_user_id(profile_id)
            try:
                await self.ensure_running(profile_id, user_id=user_id)
                spawned += 1
                logger.info(
                    "eager_spawn profile=%s (resident: crons or IM channel)",
                    profile_id,
                )
            except Exception:
                logger.exception(
                    "eager_spawn failed profile=%s", profile_id,
                )
        return spawned

    # ------------------------------------------------------------------
    # ②a scale-to-zero: idle reaper
    # ------------------------------------------------------------------

    def _reaper_interval_seconds(self) -> float:
        """巡检周期:取空闲阈值的一小段,夹在 [30s, 60s]。"""
        idle = cloud_idle_timeout_seconds()
        return max(_REAPER_MIN_INTERVAL, min(_REAPER_MAX_INTERVAL, idle / 4.0))

    def _profile_has_enabled_crons(self, profile_id: str) -> bool:
        """该 profile 是否有启用中的 cron 任务。

        有 cron 的 profile 必须常驻 24/7(cron ticker 活在网关进程内,进程一睡
        它的定时任务就静默不触发)——这类 profile 永不休眠。与 _eager_spawn_
        profiles_with_crons 的判定口径一致。读文件失败一律按「有 cron」保守处理
        (宁可不睡、别误杀定时任务)。
        """
        jobs_path = self._hermes_home / "profiles" / profile_id / "cron" / "jobs.json"
        if not jobs_path.exists():
            return False
        try:
            doc = json.loads(jobs_path.read_text() or "{}")
        except Exception:
            logger.debug(
                "reaper: cron jobs.json parse failed for %s — treating as has-cron",
                profile_id, exc_info=True,
            )
            return True
        return any(j.get("enabled", True) for j in doc.get("jobs", []))

    def _profile_has_persistent_inbound_channel(self, profile_id: str) -> bool:
        """该 profile 是否绑了持久入站 IM 渠道(Feishu/DingTalk/WeCom/… WS)。

        这类渠道的入站长连接活在**网关进程内**,inbound 直接打到网关的
        WebSocket、**不经过**云端 ensure_running。若把这种网关休眠,长连接会断
        (charter §一系统 明令保护),而云端无从感知入站、也就不会唤醒 → 消息丢。
        所以绑了此类渠道的 profile 与 cron profile 一样:必须常驻、永不休眠。

        判定:config.yaml ``platforms.<chan>`` 存在任一 WS 型渠道条目。纯 webhook
        渠道(email 入站等)由云端路由、不占进程内长连接,不在此列。读失败一律
        按「有长连接」保守处理(宁可不睡)。
        """
        profile_dir = self._hermes_home / "profiles" / profile_id
        config_file = profile_dir / "config.yaml"
        if not config_file.exists():
            return False
        try:
            cfg = yaml.safe_load(config_file.read_text()) or {}
        except Exception:
            logger.debug(
                "reaper: config.yaml parse failed for %s — treating as "
                "has-inbound-channel", profile_id, exc_info=True,
            )
            return True
        platforms = cfg.get("platforms")
        if not isinstance(platforms, dict):
            return False
        # 进程内持有入站长连接的渠道(WS/长轮询)。email 等纯 webhook 不算。
        _WS_CHANNELS = {
            "feishu", "lark", "dingtalk", "wecom", "weixin", "wechat",
            "telegram", "discord", "slack", "matrix", "signal", "whatsapp",
        }
        for chan, entry in platforms.items():
            if str(chan).strip().lower() in _WS_CHANNELS and isinstance(entry, dict) and entry:
                return True
        return False

    def _profile_must_stay_resident(self, profile_id: str) -> bool:
        """常驻资格 = 有启用 cron **或** 绑了进程内长连接 IM 渠道。

        与 reaper 的免死口径完全一致(同两个判定函数),保证「reaper 永不杀的
        集合」==「keepalive 负责救活的集合」,两个循环不会打架。
        """
        return (
            self._profile_has_enabled_crons(profile_id)
            or self._profile_has_persistent_inbound_channel(profile_id)
        )

    def _resolve_spawn_user_id(self, profile_id: str) -> str:
        """Resolve the cloud user_id for a spawn (resolver → registry → fallback).

        Extracted from _eager_spawn_profiles_with_crons so the keepalive loop
        uses the identical resolution order (resolver is authoritative;
        registry may hold a stale/poisoned value; profile_id is last resort).
        """
        user_id: Optional[str] = None
        if self._user_id_resolver is not None:
            try:
                user_id = self._user_id_resolver(profile_id)
            except Exception:
                logger.warning(
                    "spawn user_id_resolver failed for %s", profile_id,
                    exc_info=True,
                )
        if not user_id:
            try:
                reg = json.loads(
                    (self._hermes_home / "profiles" / profile_id /
                     "cloud_subprocess.json").read_text()
                )
                reg_uid = str(reg.get("user_id") or "")
                if reg_uid and reg_uid != profile_id:
                    user_id = reg_uid
            except Exception:
                pass
        return user_id or profile_id

    def _mux_spawn_user_id(self) -> str:
        """Pick a user_id to (re)spawn the shared mux gateway with.

        The mux gateway serves ALL tenants; its ``entry.user_id`` carries no
        business meaning — identity is per-turn under B1, and ``stop_user``
        skips ``__mux__`` (R1-1). Any stable value works; we resolve the first
        profile's user_id for readable logs, falling back to ``"default"`` when
        no profile dirs exist yet.
        """
        profiles_root = self._hermes_home / "profiles"
        try:
            for child in sorted(profiles_root.iterdir()):
                if child.is_dir() and child.name != self._MUX_KEY:
                    return self._resolve_spawn_user_id(child.name)
        except FileNotFoundError:
            pass
        return "default"

    _KEEPALIVE_INTERVAL_SECONDS = 45.0
    _MANUAL_STOP_GRACE_SECONDS = 600.0

    async def _resident_keepalive_loop(self) -> None:
        """Revive resident profiles whose gateway process died.

        The 2026-07-07 feishu-deaf incident: the idle reaper only ever KILLS
        and dispatch only spawns on inbound HTTP — but a persistent-IM
        profile's inbound arrives via a WebSocket held INSIDE the gateway
        process. Dead process ⇒ no listener ⇒ nothing ever triggers a
        respawn (chicken-and-egg), and inbound IM messages are silently
        lost. This loop closes that hole: every tick, any must-stay-resident
        profile without a live process is respawned — unless the operator
        stopped it deliberately within the grace window.
        """
        interval = float(
            os.environ.get("HERMES_CLOUD_KEEPALIVE_INTERVAL")
            or self._KEEPALIVE_INTERVAL_SECONDS
        )
        while True:
            try:
                await asyncio.sleep(interval)
                # R1-2: multiplex topology. The shared mux gateway holds EVERY
                # tenant's IM WebSocket + cron ticker in-process. If it dies,
                # all tenants go deaf — and the per-profile dir scan below can
                # never revive it (``__mux__`` is a sentinel, not a profile
                # dir), so IM stays permanently silent. Revive it here, gated
                # on the flag, then skip the per-profile scan (in multiplex
                # there are no per-profile subprocesses — every profile is
                # served by the mux). Flag-off: this branch is skipped and the
                # per-profile revival below runs byte-for-byte as before.
                if self._multiplex_enabled():
                    try:
                        await self._ensure_mux_running(self._mux_spawn_user_id())
                    except Exception:
                        logger.exception(
                            "resident_keepalive: mux ensure_running failed"
                        )
                    continue
                profiles_root = self._hermes_home / "profiles"
                if not profiles_root.exists():
                    continue
                for child in profiles_root.iterdir():
                    if not child.is_dir():
                        continue
                    profile_id = child.name
                    entry = self._processes.get(profile_id)
                    if entry is not None and entry.is_running:
                        continue
                    stopped_at = self._manual_stops.get(profile_id)
                    if (
                        stopped_at is not None
                        and time.monotonic() - stopped_at
                        < self._MANUAL_STOP_GRACE_SECONDS
                    ):
                        continue
                    if not self._profile_must_stay_resident(profile_id):
                        continue
                    logger.warning(
                        "resident_keepalive: profile=%s gateway not running "
                        "(entry=%s) — respawning (cron ticker / IM WebSocket "
                        "listener lives in-process)",
                        profile_id, "dead" if entry is not None else "absent",
                    )
                    try:
                        await self.ensure_running(
                            profile_id,
                            user_id=self._resolve_spawn_user_id(profile_id),
                        )
                    except Exception:
                        logger.exception(
                            "resident_keepalive: respawn failed profile=%s",
                            profile_id,
                        )
            except asyncio.CancelledError:
                raise
            except Exception:
                # Never let the keepalive loop die on a transient error.
                logger.exception("resident_keepalive: tick failed")

    async def _gateway_is_busy(self, port: int) -> Optional[bool]:
        """探网关 ``/health/detailed`` 判在途:True=有在途回合/正在 drain。

        由网关进程自己汇报 ``active_agents`` / ``gateway_busy``(权威、无竞态)。
        返回 None 表示探测失败(超时/连接错)——调用方按「不确定 → 不休眠」处理,
        绝不在探测失败时误杀可能忙碌的网关。
        """
        url = f"http://127.0.0.1:{port}/health/detailed"
        try:
            async with httpx.AsyncClient(timeout=2.0) as client:
                r = await client.get(
                    url, headers={"Authorization": f"Bearer {self._internal_key}"}
                )
        except httpx.HTTPError:
            return None
        if r.status_code >= 500:
            return None
        try:
            body = r.json()
        except Exception:
            return None
        if body.get("gateway_busy"):
            return True
        try:
            active = int(body.get("active_agents", 0) or 0)
        except (TypeError, ValueError):
            active = 0
        return active > 0

    async def _idle_reaper_loop(self) -> None:
        """后台循环:把空闲的 per-profile 网关子进程休眠(scale to zero)。

        每 ``_reaper_interval_seconds`` 巡检一次。对每个我们**自己 spawn**的、
        且空闲超阈值的 profile:
          - 跳过 adopted 条目(process=None、pid set):那是跨云重启存活的 IM
            长连接子进程,不归我们生命周期管,SIGTERM 它会踢掉长连接;
          - 跳过有启用 cron 的 profile(必须 24/7 常驻);
          - 跳过绑了持久入站 IM 渠道的 profile(休眠会断长连接、丢入站);
          - 探 /health/detailed,只有网关自报「无在途回合、不忙」才停;
          - 停用 stop_profile()(SIGTERM → hermes drain 在途回合 → 退出)。
        下次有请求/入站时 _ensure_running_locked 会按需重生(唤醒)——这条路径
        今天已存在,把「进程 returncode 非 None」当作正常「重新起一个」,没有退避、
        不标记 profile 失败,所以「干净休眠退出」不会被误判为崩溃。

        never-crash:任何单次巡检异常都吞掉,循环继续。
        """
        logger.info(
            "idle reaper started (interval=%.0fs, idle_timeout=%.0fs)",
            self._reaper_interval_seconds(), cloud_idle_timeout_seconds(),
        )
        # 起步先等一个周期,让 startup 的 adopt/eager-spawn 稳定。
        try:
            await asyncio.sleep(self._reaper_interval_seconds())
        except asyncio.CancelledError:
            return
        while True:
            try:
                await asyncio.sleep(self._reaper_interval_seconds())
                await self._reap_idle_once()
            except asyncio.CancelledError:
                raise
            except Exception:  # noqa: BLE001 — reaper 绝不能因单次异常而死
                logger.debug("idle reaper iteration error", exc_info=True)

    async def _reap_idle_once(self) -> None:
        """跑一次空闲巡检(拆出来便于单测)。"""
        idle_timeout = cloud_idle_timeout_seconds()
        # 快照候选,避免遍历中字典被 dispatch 改动。
        candidates: list[tuple[str, int]] = []
        for profile_id, entry in list(self._processes.items()):
            # B3: 共享 mux 网关(multiplex)服务全部租户,"空闲"对它无意义——
            # 绝不 idle-reap,否则一停就是全租户离线。它由 _ensure_mux_running
            # 管理生命周期,不走 scale-to-zero。
            if profile_id == self._MUX_KEY:
                continue
            # adopted 条目(无 process 句柄)不归我们管——跳过。
            if entry.process is None:
                continue
            if not entry.is_running:
                continue
            if entry.idle_seconds() < idle_timeout:
                continue
            if self._profile_has_enabled_crons(profile_id):
                continue
            # 绑了持久入站 IM 渠道的 profile 必须常驻:休眠会断长连接、丢入站。
            if self._profile_has_persistent_inbound_channel(profile_id):
                continue
            candidates.append((profile_id, entry.port))

        for profile_id, port in candidates:
            # 停之前再确认:仍在、仍空闲、仍无在途回合。
            entry = self._processes.get(profile_id)
            if entry is None or entry.process is None or not entry.is_running:
                continue
            if entry.idle_seconds() < idle_timeout:
                continue
            busy = await self._gateway_is_busy(port)
            if busy is None:
                # 探测失败:不确定是否忙 → 这轮不睡,下轮再看。
                logger.debug(
                    "reaper: health probe failed for %s (port %d) — skipping",
                    profile_id, port,
                )
                continue
            if busy:
                # 有在途回合:摸一下 idle 计时器,别让它立刻又成候选。
                entry.touch()
                continue
            logger.info(
                "scale-to-zero: profile %s idle >= %.0fs and not busy — "
                "sleeping (SIGTERM + drain; next request will wake it)",
                profile_id, idle_timeout,
            )
            try:
                await self.stop_profile(profile_id)
            except Exception:  # noqa: BLE001 — 单个 profile 停不下来不该拖垮巡检
                logger.warning(
                    "reaper: stop_profile failed for %s", profile_id, exc_info=True,
                )

    def _registry_path(self, profile_id: str) -> Path:
        """Where (pid, port) is persisted so the next cloud boot can adopt."""
        return self._hermes_home / "profiles" / profile_id / "cloud_subprocess.json"

    def _persist_registry(self, entry: _ProcessEntry) -> None:
        try:
            path = self._registry_path(entry.profile_id)
            path.parent.mkdir(parents=True, exist_ok=True)
            payload = {
                "profile_id": entry.profile_id,
                "user_id": entry.user_id,
                "pid": entry.effective_pid,
                "port": entry.port,
                "started_at": time.time(),
            }
            path.write_text(json.dumps(payload))
        except Exception:
            logger.warning(
                "registry persist failed profile=%s", entry.profile_id,
                exc_info=True,
            )

    def _delete_registry(self, profile_id: str) -> None:
        try:
            self._registry_path(profile_id).unlink()
        except FileNotFoundError:
            pass
        except Exception:
            logger.debug("registry delete failed profile=%s", profile_id, exc_info=True)

    def _evict_stale_survivor(self, profile_id: str, pid: int) -> None:
        """R1-M3: under multiplex, evict a per-profile subprocess left alive by
        a prior flag-off run instead of adopting it.

        If adopted, that survivor would re-open the profile's IM WebSocket and
        race the shared mux gateway for the SAME per-profile credentials — the
        iLink server kicks one off (errcode=-14 session timeout), so the tenant
        flaps. SIGTERM it (best-effort; it may already be gone) and clear its
        registry so the next boot doesn't see it again.
        """
        try:
            os.kill(pid, 15)  # SIGTERM
            logger.warning(
                "adopt: multiplex on — evicted stale per-profile subprocess "
                "profile=%s pid=%d (would race mux for IM WS creds)",
                profile_id, pid,
            )
        except ProcessLookupError:
            pass
        except Exception:
            logger.warning(
                "adopt: SIGTERM stale survivor failed profile=%s pid=%s",
                profile_id, pid, exc_info=True,
            )
        self._delete_registry(profile_id)

    async def _adopt_existing_subprocesses(self) -> int:
        """Scan profile dirs; for each registry entry whose pid is alive
        AND port responds /v1/models healthy, adopt without respawn.
        Reserve the port from the free pool so we don't allocate it twice.

        R1-M3: adoption is flag-gated so a flag flip can't leave two gateways
        racing the same WS creds. Flag-ON adopts ONLY the shared ``__mux__``
        entry and evicts any surviving per-profile subprocess; flag-OFF adopts
        only per-profile entries and ignores a stale ``__mux__`` registry.
        """
        profiles_root = self._hermes_home / "profiles"
        if not profiles_root.exists():
            return 0
        mux_on = self._multiplex_enabled()
        adopted = 0
        for child in profiles_root.iterdir():
            if not child.is_dir():
                continue
            reg_path = child / "cloud_subprocess.json"
            if not reg_path.exists():
                continue
            try:
                payload = json.loads(reg_path.read_text() or "{}")
            except Exception:
                logger.debug("registry parse failed %s", reg_path, exc_info=True)
                continue
            profile_id = str(payload.get("profile_id") or child.name)
            user_id = str(payload.get("user_id") or "")
            pid = payload.get("pid")
            port = payload.get("port")
            if not isinstance(pid, int) or not isinstance(port, int):
                continue
            is_mux = profile_id == self._MUX_KEY
            # R1-M3: flag-gated adoption (see docstring).
            if mux_on and not is_mux:
                # Flag ON: never adopt a per-profile survivor — evict it so it
                # can't race the mux gateway for the profile's WS creds.
                self._evict_stale_survivor(profile_id, pid)
                continue
            if not mux_on and is_mux:
                # Flag OFF: ignore a stale mux registry (per-profile owns WS).
                continue
            # PID alive?
            try:
                os.kill(pid, 0)
            except (ProcessLookupError, PermissionError):
                self._delete_registry(profile_id)
                continue
            # Port responsive?
            healthy = await self._wait_healthy(port, timeout=2.0)
            if not healthy:
                # PID alive but port silent — orphaned partial state.
                # Don't try to kill (might be a different process now);
                # just clean the registry so next dispatch spawns fresh.
                self._delete_registry(profile_id)
                continue
            # Adopt.
            entry = _ProcessEntry(
                profile_id=profile_id,
                user_id=user_id,
                port=port,
                process=None,
                pid=pid,
                log_file=None,
            )
            self._processes[profile_id] = entry
            self._free_ports.discard(port)
            adopted += 1
            logger.info(
                "adopted existing subprocess profile=%s pid=%d port=%d",
                profile_id, pid, port,
            )
        return adopted

    async def shutdown(self) -> None:
        """Cloud shutdown — leave subprocesses ALIVE.

        Charter v2 §一系统 — IM long-connection chain (iLink WS,
        feishu WS) lives in the per-profile subprocess and must not
        die on cloud restart. Subprocesses were spawned with
        ``start_new_session=True`` so they're already detached;
        we just close our log file handles + drop the in-memory dict.
        Next cloud boot's ``startup()`` will adopt them back via the
        ``cloud_subprocess.json`` registry.
        """
        # ②a scale-to-zero:停后台 reaper。绝不在这里回收子进程——子进程按
        # charter §一系统 存活给下次 adopt。
        if self._keepalive_task is not None:
            self._keepalive_task.cancel()
            try:
                await self._keepalive_task
            except (asyncio.CancelledError, Exception):
                pass
            self._keepalive_task = None
        if self._reaper_task is not None:
            self._reaper_task.cancel()
            try:
                await self._reaper_task
            except (asyncio.CancelledError, Exception):
                pass
            self._reaper_task = None
        for entry in list(self._processes.values()):
            if entry.log_file:
                try:
                    entry.log_file.close()
                except Exception:
                    pass
        self._processes.clear()
        logger.info(
            "ProfileManager shut down (subprocesses survive for adoption)."
        )

    # ------------------------------------------------------------------
    # Public API
    # ------------------------------------------------------------------

    async def ensure_running(self, profile_id: str, *, user_id: str | None = None) -> int:
        """
        Return the port for the profile's hermes process.
        Starts (and if needed creates) the process on first call.

        ``user_id`` is used only when first starting the subprocess (so the
        entry knows who owns it for ``stop_user``).

        Charter v2 (2026-05-05) collapsed the profile/user model to **1
        user ↔ 1 profile** — but they're still two identifiers. Consumers
        upstream (auth_store, profile_store) maintain the 1-1 mapping
        via FKs / unique indexes, so callers may legitimately pass
        non-equal ``profile_id`` and ``user_id`` (e.g. profile_id='admin'
        slug + user_id='1' integer). We do NOT enforce equality here.

        Raises:
            RuntimeError: when the process cannot be started.
        """
        lock = self._get_profile_lock(profile_id)
        async with lock:
            effective_user_id = user_id if user_id is not None else profile_id
            return await self._ensure_running_locked(profile_id, effective_user_id)

    def touch(self, profile_id: str) -> None:
        """Refresh the idle timer for a profile's process (call after each request)."""
        entry = self._processes.get(profile_id)
        if entry is not None:
            entry.touch()

    async def stop_profile(self, profile_id: str) -> None:
        """Forcibly stop one profile's process and release its port.

        Explicit stop kills the OS process even if adopted (charter
        v2: user-initiated stop is intentional teardown, distinct from
        cloud-restart graceful handoff).
        """
        lock = self._get_profile_lock(profile_id)
        # Honor the operator's intent: resident keepalive must not undo a
        # deliberate stop for the grace window (see _resident_keepalive_loop).
        self._manual_stops[profile_id] = time.monotonic()
        async with lock:
            entry = self._processes.pop(profile_id, None)
            if entry is not None:
                self._free_ports.add(entry.port)
                # For adopted entries, kill via OS; _terminate_entry
                # only handles the process-handle case.
                if entry.process is None and entry.pid is not None:
                    try:
                        os.kill(entry.pid, 15)  # SIGTERM
                    except ProcessLookupError:
                        pass
                    except Exception:
                        logger.warning(
                            "stop_profile: SIGTERM failed pid=%s", entry.pid,
                            exc_info=True,
                        )
                else:
                    await self._terminate_entry(entry)
                self._delete_registry(profile_id)
                logger.info(
                    "process_stopped profile_id=%s port=%d", profile_id, entry.port
                )

    async def restart_profile(
        self, profile_id: str, *, user_id: str | None = None,
    ) -> int:
        """Stop then re-spawn a profile's subprocess. Returns the new port.

        Used after a channel-credential change: hermes loads
        ``platforms.<channel>`` config at gateway boot, so a new bot
        adapter only connects after a fresh process start. The cloud's
        channel endpoints call this when creds are written so the bot
        comes online without the user manually bouncing the profile.

        Multiplex topology: the profiles all share ONE mux gateway, so
        ``stop_profile`` (SIGTERM) would take every tenant offline. Instead
        we keep the shared gateway running and hot-reload only THIS profile's
        IM adapters via the gateway's internal reload endpoint. The
        per-profile subprocess ``stop + ensure_running`` path is untouched
        when the flag is off (fail-safe: byte-for-byte identical to today).
        """
        if self._multiplex_enabled():
            effective_user_id = user_id if user_id is not None else profile_id
            port = await self._ensure_mux_running(effective_user_id)
            # R1-M2: a running mux won't re-run the cold-start key materialize,
            # and restart_profile is exactly when this profile's provider key
            # may have changed — refresh THIS profile's .env before reload so
            # the next turn resolves the new key.
            try:
                self._materialize_provider_keys_to_env(
                    self._hermes_home / "profiles" / profile_id
                )
            except Exception:
                logger.warning(
                    "restart_profile: materialize provider key failed "
                    "profile=%s", profile_id, exc_info=True,
                )
            url = (
                f"http://127.0.0.1:{port}"
                f"/internal/profiles/{profile_id}/reload-adapters"
            )
            try:
                async with httpx.AsyncClient(timeout=15.0) as client:
                    resp = await client.post(
                        url,
                        headers={"Authorization": f"Bearer {self._internal_key}"},
                    )
                logger.info(
                    "restart_profile: mux reload-adapters profile_id=%s status=%s",
                    profile_id, resp.status_code,
                )
            except Exception:
                # Non-fatal: a failed hot-reload must never take down the
                # shared gateway or 500 the channel endpoint. The bot simply
                # reconnects on the next reconcile / gateway restart.
                logger.warning(
                    "restart_profile: mux reload-adapters POST failed profile_id=%s",
                    profile_id, exc_info=True,
                )
            return port

        await self.stop_profile(profile_id)
        return await self.ensure_running(profile_id, user_id=user_id)

    async def stop_user(self, user_id: str) -> None:
        """Stop every running profile owned by this user.

        Used when deleting a user account or otherwise evicting them
        entirely. For targeted single-profile shutdown use ``stop_profile``.
        """
        # R1-1: NEVER match the shared mux sentinel. The mux entry carries
        # the user_id of whichever tenant first woke it, so a naive
        # ``entry.user_id == user_id`` would let ``stop_user`` SIGTERM the
        # shared gateway and take EVERY tenant offline. Skip it here, mirroring
        # the reaper's guard (``_reap_idle_once``). Flag-off: no mux entry ever
        # exists, so this predicate is a no-op — byte-for-byte identical.
        profile_ids = [
            pid
            for pid, entry in self._processes.items()
            if entry.user_id == user_id and pid != self._MUX_KEY
        ]
        for pid in profile_ids:
            await self.stop_profile(pid)

    def get_running_processes(self) -> list[dict]:
        """Return metadata about all currently tracked processes (for admin UI)."""
        now = time.monotonic()
        # Multiplex: there is ONE shared ``__mux__`` gateway serving every
        # profile, so ``self._processes`` holds a single sentinel entry. The
        # SPA looks each profile up by ``profile_id``; returning only
        # ``__mux__`` would make every real profile render "○ 未运行" even
        # though it IS being served. Fan the mux entry out to one row per
        # served profile (all sharing the mux port/pid), flagged
        # ``multiplexed`` so the UI can label it as a shared gateway. The
        # sentinel ``__mux__`` row itself is NOT returned (it's not a real
        # profile the operator manages).
        if self._multiplex_enabled():
            mux = self._processes.get(self._MUX_KEY)
            if mux is None or not mux.is_running:
                return []
            profiles_root = self._hermes_home / "profiles"
            served = ["default"]
            if profiles_root.is_dir():
                served += sorted(
                    c.name for c in profiles_root.iterdir()
                    # Exclude ``default`` (already seeded) and the ``__mux__``
                    # sentinel dir (provider-key materialization creates a
                    # ``profiles/__mux__`` home; it is NOT a servable profile).
                    if c.is_dir() and c.name not in ("default", self._MUX_KEY)
                )
            idle = int(now - mux.last_active)
            return [
                {
                    "profile_id": name,
                    "user_id": self._resolve_spawn_user_id(name),
                    "port": mux.port,
                    "running": True,
                    "pid": mux.effective_pid,
                    "idle_seconds": idle,
                    "multiplexed": True,
                }
                for name in served
            ]
        # Use ``effective_pid`` (defined on _ProcessEntry) — adopted
        # entries have ``process is None`` because the subprocess
        # outlived the cloud uvicorn that originally spawned it, so
        # ``e.process.pid`` would AttributeError → /api/profiles 500.
        return [
            {
                "profile_id": pid,
                "user_id": e.user_id,
                "port": e.port,
                "running": e.is_running,
                "pid": e.effective_pid,
                "idle_seconds": int(now - e.last_active),
            }
            for pid, e in self._processes.items()
        ]

    # ------------------------------------------------------------------
    # Internal helpers
    # ------------------------------------------------------------------

    def _get_profile_lock(self, profile_id: str) -> asyncio.Lock:
        if profile_id not in self._profile_locks:
            self._profile_locks[profile_id] = asyncio.Lock()
        return self._profile_locks[profile_id]

    async def _ensure_running_locked(self, profile_id: str, user_id: str) -> int:
        # ── multiplex (B2, gated on HERMES_CLOUD_MULTIPLEX) ──────────────
        # Flag ON: every profile is served by ONE shared mux gateway; return
        # its single fixed port for any profile_id and never touch the
        # per-profile path below.
        # Flag OFF (prod today): this ``if`` is a no-op — the per-profile
        # logic that follows is byte-for-byte the path that runs in prod.
        if self._multiplex_enabled():
            return await self._ensure_mux_running(user_id)

        entry = self._processes.get(profile_id)
        if entry is not None:
            if entry.is_running:
                entry.touch()
                return entry.port
            # Process died unexpectedly. For spawned entries we have a
            # Process handle with returncode; for adopted entries we
            # don't (already gone). Both cases: drop the entry, clear
            # registry, free port, then respawn fresh.
            rc = (entry.process.returncode
                  if entry.process is not None else "<adopted-died>")
            logger.warning(
                "process_died profile_id=%s pid=%s returncode=%s — restarting",
                profile_id, entry.effective_pid, rc,
            )
            self._free_ports.add(entry.port)
            if entry.log_file:
                try:
                    entry.log_file.close()
                except Exception:
                    pass
            del self._processes[profile_id]
            self._delete_registry(profile_id)

        return await self._start_for_profile(profile_id, user_id)

    async def _start_for_profile(self, profile_id: str, user_id: str) -> int:
        if not self._free_ports:
            raise RuntimeError(
                "No available ports in the profile port range. "
                "Increase port_range_end or lower idle_timeout_seconds."
            )
        port = self._free_ports.pop()
        try:
            self._configure_profile(profile_id, port)
            # Charter v2 Phase 3 (2026-05-06): system_specialists
            # mirroring removed — specialists are gone, only skill
            # markdown is mirrored (see ``system_skills_bootstrap`` in
            # main.py's lifespan).
            process, log_file = await self._launch_process(
                profile_id, port, user_id=user_id
            )
            healthy = await self._wait_healthy(port, timeout=_SPAWN_HEALTH_TIMEOUT)
            if not healthy:
                # Process may have already exited (e.g. crashed during
                # startup) — terminate()/kill() raise ProcessLookupError
                # on a dead handle. Treat "already gone" as success.
                try:
                    process.terminate()
                    try:
                        await asyncio.wait_for(process.wait(), timeout=5.0)
                    except asyncio.TimeoutError:
                        try:
                            process.kill()
                        except ProcessLookupError:
                            pass
                        await process.wait()
                except ProcessLookupError:
                    pass
                if log_file:
                    log_file.close()
                self._free_ports.add(port)
                raise RuntimeError(
                    f"Hermes process for profile {profile_id!r} failed to pass "
                    f"health check on port {port} within {_SPAWN_HEALTH_TIMEOUT:.0f} s."
                )
            entry = _ProcessEntry(
                profile_id=profile_id,
                user_id=user_id,
                port=port,
                process=process,
                log_file=log_file,
            )
            self._processes[profile_id] = entry
            self._persist_registry(entry)
            logger.info(
                "process_started profile_id=%s user_id=%s port=%d pid=%d",
                profile_id, user_id, port, process.pid,
            )
            return port
        except Exception:
            self._free_ports.add(port)
            raise

    def _configure_profile(self, profile_id: str, port: int) -> None:
        """Write API-server settings into the profile's config.yaml."""
        profile_dir = self._hermes_home / "profiles" / profile_id
        profile_dir.mkdir(parents=True, exist_ok=True)

        config_file = profile_dir / "config.yaml"
        existing: dict = {}
        if config_file.exists():
            try:
                existing = yaml.safe_load(config_file.read_text()) or {}
            except Exception:
                existing = {}

        existing.update(
            {
                "API_SERVER_ENABLED": True,
                "API_SERVER_HOST": "127.0.0.1",
                "API_SERVER_PORT": port,
                "API_SERVER_KEY": self._internal_key,
            }
        )

        existing = _apply_user_char_limit(existing)

        # Legacy-config migration: older hermes_cloud writes stored the model
        # under ``model: { model: xxx }`` without a ``default`` key. The hermes
        # cron scheduler reads ``config["model"]["default"]`` (run_agent.py
        # also prefers it), so a missing ``default`` causes cron jobs to start
        # with model=None. Backfill ``default`` from ``model`` on launch so
        # existing users don't have to re-save their model settings in the UI.
        model_section = existing.get("model")
        if isinstance(model_section, dict):
            legacy_model = model_section.get("model")
            if legacy_model and not model_section.get("default"):
                model_section["default"] = legacy_model
                existing["model"] = model_section

        # Real-time token streaming, ON by default for every profile.
        #
        # Without this, the gateway's ``StreamingConfig.enabled`` defaults to
        # False, so messaging channels (Feishu/Telegram/Discord/Slack/DingTalk)
        # buffer the WHOLE turn and only deliver once generation finishes — a
        # long reply leaves the user staring at "思考中" for 60-90s, then dumps
        # a wall of text. With streaming on, ``GatewayStreamConsumer`` edits the
        # sent message in place as tokens arrive, so the first words appear in
        # 1-3s and the text grows live. Editable-message platforms stream;
        # non-editable ones (WeChat/WeCom/Signal — ``SUPPORTS_MESSAGE_EDITING``
        # = False) auto-skip, so this is safe to enable globally. The chatbox
        # (api_server) already streams via its own SSE path, independent of
        # this flag. ``load_gateway_config`` reads this top-level ``streaming``
        # dict (see hermes/gateway/config.py). Seed-only: we never clobber an
        # ``enabled`` the user explicitly set, so the UI can still turn it off.
        streaming_section = existing.get("streaming")
        if not isinstance(streaming_section, dict):
            existing["streaming"] = {"enabled": True}
        elif "enabled" not in streaming_section:
            streaming_section["enabled"] = True
            existing["streaming"] = streaming_section

        # model.context_length — explicit, never probed (2026-07-07 latency
        # plan Phase 3). hermes probes the provider's /models endpoint for
        # the context window on EVERY agent spin-up; providers that don't
        # expose it (LongCat: "probe-down") fall back to a 256k default.
        # Two costs: the probe itself delays the turn, and the compression
        # trigger = 0.5 × context_length, so a phantom 256k means history
        # isn't compacted until 128k tokens — measured sessions ballooned to
        # 122-136k input per call. Seed a conservative 128k (halves the
        # trigger to 64k) whenever the value is unset; never clobber an
        # explicit user setting.
        model_section = existing.get("model")
        if isinstance(model_section, dict) and not model_section.get("context_length"):
            model_section["context_length"] = 131072
            existing["model"] = model_section

        # ②a drain:SIGTERM 时优雅 drain 在途 agent 回合。hermes stop() 读
        # ``agent.restart_drain_timeout``——默认 0 表示不 drain(立刻打断在途
        # 回合)。云端 scale-to-zero 会主动 SIGTERM 空闲网关、部署时也会 SIGTERM
        # 全部网关,若不种这个值,在途会话会被硬打断。种一个非零默认让 stop()
        # 走 _drain_active_agents(timeout),在途回合有机会跑完再退出。
        # Seed-only:不覆盖用户/UI 显式设过的值。
        agent_section = existing.get("agent")
        if not isinstance(agent_section, dict):
            agent_section = {}
        if "restart_drain_timeout" not in agent_section:
            agent_section["restart_drain_timeout"] = _DEFAULT_RESTART_DRAIN_TIMEOUT
            existing["agent"] = agent_section

        # ②a 前向兼容(非当前生效路径):种 hermes 自己的 scale_to_zero 行为配置
        # 兜底默认。当前云端网关直连、无 relay/wakeUrl,hermes 的 watcher 不会
        # arm,真正的休眠由本模块的 reaper 驱动(见文件头注释)。但若将来某个
        # profile 走 relay/Fly 拓扑,这个默认让它的 idle_timeout 有定义值,而
        # 不必再改一遍代码。Seed-only。
        stz_section = existing.get("scale_to_zero")
        if not isinstance(stz_section, dict):
            stz_section = {}
        if "idle_timeout_minutes" not in stz_section:
            stz_section["idle_timeout_minutes"] = int(
                cloud_idle_timeout_seconds() // 60
            )
            existing["scale_to_zero"] = stz_section

        # 打开 Curator——hermes 的技能园丁:自动把 agent 在使用中自创的技能做
        # 生命周期整理(归并/自我改进/闲置归档,归档可恢复)。0.18「闭环学习」
        # 的收尾环节,默认关,这里开。
        # ⚠️ prune_builtins 必须 False:Curator 默认(True)会把「非 hermes
        # 自带、又无 agent-created 记录」的技能也纳入闲置归档——而我们经 patch10
        # + sync 铺进 profile 的 crm-* 核心技能恰好落这一类(created_by=None),
        # 会被误当 agent-sediment 归档/改写。设 False 后 Curator 只管 created_by
        # =agent 的真·自创技能(实测:crm-* 全部被排除),我们的策展技能零风险。
        # Seed-only:不覆盖用户/UI 显式设过的值。
        curator_section = existing.get("curator")
        if not isinstance(curator_section, dict):
            curator_section = {}
        if "enabled" not in curator_section:
            curator_section["enabled"] = True
        if "prune_builtins" not in curator_section:
            curator_section["prune_builtins"] = False
        existing["curator"] = curator_section

        # Per-user-channel-bot model: each profile's channel credentials
        # (Feishu app_id/secret, Telegram token, ...) live in this
        # config.yaml under ``platforms.<channel>``, written by the
        # cloud's /api/profiles/{id}/channels/* endpoints. Hermes' native
        # adapter reads them and connects directly — no cloud relay.
        # This sync helper does NOT touch the platforms section; it only
        # syncs model/provider config. The channel endpoints own
        # ``platforms.*`` exclusively.

        config_file.write_text(yaml.dump(existing, default_flow_style=False))

        # Skill mirroring is intentionally NOT done here.
        #
        # hermes ships its own bundled-skill sync (``hermes/tools/
        # skills_sync.py``) that runs on every ``hermes`` CLI launch —
        # including ``hermes gateway run`` which our subprocess spawns.
        # It uses a per-profile manifest (``~/.hermes/profiles/<id>/
        # skills/.bundled_manifest``) recording each bundled skill's
        # origin hash, then on each sync:
        #   - new bundled skill → copy + record hash
        #   - origin_hash == user_hash → safe to update from bundled
        #   - origin_hash != user_hash → user customized, never overwrite
        #   - user deleted file → don't resurrect
        #   - bundled deleted → drop from manifest (user copy preserved)
        # An earlier version of this method (``_mirror_bundled_crm_skills``)
        # tried to do its own copy-if-missing pass for crm-* skills, but
        # that broke sync_skills' invariants: writes without manifest
        # entry, which sync_skills then treats as "user-added skill"
        # and refuses to ever update from bundled. Removed 2026-05-19.

    async def _launch_process(
        self, profile_id: str, port: int, *, user_id: str | None = None
    ) -> tuple[asyncio.subprocess.Process, object]:
        profile_dir = self._hermes_home / "profiles" / profile_id
        logs_dir = profile_dir / "logs"
        logs_dir.mkdir(parents=True, exist_ok=True)
        self._clear_stale_gateway_state(profile_dir)

        env = os.environ.copy()
        # Point hermes home at the per-profile directory
        env["HERMES_HOME"] = str(profile_dir)
        # Env-var overrides take precedence over config.yaml in case of stale values
        env["API_SERVER_ENABLED"] = "true"
        env["API_SERVER_HOST"] = "127.0.0.1"
        env["API_SERVER_PORT"] = str(port)
        env["API_SERVER_KEY"] = self._internal_key
        # Each gateway is scoped to a single profile — open access is safe.
        env["GATEWAY_ALLOW_ALL_USERS"] = "true"
        # Prevent inheriting any parent profile context
        env.pop("HERMES_PROFILE", None)

        # ②a scale-to-zero:把云端总开关也传给 hermes 的 HERMES_SCALE_TO_ZERO
        # stamp。当前云端网关直连、无 relay/wakeUrl,hermes 内建 watcher 不会
        # arm(见文件头注释),真正的休眠由 ProfileManager reaper 驱动;这里传
        # 下去只是前向兼容——若某 profile 将来走 relay/Fly 拓扑,内建 watcher
        # 即可无需改码地启用。不开云端开关时不设,行为与今天一致。
        if cloud_scale_to_zero_enabled():
            env["HERMES_SCALE_TO_ZERO"] = "1"

        # Strip cloud-global FEISHU_* / channel env vars from the
        # subprocess. Per-user-channel-bot model: each profile's channel
        # credentials come ONLY from its own config.yaml
        # ``platforms.<channel>`` (written by the cloud's channel
        # endpoints). Letting a subprocess inherit the cloud-global
        # FEISHU_APP_ID would make every profile open a competing WS
        # against the same shared app — the exact collision the
        # per-profile model exists to eliminate.
        for key in list(env):
            if key.startswith("FEISHU_"):
                env.pop(key, None)

        # Project structured platforms.<chan>.home_channel into the
        # *_HOME_CHANNEL env vars hermes' cron scheduler reads. The
        # per-profile-bot model stores the cron-delivery target in
        # config.yaml (set_home_channel at QR-bind time); hermes still
        # resolves it from env, so bridge here — AFTER the FEISHU_* strip
        # above, so FEISHU_HOME_CHANNEL survives. Same pattern as the
        # provider-key bridge below.
        for _env_var, _val in home_channel_env_for_profile(profile_dir).items():
            env[_env_var] = _val

        # DingTalk's requirements-check (gateway.run → check_dingtalk_
        # requirements) gates on the DINGTALK_CLIENT_ID/SECRET *env vars* —
        # unlike feishu/wecom whose checks don't require creds and whose
        # adapters read straight from config.yaml. The per-profile-bot model
        # stores dingtalk creds under platforms.dingtalk.extra, so bridge
        # them into env here, else the adapter is never built ("No adapter
        # available for dingtalk"). Strip any inherited cloud-global pair
        # first (mirrors the FEISHU_* strip) so each profile uses its own
        # bot creds, not a shared global.
        from hermes_cloud.app.channel_config import read_channel_config as _read_ch
        env.pop("DINGTALK_CLIENT_ID", None)
        env.pop("DINGTALK_CLIENT_SECRET", None)
        _dt_extra = _read_ch(profile_dir, "dingtalk")
        if _dt_extra.get("client_id") and _dt_extra.get("client_secret"):
            env["DINGTALK_CLIENT_ID"] = str(_dt_extra["client_id"])
            env["DINGTALK_CLIENT_SECRET"] = str(_dt_extra["client_secret"])

        # Opt-in cleanup: scrub inert legacy channel residue from the
        # profile config and GC stale weixin creds files. Cosmetic — the
        # bridge above already restores delivery regardless.
        from hermes_cloud.app.channel_config import read_profile_config, write_profile_config
        _cfg = read_profile_config(profile_dir)
        _changed, _cfg = _scrub_legacy_channel_residue(_cfg)
        if _changed:
            write_profile_config(profile_dir, _cfg)
        _accounts_dir = profile_dir / "weixin" / "accounts"
        if _accounts_dir.is_dir():
            for _f in _accounts_dir.glob("*.json"):
                name = _f.name
                if name.endswith(".context-tokens.json") or name.endswith(".sync.json"):
                    continue  # 运行时状态，保留
                try:
                    _f.unlink()
                except OSError as exc:
                    logger.warning("weixin creds GC failed for %s: %s", _f, exc)

        # CRM tool binding — pass user identity to the hermes subprocess
        # so the CRM plugin can register tools with the correct RBAC context.
        if user_id:
            env["CRM_USER_ID"] = str(user_id)

        # Inject provider API key env vars from config.yaml.
        # hermes resolves API keys from env vars (e.g. OPENCODE_ZEN_API_KEY),
        # not from config.yaml's model.api_key. The cloud writes the key into
        # config.yaml via _sync_config_to_profile, so we bridge the gap here.
        # (Map is a class attribute, ``_PROVIDER_KEY_ENV``, reused by the mux
        # per-profile .env materializer — R1-M2.)
        config_file = profile_dir / "config.yaml"
        if config_file.exists():
            try:
                cfg = yaml.safe_load(config_file.read_text()) or {}
                provider = (cfg.get("model") or {}).get("provider", "")
                api_key = (cfg.get("model") or {}).get("api_key", "")
                env_var = self._PROVIDER_KEY_ENV.get(provider)
                if env_var and api_key:
                    env[env_var] = api_key
                    logger.info("injected %s for provider %s", env_var, provider)
            except Exception as exc:
                logger.warning("config parse error for %s: %s", profile_id, exc)

        # Charter §一原语 ① — agent message tap. The hermes-patches/09
        # patch's hermes_state.append_message() POSTs every agent message
        # to this URL so it lands in the canonical interactions table. The
        # subprocess uses HERMES_CLOUD_INTERNAL_KEY for auth.
        # HERMES_CLOUD_PROFILE_ID disambiguates the source profile in the
        # cloud's interactions row.
        agent_event_url = self._agent_event_url
        if agent_event_url:
            env["HERMES_CLOUD_AGENT_EVENT_URL"] = agent_event_url
            env["HERMES_CLOUD_INTERNAL_KEY"] = self._internal_key
            env["HERMES_CLOUD_PROFILE_ID"] = str(profile_id)
        else:
            env.pop("HERMES_CLOUD_AGENT_EVENT_URL", None)
            env.pop("HERMES_CLOUD_PROFILE_ID", None)

        # Gateway → cloud touchpoint tap (email / wechat). Same internal
        # key as the agent-event tap; the base URL is just scheme+host+port,
        # the platform code appends ``/api/touchpoints/internal/ingest/<src>``.
        if self._interaction_log_url:
            env["HERMES_CLOUD_INTERACTION_LOG_URL"] = self._interaction_log_url
            env["HERMES_CLOUD_INTERNAL_KEY"] = self._internal_key
        else:
            env.pop("HERMES_CLOUD_INTERACTION_LOG_URL", None)

        # Charter §九 — PgSessionDB dispatch (hermes-patches/10).
        # Hermes' SessionDB is sqlite-only upstream; with this env var
        # the in-process import gets PgSessionDB instead so the agent
        # loop's session storage lives in the same PG cluster as
        # interactions / CRM (single-DB compliance). The PG implementation
        # is at hermes_cloud.app.agent_session_pg and uses cloud's
        # HERMES_CRM_DATABASE_URL pool, so we forward that too.
        env["HERMES_USE_CLOUD_DB"] = "1"
        # Make hermes_cloud importable from the subprocess.
        cloud_pkg_root = str(
            Path(__file__).resolve().parents[2]  # hermes-cloud repo root
        )
        existing_pp = env.get("PYTHONPATH", "")
        if cloud_pkg_root not in existing_pp.split(":"):
            env["PYTHONPATH"] = (
                f"{cloud_pkg_root}:{existing_pp}" if existing_pp
                else cloud_pkg_root
            )
        # Forward the CRM DSN so the subprocess's PgSessionDB pool
        # connects to the same PG. Either name works for the cloud's
        # crm.db._resolve_dsn.
        for var in ("HERMES_CRM_DATABASE_URL", "CRM_DATABASE_URL"):
            v = os.environ.get(var)
            if v:
                env[var] = v
                break

        # Right-size the subprocess's CRM connection pool. crm.db.get_pool
        # is a per-process singleton, so total PG connections scale with
        # profile count. A per-profile hermes agent processes one turn at
        # a time (a few concurrent platform handlers at most), so a small
        # ceiling is plenty — and min_size=0 means an idle profile holds
        # zero connections. The cloud web server keeps its larger default
        # pool from .env; this override applies only to subprocesses.
        env["HERMES_CRM_POOL_MAX"] = os.environ.get(
            "HERMES_CRM_PROFILE_POOL_MAX", "3"
        )
        env["HERMES_CRM_POOL_MIN"] = os.environ.get(
            "HERMES_CRM_PROFILE_POOL_MIN", "0"
        )

        # Charter §一原语 ⑤ — cloud-approval plugin (bundled via
        # hermes-patches/11) needs to call back into cloud's
        # ``/api/gateway/actions/{id}/approve`` from inside the
        # subprocess. Forward the cloud's loopback URL.
        cloud_url = (
            os.environ.get("HERMES_CLOUD_INTERNAL_URL")
            or os.environ.get("CLOUD_INTERNAL_URL")
            or "http://127.0.0.1:8765"
        )
        env["HERMES_CLOUD_INTERNAL_URL"] = cloud_url

        # Channel delivery is fully per-profile: the profile's hermes
        # subprocess reads its own config.yaml (``platforms.<channel>``
        # creds + ``home_channel`` cron-delivery target) and the native
        # adapter handles inbound + outbound itself. The cloud injects
        # no ``*_HOME_CHANNEL`` / ``WEIXIN_TOKEN`` env and reads no
        # channel config here.

        log_file = open(logs_dir / "gateway.log", "a")
        # `--replace` tells hermes to kill any stale gateway PID from a
        # previous (e.g. crashed) cloud process and take over the port.
        # Without it the subprocess exits instantly with "Gateway already
        # running" whenever a stale PID file remains.
        #
        # Charter v2 §一系统 — start_new_session=True detaches the child
        # from cloud's process group, so when uvicorn exits the IM
        # long-connection subprocess SURVIVES (no SIGHUP/SIGTERM
        # cascades from session leader). On next cloud boot, the
        # adoption logic in startup() picks it back up via the
        # ``cloud_subprocess.json`` registry.
        process = await asyncio.create_subprocess_exec(
            self._hermes_bin,
            "gateway",
            "run",
            "--replace",
            env=env,
            stdout=log_file,
            stderr=log_file,
            start_new_session=True,
        )
        return process, log_file

    # ------------------------------------------------------------------
    # multiplex (B2) — single shared mux gateway, gated on
    # HERMES_CLOUD_MULTIPLEX. Everything below is DEAD CODE when the flag
    # is off; the per-profile path above is untouched.
    # ------------------------------------------------------------------

    # Sentinel key under which the single shared mux gateway's _ProcessEntry
    # is tracked in self._processes. It is deliberately NOT a valid profile
    # id (double-underscore) so it never collides with a real profile dir and
    # is ignored by the profile-dir scanners (adopt / eager-spawn / keepalive).
    _MUX_KEY = "__mux__"

    # Provider → env-var name that hermes reads the API key from (it does NOT
    # read config.yaml's ``model.api_key``). Used by ``_launch_process`` to
    # bridge into per-profile subprocess env, and by the mux path to
    # materialize each profile's key into its own ``.env`` (R1-M2).
    _PROVIDER_KEY_ENV = {
        "opencode-zen": "OPENCODE_ZEN_API_KEY",
        "opencode-go": "OPENCODE_GO_API_KEY",
    }

    def _multiplex_enabled(self, environ: Optional[dict] = None) -> bool:
        """云端 multiplex 拓扑总开关(HERMES_CLOUD_MULTIPLEX)。

        **2026-07-24 起 multiplex 为默认拓扑**:缺省/空/truthy → 开启(单进程
        多租户共享网关)。只有显式设成 falsey(``0/false/no/off``)才回退到
        「每 profile 一进程」的非 multiplex 老路径——保留为紧急 opt-out /
        回滚保命阀,非 multiplex 代码路径本身不变。
        """
        env = environ if environ is not None else os.environ
        raw = str(env.get(_CLOUD_MULTIPLEX_ENV, "")).strip().lower()
        if not raw:
            return True  # 默认拓扑 = multiplex
        return raw not in _FALSEY

    def mux_path_prefix(self, profile_id: str) -> str:
        """反代 URL 前缀:multiplex 关→``""``(逐字节不变);开→``/p/{profile_id}``。

        供 main.py 的 URL 构造点用:``f"http://127.0.0.1:{port}{pm.mux_path_prefix
        (profile_id)}{upstream_path}"``。关时 prefix 为空 → URL 与今天一致;开时 mux
        网关的 api_server 已镜像 ``/p/{profile}{path}`` 全路由。
        """
        if not self._multiplex_enabled():
            return ""
        return f"/p/{profile_id}"

    async def _ensure_mux_running(self, user_id: str) -> int:
        """Ensure the single shared mux gateway is up; return its fixed port.

        Called (only when the flag is on) from the top of
        ``_ensure_running_locked`` for ANY profile_id — they all share this one
        gateway, so any profile_id maps to the same port.

        Concurrency: ``_ensure_running_locked`` runs under the *per-profile*
        lock, so two different profiles racing to first-start the mux gateway
        would each hold their own lock. We therefore serialize the mux start on
        a dedicated lock keyed by ``_MUX_KEY`` (a distinct key → no deadlock
        with the outer per-profile lock).
        """
        async with self._get_profile_lock(self._MUX_KEY):
            entry = self._processes.get(self._MUX_KEY)
            if entry is not None:
                if entry.is_running:
                    entry.touch()
                    return entry.port
                # Mux gateway died — drop the stale entry, free its port
                # (so it gets picked again below → same fixed port), respawn.
                logger.warning(
                    "mux gateway died pid=%s returncode=%s — restarting",
                    entry.effective_pid,
                    (entry.process.returncode
                     if entry.process is not None else "<adopted-died>"),
                )
                self._free_ports.add(entry.port)
                if entry.log_file:
                    try:
                        entry.log_file.close()
                    except Exception:
                        pass
                del self._processes[self._MUX_KEY]

            # Spawn once. Mirrors _start_for_profile's spawn/health/entry
            # skeleton, but with the mux launch/config (root HERMES_HOME,
            # -p default, shared listener) — kept separate so the per-profile
            # path stays untouched.
            if not self._free_ports:
                raise RuntimeError(
                    "No available ports for the mux gateway. "
                    "Increase port_range_end."
                )
            # Deterministic fixed port = the range's first (lowest) free port.
            # On a restart the previous mux port was returned to the pool
            # above, so min() picks it again → the mux gateway keeps its port.
            port = min(self._free_ports)
            self._free_ports.discard(port)
            try:
                self._configure_mux_root(port)
                # R1-M2: the mux gateway is ONE process serving every profile,
                # so provider keys can't ride process env (a single value would
                # leak across tenants, and the cloud only ever writes
                # config.yaml). hermes' multiplex secret_scope resolves secrets
                # per-turn from each profile's OWN .env — materialize them there
                # before the gateway boots.
                self._materialize_all_provider_keys()
                process, log_file = await self._launch_mux_process(port, user_id)
                healthy = await self._wait_healthy(
                    port, timeout=_SPAWN_HEALTH_TIMEOUT
                )
                if not healthy:
                    try:
                        process.terminate()
                        try:
                            await asyncio.wait_for(process.wait(), timeout=5.0)
                        except asyncio.TimeoutError:
                            try:
                                process.kill()
                            except ProcessLookupError:
                                pass
                            await process.wait()
                    except ProcessLookupError:
                        pass
                    if log_file:
                        log_file.close()
                    self._free_ports.add(port)
                    raise RuntimeError(
                        f"Mux gateway failed to pass health check on port "
                        f"{port} within {_SPAWN_HEALTH_TIMEOUT:.0f} s."
                    )
                entry = _ProcessEntry(
                    profile_id=self._MUX_KEY,
                    user_id=user_id,
                    port=port,
                    process=process,
                    log_file=log_file,
                )
                self._processes[self._MUX_KEY] = entry
                # R1-M1: persist the mux entry so a cloud bounce's startup
                # adoption can RECLAIM this live gateway instead of spawning a
                # second one with ``--replace`` (which SIGKILLs the survivor and
                # drops every tenant's WS). Mirrors _start_for_profile's persist.
                self._persist_registry(entry)
                logger.info(
                    "mux_gateway_started port=%d pid=%d", port, process.pid,
                )
                return port
            except Exception:
                self._free_ports.add(port)
                raise

    def _configure_mux_root(self, port: int) -> None:
        """Write the shared api_server + multiplex settings into ROOT config.yaml.

        In multiplex mode a single mux gateway (HERMES_HOME = root, pinned to
        the ``default`` profile via ``-p default``) serves every named profile,
        reading each profile's own ``profiles/<id>/config.yaml`` for its creds.
        The ROOT config must therefore:
          - set ``multiplex_profiles: true`` so the gateway routes
            ``/p/{profile}/*`` into each per-profile runtime scope;
          - own the SHARED api_server listener (top-level ``API_SERVER_*``).

        The pure ``default`` profile has no messaging platform, and a bare
        top-level ``API_SERVER_ENABLED`` did not reliably materialize a
        listener (L2a finding), so we ALSO declare ``platforms.api_server``
        explicitly with the port — that guarantees the listen socket binds.

        Unlike ``_configure_profile`` this binds NO per-profile port: the mux
        gateway owns the one shared socket here; named-profile configs keep
        their own settings but no longer drive a private api_server port.
        Seed/merge style: preserves any existing root config keys.
        """
        root = self._hermes_home
        root.mkdir(parents=True, exist_ok=True)
        config_file = root / "config.yaml"
        existing: dict = {}
        if config_file.exists():
            try:
                existing = yaml.safe_load(config_file.read_text()) or {}
            except Exception:
                existing = {}

        existing.update(
            {
                "API_SERVER_ENABLED": True,
                "API_SERVER_HOST": "127.0.0.1",
                "API_SERVER_PORT": port,
                "API_SERVER_KEY": self._internal_key,
                "multiplex_profiles": True,
            }
        )

        platforms = existing.get("platforms")
        if not isinstance(platforms, dict):
            platforms = {}
        api_server = platforms.get("api_server")
        if not isinstance(api_server, dict):
            api_server = {}
        api_server["enabled"] = True
        extra = api_server.get("extra")
        if not isinstance(extra, dict):
            extra = {}
        extra["port"] = port
        api_server["extra"] = extra
        platforms["api_server"] = api_server
        existing["platforms"] = platforms

        config_file.write_text(yaml.dump(existing, default_flow_style=False))

    @staticmethod
    def _upsert_env_var(env_path: Path, key: str, value: str) -> None:
        """Set ``KEY=value`` in a ``.env`` file, replacing an existing ``KEY=``
        line and preserving every other line. Creates the file if absent.

        Deliberately touches only the target ``.env`` file — NEVER
        ``os.environ`` — so provider keys can't cross-contaminate other
        profiles served by the same mux process.
        """
        line = f"{key}={value}"
        existing_lines: list[str] = []
        if env_path.exists():
            try:
                existing_lines = env_path.read_text().splitlines()
            except Exception:
                existing_lines = []
        out: list[str] = []
        replaced = False
        for ln in existing_lines:
            if ln.startswith(f"{key}="):
                if not replaced:
                    out.append(line)
                    replaced = True
                # drop any duplicate later occurrences
            else:
                out.append(ln)
        if not replaced:
            out.append(line)
        env_path.parent.mkdir(parents=True, exist_ok=True)
        env_path.write_text("\n".join(out) + "\n")

    def _materialize_provider_keys_to_env(self, profile_dir: Path) -> None:
        """R1-M2: bridge a profile's provider API key from its config.yaml into
        its OWN ``profiles/<id>/.env`` so the mux gateway's per-turn
        secret_scope (which reads each profile's ``.env``) can resolve it.

        In the per-profile topology ``_launch_process`` bridges the key into
        the subprocess's process env. The mux gateway is a SINGLE process
        serving every profile, so a process-level env var would both leak the
        key across tenants and not exist at all (the cloud writes only
        config.yaml, never a per-profile ``.env``). Writing per-profile ``.env``
        is the mux-safe equivalent. Idempotent; never mutates ``os.environ``.
        No-op for profiles without a mapped provider key.
        """
        # hermes v0.20.1: named-profile requests on the mux gateway are
        # authenticated against a PROFILE-SCOPED API_SERVER_KEY
        # (api_server._expected_api_key → get_secret under the profile
        # scope → this .env) and FAIL CLOSED when none resolves. Without
        # this line every cloud → /p/{tenant}/… call (chat proxy, channel
        # push, reload-adapters) 401s: "no profile-scoped API_SERVER_KEY
        # is configured". Seed it before the config.yaml early-returns so
        # even a profile with a broken/missing config stays reachable.
        if self._internal_key:
            self._upsert_env_var(
                profile_dir / ".env", "API_SERVER_KEY", str(self._internal_key)
            )

        # Authorization gates must live in the profile SCOPE, not only the
        # mux process env. gateway/authz_mixin._platform_gate_env reads these
        # from the per-profile secret scope under multiplex and — by design,
        # to stop profile A's allowlist leaking into profile B (#72348) —
        # returns the DEFAULT on a scope miss instead of falling through to
        # os.environ. The mux process sets GATEWAY_ALLOW_ALL_USERS=true, but
        # once this file exists a scope IS installed, so a missing key here
        # reads as "not allow-all" and every IM user is refused with a
        # pairing prompt ("Hi~ I don't recognize you yet"). Observed live
        # 2026-09-04: seeding API_SERVER_KEY (2026-08-16) created the .env
        # for profiles that never had one, which silently flipped this gate
        # for every tenant. Seed the gate alongside the key so scope and
        # process env agree.
        self._upsert_env_var(profile_dir / ".env", "GATEWAY_ALLOW_ALL_USERS", "true")

        config_file = profile_dir / "config.yaml"
        if not config_file.exists():
            return
        try:
            cfg = yaml.safe_load(config_file.read_text()) or {}
        except Exception:
            return

        # Topology migration: a profile provisioned under the per-profile
        # topology carries top-level ``API_SERVER_ENABLED/HOST/PORT`` from
        # _configure_profile. Under mux those make gateway/run.py classify
        # the profile as port-binding and SKIP it entirely ("Skipping
        # secondary profile … due to port-binding config error") — its IM
        # adapters never mount and every /p/{profile}/ push 503s. Neutralize
        # here (runs per profile at mux cold-start + restart_profile);
        # flipping back to per-profile topology is safe because
        # _configure_profile re-writes these keys on next spawn.
        _dirty = False
        if cfg.get("API_SERVER_ENABLED"):
            cfg["API_SERVER_ENABLED"] = False
            cfg.pop("API_SERVER_PORT", None)
            _dirty = True
        # The explicit pin is REQUIRED, not just cleanup: gateway/config.py
        # force-enables api_server whenever a usable API_SERVER_KEY resolves
        # in the profile scope (and the key stays — mux auth needs it),
        # UNLESS the profile pins ``platforms.api_server.enabled: false``
        # (the ``_enabled_explicit`` escape upstream documents for exactly
        # this multiplex-secondary case).
        _platforms = cfg.get("platforms")
        if not isinstance(_platforms, dict):
            _platforms = {}
            cfg["platforms"] = _platforms
        _api = _platforms.get("api_server")
        if not isinstance(_api, dict):
            _api = {}
            _platforms["api_server"] = _api
        if _api.get("enabled") is not False:
            _api["enabled"] = False
            _dirty = True
        if _dirty:
            try:
                config_file.write_text(
                    yaml.safe_dump(cfg, allow_unicode=True, sort_keys=False)
                )
                logger.info(
                    "mux: neutralized per-profile api_server config in %s",
                    profile_dir.name,
                )
            except Exception as exc:
                logger.warning(
                    "mux: failed to neutralize api_server config for %s: %s",
                    profile_dir.name, exc,
                )

        model = cfg.get("model") or {}
        if not isinstance(model, dict):
            return
        provider = model.get("provider", "")
        api_key = model.get("api_key", "")
        env_var = self._PROVIDER_KEY_ENV.get(provider)
        if not env_var or not api_key:
            return
        self._upsert_env_var(profile_dir / ".env", env_var, str(api_key))

    def _materialize_all_provider_keys(self) -> None:
        """Materialize provider keys into every real profile's ``.env`` (R1-M2).

        Runs on mux cold-start so all existing profiles have their key resolved
        before the shared gateway boots. Skips the ``__mux__`` sentinel dir and
        never raises — a single bad profile must not block the mux start.
        """
        profiles_root = self._hermes_home / "profiles"
        try:
            children = list(profiles_root.iterdir())
        except FileNotFoundError:
            return
        for child in children:
            if not child.is_dir() or child.name == self._MUX_KEY:
                continue
            try:
                self._materialize_provider_keys_to_env(child)
            except Exception:
                logger.warning(
                    "mux: materialize provider key failed profile=%s",
                    child.name, exc_info=True,
                )

    async def _launch_mux_process(
        self, port: int, user_id: str | None = None
    ) -> tuple[asyncio.subprocess.Process, object]:
        """Spawn the single mux gateway. Mirrors ``_launch_process``'s
        subprocess/log-file mechanics, with the multiplex-specific deltas:

          - ``HERMES_HOME`` = ROOT (not a per-profile dir);
          - command adds ``-p default`` to pin the root/default profile
            (otherwise it falls onto the sticky active_profile — single-tenant
            trap, see project_multiplex_activation);
          - NO process-level ``CRM_USER_ID`` (identity is per-turn since B1 —
            a process-level value would poison every other profile's turns);
          - NO per-profile credential bridging (dingtalk creds / provider key /
            home_channel env): multiplex resolves those per-turn from each
            profile's own config.yaml, not from process env.

        Kept (same rationale as per-profile): FEISHU_* strip (a stale global
        must not clobber per-profile app creds), ``GATEWAY_ALLOW_ALL_USERS``,
        ``HERMES_PROFILE`` pop, scale-to-zero stamp, the agent-message tap
        (event url + internal key — but NOT a fixed PROFILE_ID, which is
        per-turn here), the touchpoint tap, single-DB (PgSessionDB) wiring,
        cloud pkg on PYTHONPATH, CRM DSN + pool sizing, and cloud loopback URL.
        """
        logs_dir = self._hermes_home / "logs"
        logs_dir.mkdir(parents=True, exist_ok=True)
        self._clear_stale_gateway_state(self._hermes_home)

        env = os.environ.copy()
        # HERMES_HOME = ROOT (the key delta vs per-profile). The mux gateway
        # reads each named profile's config out of profiles/<id>/ itself.
        env["HERMES_HOME"] = str(self._hermes_home)
        # Shared listener — same env override precedence as per-profile.
        env["API_SERVER_ENABLED"] = "true"
        env["API_SERVER_HOST"] = "127.0.0.1"
        env["API_SERVER_PORT"] = str(port)
        env["API_SERVER_KEY"] = self._internal_key
        # Multiplex: upstream private chats default-allow (see
        # project_multiplex_channel_auth); machine-owner authz is B/C.
        env["GATEWAY_ALLOW_ALL_USERS"] = "true"
        # Prevent inheriting any parent profile context.
        env.pop("HERMES_PROFILE", None)

        # scale-to-zero stamp — forward-compat only (see _launch_process).
        if cloud_scale_to_zero_enabled():
            env["HERMES_SCALE_TO_ZERO"] = "1"

        # Strip cloud-global FEISHU_* so a stale global app id can't clobber
        # each profile's own feishu app creds (project_feishu_multiplex_cred_
        # clobber). Still required under multiplex.
        for key in list(env):
            if key.startswith("FEISHU_"):
                env.pop(key, None)

        # DELIBERATELY NOT SET under multiplex: CRM_USER_ID (per-turn since
        # B1), DINGTALK_* / provider-key / *_HOME_CHANNEL bridges (resolved
        # per-turn from each profile's config.yaml by the mux gateway).

        # Agent-message tap (Charter §一原语 ①). The mux gateway also taps —
        # event url + internal key. NOTE: no process-level HERMES_CLOUD_
        # PROFILE_ID (the source profile is per-turn here, so a fixed value
        # would mislabel every other profile's rows).
        if self._agent_event_url:
            env["HERMES_CLOUD_AGENT_EVENT_URL"] = self._agent_event_url
            env["HERMES_CLOUD_INTERNAL_KEY"] = self._internal_key
            env.pop("HERMES_CLOUD_PROFILE_ID", None)
        else:
            env.pop("HERMES_CLOUD_AGENT_EVENT_URL", None)
            env.pop("HERMES_CLOUD_PROFILE_ID", None)

        # Gateway → cloud touchpoint tap (email / wechat).
        if self._interaction_log_url:
            env["HERMES_CLOUD_INTERACTION_LOG_URL"] = self._interaction_log_url
            env["HERMES_CLOUD_INTERNAL_KEY"] = self._internal_key
        else:
            env.pop("HERMES_CLOUD_INTERACTION_LOG_URL", None)

        # Single-DB (PgSessionDB) dispatch — same as per-profile.
        env["HERMES_USE_CLOUD_DB"] = "1"
        cloud_pkg_root = str(Path(__file__).resolve().parents[2])
        existing_pp = env.get("PYTHONPATH", "")
        if cloud_pkg_root not in existing_pp.split(":"):
            env["PYTHONPATH"] = (
                f"{cloud_pkg_root}:{existing_pp}" if existing_pp
                else cloud_pkg_root
            )
        for var in ("HERMES_CRM_DATABASE_URL", "CRM_DATABASE_URL"):
            v = os.environ.get(var)
            if v:
                env[var] = v
                break
        env["HERMES_CRM_POOL_MAX"] = os.environ.get(
            "HERMES_CRM_PROFILE_POOL_MAX", "3"
        )
        env["HERMES_CRM_POOL_MIN"] = os.environ.get(
            "HERMES_CRM_PROFILE_POOL_MIN", "0"
        )

        # Cloud-approval plugin loopback (Charter §一原语 ⑤).
        cloud_url = (
            os.environ.get("HERMES_CLOUD_INTERNAL_URL")
            or os.environ.get("CLOUD_INTERNAL_URL")
            or "http://127.0.0.1:8765"
        )
        env["HERMES_CLOUD_INTERNAL_URL"] = cloud_url

        log_file = open(logs_dir / "gateway.log", "a")
        # ``-p default`` pins the root/default profile (global option, before
        # the subcommand). ``--replace`` reclaims a stale gateway PID/port.
        # start_new_session=True detaches from cloud's process group (survives
        # uvicorn restart), mirroring the per-profile spawn.
        process = await asyncio.create_subprocess_exec(
            self._hermes_bin,
            "-p",
            "default",
            "gateway",
            "run",
            "--replace",
            env=env,
            stdout=log_file,
            stderr=log_file,
            start_new_session=True,
        )
        return process, log_file

    def _clear_stale_gateway_state(self, profile_dir: Path) -> None:
        state_path = profile_dir / "gateway_state.json"
        if not state_path.exists():
            return
        try:
            payload = json.loads(state_path.read_text() or "{}")
        except Exception:
            payload = {}
        pid = payload.get("pid")
        if not isinstance(pid, int):
            try:
                state_path.unlink()
            except FileNotFoundError:
                pass
            return
        # Is the PID still alive?
        try:
            os.kill(pid, 0)
            return  # still running; leave state alone
        except ProcessLookupError:
            pass
        except PermissionError:
            return  # exists but we can't signal; leave alone
        try:
            state_path.unlink()
        except FileNotFoundError:
            pass

    async def _wait_healthy(self, port: int, timeout: float) -> bool:
        url = f"http://127.0.0.1:{port}/v1/models"
        deadline = time.monotonic() + timeout
        async with httpx.AsyncClient(timeout=2.0) as client:
            while time.monotonic() < deadline:
                try:
                    r = await client.get(
                        url, headers={"Authorization": f"Bearer {self._internal_key}"}
                    )
                    if r.status_code < 500:
                        return True
                except httpx.HTTPError:
                    pass
                await asyncio.sleep(_HEALTH_POLL_INTERVAL)
        return False

    def _graceful_stop_wait_seconds(self) -> float:
        """SIGTERM 后等子进程优雅退出(drain 在途回合)的预算,再回落 SIGKILL。

        对齐种进 config.yaml 的 ``agent.restart_drain_timeout``(_DEFAULT_RESTART_
        DRAIN_TIMEOUT)+ 余量,覆盖 drain 之外的 adapter 断开 / SessionDB 关闭 /
        文件清理。若等得比 hermes 的 drain 短,SIGKILL 会在 drain 中途落下、丢掉
        在途会话——正是本特性要避免的。
        """
        return _DEFAULT_RESTART_DRAIN_TIMEOUT + 15.0

    async def _terminate_entry(self, entry: _ProcessEntry) -> None:
        # Charter v2 §一系统 — only terminate processes we OWN (spawned
        # this cloud lifetime). Adopted subprocesses (process=None,
        # pid set) survive the cloud restart by design; tearing them
        # down on cloud shutdown would defeat the adoption.
        if entry.process is not None:
            if entry.is_running:
                entry.process.terminate()
                # ②a drain:给网关走完 SIGTERM → _drain_active_agents 的时间,
                # 再回落 SIGKILL(旧的 5s 会在 30s drain 中途硬杀,丢在途会话)。
                try:
                    await asyncio.wait_for(
                        entry.process.wait(),
                        timeout=self._graceful_stop_wait_seconds(),
                    )
                except asyncio.TimeoutError:
                    logger.warning(
                        "terminate: profile %s did not exit within %.0fs of "
                        "SIGTERM — sending SIGKILL",
                        entry.profile_id, self._graceful_stop_wait_seconds(),
                    )
                    entry.process.kill()
                    await entry.process.wait()
            if entry.log_file:
                try:
                    entry.log_file.close()
                except Exception:
                    pass
            self._delete_registry(entry.profile_id)
        # Adopted entries: leave the OS process alone, leave the registry
        # file alone — next cloud boot will adopt it again.
