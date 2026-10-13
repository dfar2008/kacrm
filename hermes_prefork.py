#!/usr/bin/env python3
"""
hermes_prefork.py — Production-grade prefork worker pool manager for hermes-agent.

Master process:
  1. Preloads all heavy Python modules so forked children share CoW memory.
  2. Forks N worker processes, each running the hermes API server on a
     dedicated port (port_base + worker_index).
  3. Watches children with os.wait(); dead workers are restarted with
     exponential backoff (max 30 s, up to 10 restarts before giving up).
  4. On SIGTERM/SIGINT: broadcasts SIGTERM to workers, waits up to 30 s,
     then SIGKILLs any survivors.
  5. Exposes a lightweight /health HTTP endpoint on --health-port (default 9000).

Usage:
    python hermes_prefork.py \
        --workers 4 \
        --port-base 8100 \
        --health-port 9000 \
        --config /path/to/config.yaml \
        --internal-key sk-internal-xxx

Environment equivalents:
    HERMES_WORKERS        (default: cpu count)
    HERMES_PORT_BASE      (default: 8100)
    HERMES_HEALTH_PORT    (default: 9000)
    HERMES_CONFIG         (default: ~/.hermes/config.yaml)
    HERMES_INTERNAL_KEY   (required)
"""

from __future__ import annotations

import argparse
import asyncio
import http.server
import json
import logging
import os
import signal
import socket
import sys
import threading
import time
import traceback
from pathlib import Path
from typing import Dict, Optional

# ---------------------------------------------------------------------------
# Logging setup
# ---------------------------------------------------------------------------

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [hermes-prefork] [%(levelname)s] %(message)s",
    datefmt="%Y-%m-%dT%H:%M:%S",
)
logger = logging.getLogger("hermes-prefork")

# ---------------------------------------------------------------------------
# Constants
# ---------------------------------------------------------------------------

MAX_RESTARTS = 10
SHUTDOWN_GRACE_SECONDS = 30
BACKOFF_BASE = 1.0     # seconds
BACKOFF_MAX = 30.0     # seconds


# ---------------------------------------------------------------------------
# Optional: process title update
# ---------------------------------------------------------------------------

def _set_proc_title(title: str) -> None:
    """Update the process title visible in ps/top output."""
    try:
        import setproctitle  # type: ignore
        setproctitle.setproctitle(title)
        return
    except ImportError:
        pass
    # Fallback: modify argv[0] in-place (visible in ps on Linux)
    try:
        # Encode title into the original argv[0] buffer where possible
        sys.argv[0] = title
    except Exception:
        pass


# ---------------------------------------------------------------------------
# Preload strategy — maximise Linux CoW sharing across forked workers
# ---------------------------------------------------------------------------

def _preload_modules() -> None:
    """Import heavy modules before forking so all workers share their memory."""
    modules = [
        ("run_agent", "AIAgent and model logic"),
        ("model_tools", "tool discovery"),
        ("gateway.platforms.api_server", "aiohttp app factory"),
        ("gateway.run", "GatewayRunner"),
    ]
    for mod_name, description in modules:
        try:
            __import__(mod_name)
            logger.info("[master] preloaded %s (%s)", mod_name, description)
        except Exception as exc:
            logger.warning(
                "[master] preload of %s (%s) failed — continuing without it: %s",
                mod_name, description, exc,
            )


# ---------------------------------------------------------------------------
# Port conflict check
# ---------------------------------------------------------------------------

def _port_in_use(port: int, host: str = "127.0.0.1") -> bool:
    with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as s:
        s.settimeout(1)
        try:
            s.connect((host, port))
            return True
        except (ConnectionRefusedError, OSError):
            return False


def _validate_ports(port_base: int, num_workers: int, health_port: int) -> None:
    """Fail fast if any required port is already in use."""
    ports_to_check = [port_base + i for i in range(num_workers)] + [health_port]
    for port in ports_to_check:
        if _port_in_use(port):
            logger.error(
                "[master] Port %d is already in use. Aborting startup.", port
            )
            sys.exit(1)


# ---------------------------------------------------------------------------
# Health HTTP handler (runs in a daemon thread on the master)
# ---------------------------------------------------------------------------

