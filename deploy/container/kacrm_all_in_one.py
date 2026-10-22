"""Single-container KaCRM: local PostgreSQL, Redis, Web, worker and beat."""
from __future__ import annotations
import fcntl
import json
import os
from pathlib import Path
import secrets
import signal
import subprocess
import sys
import time
import urllib.request

STATE = Path('/var/lib/kacrm')
PG_BIN = Path('/usr/lib/postgresql/15/bin')
INSTALLATION = 'kacrm-installation.json'


def installation(root: Path = STATE) -> dict[str, str]:
    path = root / INSTALLATION
    if path.exists():
        if path.is_symlink():
            raise RuntimeError('Installation settings must not be a symbolic link')
        data = json.loads(path.read_text())
        required = {'admin_email', 'admin_password', 'db_password', 'redis_password', 'internal_key', 'session_secret'}
        if set(data) != required or not all(isinstance(v, str) and v for v in data.values()):
            raise RuntimeError('Installation settings are incomplete; restore them from backup')
        return data
    if (root / 'postgres' / 'PG_VERSION').exists():
        raise RuntimeError('Database exists but installation settings are missing; restore settings from backup')
    data = {name: secrets.token_hex(32) for name in (
        'admin_password', 'db_password', 'redis_password', 'internal_key', 'session_secret')}
    data['admin_email'] = os.getenv('KACRM_ADMIN_EMAIL', 'admin@kacrm.local')
    # Use the one-time override only during first installation; never reset an existing admin.
    if os.getenv('KACRM_ADMIN_PASSWORD'):
        data['admin_password'] = os.environ['KACRM_ADMIN_PASSWORD']
    fd = os.open(path, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
    with os.fdopen(fd, 'w') as stream:
        json.dump(data, stream)
        stream.flush()
        os.fsync(stream.fileno())
    return data


def local_environment(settings: dict[str, str]) -> dict[str, str]:
    db = f"postgresql+psycopg://kacrm:{settings['db_password']}@127.0.0.1:5432/kacrm"
    broker = f"redis://:{settings['redis_password']}@127.0.0.1:6379/3"
    return {
        'HERMES_CRM_DATABASE_URL': db, 'HERMES_CLOUD_DB_URL': db, 'ALEMBIC_DATABASE_URL': db,
        'HERMES_CLOUD_ADMIN_EMAIL': settings['admin_email'],
        'HERMES_CLOUD_ADMIN_PASSWORD': settings['admin_password'],
        'HERMES_CRM_ADMIN_EMAIL': settings['admin_email'],
        'HERMES_CRM_ADMIN_PASSWORD': settings['admin_password'],
        'HERMES_CLOUD_INTERNAL_KEY': settings['internal_key'],
        'HERMES_SESSION_SECRET': settings['session_secret'],
        'CELERY_BROKER_URL': broker, 'CELERY_RESULT_BACKEND': broker,
        'CLOUD_INTERNAL_URL': 'http://127.0.0.1:8765', 'PORT': '8765',
    }


class Supervisor:
    def __init__(self):
        self.children: list[tuple[str, subprocess.Popen]] = []
        self.stopping = False

    def stop(self, *_):
        self.stopping = True

    def start(self, name: str, cmd: list[str]) -> subprocess.Popen:
        process = subprocess.Popen(cmd, start_new_session=True)
        self.children.append((name, process))
        return process

    def check(self):
        if self.stopping:
            raise InterruptedError('Shutdown requested')
        for name, child in self.children:
            if child.poll() is not None:
                raise RuntimeError(f'{name} exited; stopping the container so Docker can restart it')

    def wait_ready(self, probe, timeout=60):
        deadline = time.monotonic() + timeout
        while time.monotonic() < deadline:
            self.check()
            try:
                if probe():
                    return
            except Exception as exc:
                # Probe failures are expected while services start; never log credentials/DSNs.
                if isinstance(exc, InterruptedError):
                    raise
            time.sleep(.3)
        raise RuntimeError('Internal service readiness timed out')

    def shutdown(self):
        # Stop producers and consumers before their databases. All children use their own groups.
        for name, child in reversed(self.children):
            if child.poll() is not None:
                continue
            try:
                os.killpg(child.pid, signal.SIGTERM)
            except ProcessLookupError:
                continue
            deadline = time.monotonic() + (30 if name in {'worker', 'postgres'} else 15)
            while child.poll() is None and time.monotonic() < deadline:
                time.sleep(.2)
            if child.poll() is None:
                try:
                    os.killpg(child.pid, signal.SIGKILL)
                except ProcessLookupError:
                    pass
            child.wait()


def pg_ready() -> bool:
    return subprocess.run([str(PG_BIN / 'pg_isready'), '-q', '-h', '127.0.0.1', '-p', '5432'],
                          stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL).returncode == 0


def serve():
    STATE.mkdir(parents=True, exist_ok=True)
    # A data volume must never be used by two live instances.
    lock = (STATE / 'kacrm.lock').open('a')
    try:
        fcntl.flock(lock, fcntl.LOCK_EX | fcntl.LOCK_NB)
    except BlockingIOError:
        raise RuntimeError('This KaCRM data volume is already in use') from None
    settings = installation()
    os.environ.update(local_environment(settings))
    pgdata = STATE / 'postgres'
    redis_dir = STATE / 'redis'
    run = STATE / 'run'
    redis_dir.mkdir(exist_ok=True)
    run.mkdir(exist_ok=True)
    if (pgdata / 'PG_VERSION').exists():
        if (pgdata / 'PG_VERSION').read_text().strip() != '15':
            raise RuntimeError('This image needs PostgreSQL 15 data; migrate other major versions separately')
    else:
        pwfile = run / 'postgres-password'
        fd = os.open(pwfile, os.O_WRONLY | os.O_CREAT | os.O_TRUNC, 0o600)
        try:
            with os.fdopen(fd, 'w') as stream:
                stream.write(settings['db_password'] + '\n')
            subprocess.run([str(PG_BIN / 'initdb'), '-D', str(pgdata), '--username=kacrm',
                            '--auth-host=scram-sha-256', '--auth-local=trust',
                            '--pwfile=' + str(pwfile), '--encoding=UTF8', '--locale=C.UTF-8'], check=True)
        finally:
            pwfile.unlink(missing_ok=True)
    config = run / 'redis.conf'
    fd = os.open(config, os.O_WRONLY | os.O_CREAT | os.O_TRUNC, 0o600)
    with os.fdopen(fd, 'w') as stream:
        stream.write(f'bind 127.0.0.1\nport 6379\nprotected-mode yes\nrequirepass {settings["redis_password"]}\n'
                     f'dir {redis_dir}\nappendonly yes\nappendfsync everysec\nsave ""\n')
    supervisor = Supervisor()
    signal.signal(signal.SIGTERM, supervisor.stop)
    signal.signal(signal.SIGINT, supervisor.stop)
    try:
        supervisor.start('postgres', [str(PG_BIN / 'postgres'), '-D', str(pgdata),
                                      '-h', '127.0.0.1', '-k', str(run)])
        supervisor.wait_ready(pg_ready)
        import psycopg
        with psycopg.connect(host='127.0.0.1', user='kacrm', password=settings['db_password'],
                             dbname='postgres', autocommit=True) as connection:
            if not connection.execute("SELECT 1 FROM pg_database WHERE datname='kacrm'").fetchone():
                connection.execute('CREATE DATABASE kacrm')
        supervisor.start('redis', ['redis-server', str(config)])
        import redis
        client = redis.Redis(host='127.0.0.1', password=settings['redis_password'], socket_timeout=2)
        supervisor.wait_ready(client.ping)
        init = subprocess.Popen([sys.executable, '-m', 'deploy.container.kacrm_runtime', 'init'],
                                start_new_session=True)
        try:
            while init.poll() is None:
                supervisor.check()
                time.sleep(.3)
            if init.returncode != 0:
                raise RuntimeError('Database migration/initialization failed; see preceding logs')
        finally:
            if init.poll() is None:
                os.killpg(init.pid, signal.SIGTERM)
                try:
                    init.wait(timeout=15)
                except subprocess.TimeoutExpired:
                    os.killpg(init.pid, signal.SIGKILL)
                    init.wait()
        supervisor.start('web', [sys.executable, '-m', 'deploy.container.kacrm_runtime', 'web'])
        supervisor.wait_ready(lambda: urllib.request.urlopen('http://127.0.0.1:8765/health', timeout=3).status == 200,
                              timeout=120)
        supervisor.start('worker', [sys.executable, '-m', 'deploy.container.kacrm_runtime', 'worker'])
        supervisor.start('beat', [sys.executable, '-m', 'deploy.container.kacrm_runtime', 'beat'])
        print('KaCRM ready at /app/. Retrieve the initial admin credentials with: '
              'docker exec <container> python -m deploy.container.kacrm_all_in_one credentials', flush=True)
        while not supervisor.stopping:
            supervisor.check()
            time.sleep(.5)
    finally:
        supervisor.shutdown()
        lock.close()


def health():
    settings = json.loads((STATE / INSTALLATION).read_text())
    import redis
    if not pg_ready() or not redis.Redis(host='127.0.0.1', password=settings['redis_password'], socket_timeout=2).ping():
        raise RuntimeError('Internal database or Redis is unavailable')
    with urllib.request.urlopen('http://127.0.0.1:8765/health', timeout=3) as response:
        if response.status != 200:
            raise RuntimeError('Web is unavailable')


def main(args: list[str]):
    role = args[0] if args else 'serve'
    if role == 'serve':
        serve()
    elif role == 'credentials':
        settings = json.loads((STATE / INSTALLATION).read_text())
        print('Administrator:', settings['admin_email'])
        print('Initial password:', settings['admin_password'])
    elif role == 'health':
        health()
    else:
        from deploy.container.kacrm_runtime import main as runtime_main
        runtime_main(args)


if __name__ == '__main__':
    try:
        main(sys.argv[1:])
    except InterruptedError:
        pass
    except Exception as exc:
        # Avoid printing exception values: DB exceptions may contain connection credentials.
        print(f'KaCRM startup failed ({type(exc).__name__}); inspect service logs and persistent volume.', file=sys.stderr)
        sys.exit(1)
