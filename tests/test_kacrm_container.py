"""Container boundary checks: no private context, no embedded provider credentials."""
import contextlib
import importlib.util
import io
import os
from pathlib import Path
import subprocess
import tempfile
import unittest
from unittest.mock import patch

from deploy.container import kacrm_runtime as runtime

ROOT = Path(__file__).resolve().parents[1]
spec = importlib.util.spec_from_file_location("kacrm_image_build", ROOT / "scripts/build-kacrm-image.py")
builder = importlib.util.module_from_spec(spec)
spec.loader.exec_module(builder)


class RuntimeSettingsTests(unittest.TestCase):
    def settings(self):
        return {
            "HERMES_CRM_DATABASE_URL": "postgresql+psycopg://kacrm@postgres/kacrm",
            "HERMES_CLOUD_INTERNAL_KEY": "test-internal",
            "HERMES_SESSION_SECRET": "test-session",
            "HERMES_CLOUD_ADMIN_EMAIL": "admin@example.invalid",
            "HERMES_CLOUD_ADMIN_PASSWORD": "test-only-password",
        }

    def test_no_model_key_required_and_no_admin_defaults(self):
        with patch.dict(os.environ, self.settings(), clear=True):
            runtime.configure()
            self.assertNotIn("OPENAI_API_KEY", os.environ)
            self.assertEqual(os.environ["HERMES_CRM_ADMIN_PASSWORD"], "test-only-password")
        with patch.dict(os.environ, {}, clear=True):
            with self.assertRaisesRegex(RuntimeError, "HERMES_CLOUD_ADMIN_PASSWORD"):
                runtime.configure()

    def test_runtime_secret_file_does_not_log_value(self):
        with tempfile.TemporaryDirectory() as directory:
            secret = Path(directory) / "password"
            secret.write_text("private-test-value\n")
            env = self.settings()
            del env["HERMES_CLOUD_ADMIN_PASSWORD"]
            env["HERMES_CLOUD_ADMIN_PASSWORD_FILE"] = str(secret)
            with patch.dict(os.environ, env, clear=True):
                runtime.configure()
                self.assertEqual(os.environ["HERMES_CRM_ADMIN_PASSWORD"], "private-test-value")
                runtime.configure()  # Web validates once before exec, and again in its app factory.

    def test_conflicting_file_and_value_fail_without_echoing_secret(self):
        env = self.settings()
        env["HERMES_CLOUD_ADMIN_PASSWORD_FILE"] = "/unused"
        with patch.dict(os.environ, env, clear=True):
            with self.assertRaises(RuntimeError) as error:
                runtime.configure()
            self.assertNotIn(env["HERMES_CLOUD_ADMIN_PASSWORD"], str(error.exception))

    def test_mismatched_admin_rejected(self):
        env = {**self.settings(), "HERMES_CRM_ADMIN_EMAIL": "other@example.invalid"}
        with patch.dict(os.environ, env, clear=True):
            with self.assertRaisesRegex(RuntimeError, "emails must match"):
                runtime.configure()


class BuildContextTests(unittest.TestCase):
    def git(self, directory, *args):
        return subprocess.check_output(["git", "-c", "user.name=KaCRM Test",
                                        "-c", "user.email=test@example.invalid", *args],
                                       cwd=directory, stderr=subprocess.DEVNULL)

    def test_only_pinned_hermes_and_allowed_sources_enter_context(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory) / "repo"
            root.mkdir()
            upstream = root / "hermes"
            upstream.mkdir()
            self.git(upstream, "init", "-q")
            (upstream / "public.py").write_text("PUBLIC_SOURCE = True\n")
            (upstream / ".env").write_text("OPENAI_API_KEY=PRIVATE_UPSTREAM_CANARY\n")
            self.git(upstream, "add", ".")
            self.git(upstream, "commit", "-qm", "fixture")
            self.git(root, "init", "-q")
            (root / "hermes_cloud").mkdir()
            (root / "hermes_cloud/public.py").write_text("PUBLIC_APP = True\n")
            (root / "hermes_cloud/.env.local").write_text("PRIVATE_NESTED_CANARY")
            (root / ".env").write_text("PRIVATE_ROOT_CANARY")
            self.git(root, "add", ".")
            self.git(root, "commit", "-qm", "fixture")
            # A developer's dirty Hermes checkout and untracked credentials must not ship.
            (upstream / "public.py").write_text("PRIVATE_DIRTY_CANARY")
            (upstream / "local-token.json").write_text("PRIVATE_UNTRACKED_CANARY")
            (root / "订阅号推广").mkdir()
            (root / "订阅号推广/private.txt").write_text("PRIVATE_PROMOTION_CANARY")
            destination = Path(directory) / "context"
            destination.mkdir()
            with contextlib.redirect_stdout(io.StringIO()):
                builder.prepare_context(destination, root)
            self.assertEqual((destination / "hermes/public.py").read_text(), "PUBLIC_SOURCE = True\n")
            payload = b"".join(p.read_bytes() for p in destination.rglob("*") if p.is_file())
            self.assertNotIn(b"PRIVATE_", payload)
            self.assertTrue((destination / "hermes_cloud/public.py").is_file())
            self.assertFalse((destination / ".git").exists())


class AllInOneTests(unittest.TestCase):
    def test_installation_is_private_unique_and_persistent(self):
        from deploy.container import kacrm_all_in_one as aio
        with tempfile.TemporaryDirectory() as a, tempfile.TemporaryDirectory() as b:
            with patch.dict(os.environ, {}, clear=True):
                first = aio.installation(Path(a))
                second = aio.installation(Path(b))
            self.assertNotEqual(first['admin_password'], second['admin_password'])
            self.assertNotEqual(first['db_password'], second['db_password'])
            self.assertEqual((Path(a) / aio.INSTALLATION).stat().st_mode & 0o777, 0o600)
            with patch.dict(os.environ, {'KACRM_ADMIN_PASSWORD': 'changed'}, clear=True):
                self.assertEqual(aio.installation(Path(a)), first)
            env = aio.local_environment(first)
            self.assertIn('@127.0.0.1:5432/', env['HERMES_CRM_DATABASE_URL'])
            self.assertIn('@127.0.0.1:6379/', env['CELERY_BROKER_URL'])
            self.assertFalse(any('API_KEY' in key for key in env))

    def test_missing_settings_never_regenerates_database_password(self):
        from deploy.container import kacrm_all_in_one as aio
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            (root / 'postgres').mkdir()
            (root / 'postgres/PG_VERSION').write_text('15')
            with self.assertRaisesRegex(RuntimeError, 'restore settings'):
                aio.installation(root)
            self.assertFalse((root / aio.INSTALLATION).exists())

    def test_shutdown_handles_process_exit_race(self):
        from deploy.container import kacrm_all_in_one as aio
        from unittest.mock import Mock
        supervisor = aio.Supervisor()
        child = Mock(pid=999999)
        child.poll.return_value = None
        supervisor.children = [('postgres', child), ('redis', child)]
        with patch.object(aio.os, 'killpg', side_effect=ProcessLookupError) as kill:
            supervisor.shutdown()
        self.assertEqual(kill.call_count, 2)


if __name__ == "__main__":
    unittest.main()