class _HealthHandler(http.server.BaseHTTPRequestHandler):
    """Minimal HTTP handler for GET /health."""

    # The master injects the worker registry here before starting the thread.
    workers: Dict[int, Dict] = {}
    start_time: float = time.time()

    def do_GET(self):  # noqa: N802 — aiohttp naming convention
        if self.path != "/health":
            self.send_response(404)
            self.end_headers()
            return

        worker_list = []
        for pid, info in _HealthHandler.workers.items():
            worker_list.append({
                "pid": pid,
                "port": info["port"],
                "index": info["index"],
                "status": info.get("status", "running"),
                "restarts": info.get("restarts", 0),
                "uptime_seconds": round(time.time() - info.get("start_time", time.time()), 1),
            })

        body = json.dumps({
            "status": "ok",
            "master_pid": os.getpid(),
            "uptime_seconds": round(time.time() - _HealthHandler.start_time, 1),
            "workers": worker_list,
        }).encode()

        self.send_response(200)
        self.send_header("Content-Type", "application/json")
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)

    def log_message(self, fmt, *args):  # silence access logs
        pass


def _start_health_server(port: int, workers: Dict[int, Dict]) -> None:
    """Launch the health HTTP server in a background daemon thread."""
    _HealthHandler.workers = workers
    _HealthHandler.start_time = time.time()

    server = http.server.HTTPServer(("0.0.0.0", port), _HealthHandler)
    t = threading.Thread(target=server.serve_forever, daemon=True, name="health-server")
    t.start()
    logger.info("[master] health endpoint listening on http://0.0.0.0:%d/health", port)


# ---------------------------------------------------------------------------
# Worker entrypoint (runs after fork)
# ---------------------------------------------------------------------------

def _worker_main(
    worker_index: int,
    port: int,
    config_path: Optional[str],
    internal_key: str,
) -> None:
    """
    Worker process body.  Runs the hermes API server on the given port.
    This function never returns normally — it calls sys.exit() when done.
    """
    _set_proc_title(f"hermes-worker[{worker_index}] port={port}")

    # Reset inherited signal handlers so Ctrl-C reaches the master cleanly.
    signal.signal(signal.SIGTERM, signal.SIG_DFL)
    signal.signal(signal.SIGINT, signal.SIG_DFL)

    # Point the API server at the assigned port and configure the internal key.
    os.environ["API_SERVER_PORT"] = str(port)
    os.environ["API_SERVER_HOST"] = "0.0.0.0"
    if internal_key:
        os.environ["API_SERVER_KEY"] = internal_key

    # Override HERMES_HOME config if a custom config path was provided.
    if config_path:
        config_file = Path(config_path)
        if config_file.exists():
            os.environ["HERMES_HOME"] = str(config_file.parent)
        else:
            logger.warning(
                "[worker %d] config path %s not found — using default HERMES_HOME",
                worker_index, config_path,
            )

    logger.info("[worker %d] starting API server on port %d (pid=%d)", worker_index, port, os.getpid())

    try:
        from gateway.config import PlatformConfig
        from gateway.platforms.api_server import APIServerAdapter

        config = PlatformConfig(enabled=True)
        adapter = APIServerAdapter(config)

        async def _run() -> None:
            ok = await adapter.connect()
            if not ok:
                logger.error("[worker %d] adapter.connect() returned False", worker_index)
                sys.exit(1)
            # Keep running until interrupted
            try:
                while True:
                    await asyncio.sleep(3600)
            except (KeyboardInterrupt, SystemExit):
                pass
            finally:
                await adapter.disconnect()

        asyncio.run(_run())

    except (KeyboardInterrupt, SystemExit):
        pass
    except Exception as exc:
        logger.error("[worker %d] unhandled exception: %s", worker_index, exc)
        traceback.print_exc()
        sys.exit(1)

    sys.exit(0)


# ---------------------------------------------------------------------------
# Master process — worker lifecycle management
# ---------------------------------------------------------------------------

class PreforkMaster:
    def __init__(
        self,
        num_workers: int,
        port_base: int,
        health_port: int,
        config_path: Optional[str],
        internal_key: str,
    ) -> None:
        self.num_workers = num_workers
        self.port_base = port_base
        self.health_port = health_port
        self.config_path = config_path
        self.internal_key = internal_key

        # pid -> {"port", "index", "start_time", "restarts", "status"}
        self.workers: Dict[int, Dict] = {}
        # index -> backoff state {"next_backoff", "restarts"}
        self._backoff: Dict[int, Dict] = {
            i: {"next_backoff": BACKOFF_BASE, "restarts": 0}
            for i in range(num_workers)
        }
        self._shutdown = False
        self._shutdown_event = threading.Event()

    # ------------------------------------------------------------------
    # Signal handling
    # ------------------------------------------------------------------

    def _install_signal_handlers(self) -> None:
        signal.signal(signal.SIGTERM, self._handle_signal)
        signal.signal(signal.SIGINT, self._handle_signal)
        # SIGCHLD: wake up the wait loop when a child exits
        signal.signal(signal.SIGCHLD, self._handle_sigchld)

    def _handle_signal(self, signum, frame) -> None:
        logger.info("[master] received signal %d — initiating graceful shutdown", signum)
        self._shutdown = True
        self._shutdown_event.set()

    def _handle_sigchld(self, signum, frame) -> None:
        # Non-blocking reap; the main wait loop also calls waitpid.
        pass

    # ------------------------------------------------------------------
    # Fork a single worker
    # ------------------------------------------------------------------

    def _fork_worker(self, index: int) -> int:
        """Fork a new worker for the given slot index. Returns the new PID."""
        port = self.port_base + index
        pid = os.fork()
        if pid == 0:
            # ---- child ----
            _worker_main(index, port, self.config_path, self.internal_key)
            # _worker_main calls sys.exit(); this line is never reached.
            os._exit(1)

        # ---- parent ----
        ts = time.time()
        self.workers[pid] = {
            "port": port,
            "index": index,
            "start_time": ts,
            "restarts": self._backoff[index]["restarts"],
            "status": "running",
        }
        # Keep the shared dict reference that the health handler reads.
        _HealthHandler.workers = self.workers
        logger.info("[master] forked worker pid=%d index=%d port=%d", pid, index, port)
        return pid

    # ------------------------------------------------------------------
    # Main run loop
    # ------------------------------------------------------------------

    def run(self) -> None:
        _set_proc_title(f"hermes-master workers={self.num_workers} base={self.port_base}")
        self._install_signal_handlers()
        _start_health_server(self.health_port, self.workers)

        # Spawn all workers initially
        for i in range(self.num_workers):
            self._fork_worker(i)

        logger.info(
            "[master] all %d workers started (ports %d–%d)",
            self.num_workers, self.port_base, self.port_base + self.num_workers - 1,
        )

        # Main monitoring loop
        while not self._shutdown:
            self._reap_children()
            # Sleep briefly, but wake immediately on SIGCHLD or shutdown signal
            self._shutdown_event.wait(timeout=1.0)
            self._shutdown_event.clear()

        self._graceful_shutdown()

    def _reap_children(self) -> None:
        """Check for dead workers and restart them if appropriate."""
        while True:
            try:
                pid, status = os.waitpid(-1, os.WNOHANG)
            except ChildProcessError:
                break
            if pid == 0:
                break

            info = self.workers.pop(pid, None)
            if info is None:
                continue

            exit_code = os.WEXITSTATUS(status) if os.WIFEXITED(status) else -1
            index = info["index"]
            port = info["port"]

            if self._shutdown:
                logger.info(
                    "[master] worker pid=%d port=%d exited during shutdown (code=%d)",
                    pid, port, exit_code,
                )
                continue

            bk = self._backoff[index]
            bk["restarts"] += 1

            if bk["restarts"] > MAX_RESTARTS:
                logger.error(
                    "[master] worker index=%d port=%d has restarted %d times — "
                    "giving up to avoid a restart storm",
                    index, port, bk["restarts"],
                )
                continue

            backoff = min(bk["next_backoff"], BACKOFF_MAX)
            bk["next_backoff"] = min(bk["next_backoff"] * 2, BACKOFF_MAX)

            logger.warning(
                "[master] worker pid=%d port=%d died (code=%d), restarting in %.1fs "
                "(attempt %d)",
                pid, port, exit_code, backoff, bk["restarts"],
            )
            time.sleep(backoff)
            self._fork_worker(index)

    # ------------------------------------------------------------------
    # Shutdown
    # ------------------------------------------------------------------

    def _graceful_shutdown(self) -> None:
        """Send SIGTERM to all workers, wait up to 30 s, then SIGKILL stragglers."""
        logger.info("[master] sending SIGTERM to %d workers", len(self.workers))
        for pid in list(self.workers):
            try:
                os.kill(pid, signal.SIGTERM)
            except ProcessLookupError:
                pass

        deadline = time.time() + SHUTDOWN_GRACE_SECONDS
        while self.workers and time.time() < deadline:
            self._reap_children()
            time.sleep(0.1)

        # Kill any workers that didn't exit gracefully
        for pid in list(self.workers):
            logger.warning("[master] worker pid=%d did not exit in time — sending SIGKILL", pid)
            try:
                os.kill(pid, signal.SIGKILL)
            except ProcessLookupError:
                pass

        # Final reap
        for _ in range(50):
            if not self.workers:
                break
            self._reap_children()
            time.sleep(0.1)

        logger.info("[master] shutdown complete")


# ---------------------------------------------------------------------------
# CLI
# ---------------------------------------------------------------------------

def _cpu_count() -> int:
    try:
        return os.cpu_count() or 2
    except Exception:
        return 2


def _parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description="Prefork worker pool manager for hermes-agent API server",
        formatter_class=argparse.RawDescriptionHelpFormatter,
    )
    parser.add_argument(
        "--workers",
        type=int,
        default=int(os.environ.get("HERMES_WORKERS", str(_cpu_count()))),
        help="Number of worker processes (default: cpu_count, env: HERMES_WORKERS)",
    )
    parser.add_argument(
        "--port-base",
        type=int,
        default=int(os.environ.get("HERMES_PORT_BASE", "8100")),
        help="First worker port; workers get port_base+i (default: 8100, env: HERMES_PORT_BASE)",
    )
    parser.add_argument(
        "--health-port",
        type=int,
        default=int(os.environ.get("HERMES_HEALTH_PORT", "9000")),
        help="Master health endpoint port (default: 9000, env: HERMES_HEALTH_PORT)",
    )
    parser.add_argument(
        "--config",
        default=os.environ.get("HERMES_CONFIG", ""),
        help="Path to config.yaml (env: HERMES_CONFIG)",
    )
    parser.add_argument(
        "--internal-key",
        default=os.environ.get("HERMES_INTERNAL_KEY", ""),
        help="Bearer token workers require for requests (env: HERMES_INTERNAL_KEY, REQUIRED)",
    )
    return parser.parse_args()


def main() -> None:
    if sys.platform == "win32":
        logger.error("hermes_prefork requires a Unix-like OS (os.fork not available on Windows)")
        sys.exit(1)

    args = _parse_args()

    if not args.internal_key:
        logger.error(
            "HERMES_INTERNAL_KEY (or --internal-key) is required. "
            "Set it to the Bearer token workers will accept."
        )
        sys.exit(1)

    if args.workers < 1:
        logger.error("--workers must be at least 1")
        sys.exit(1)

    config_path: Optional[str] = args.config or None
    if config_path and not Path(config_path).exists():
        logger.error("Config file not found: %s", config_path)
        sys.exit(1)

    logger.info(
        "[master] hermes-prefork starting — workers=%d port_base=%d health_port=%d",
        args.workers, args.port_base, args.health_port,
    )

    # Startup validation
    _validate_ports(args.port_base, args.workers, args.health_port)

    # Preload heavy modules into master memory before forking
    _preload_modules()

    master = PreforkMaster(
        num_workers=args.workers,
        port_base=args.port_base,
        health_port=args.health_port,
        config_path=config_path,
        internal_key=args.internal_key,
    )
    master.run()


if __name__ == "__main__":
    main()
