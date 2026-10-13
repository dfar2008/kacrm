# KaCRM Script Reference

**English** · [简体中文](README.md)

See the [Deployment Guide](../docs/deployment.en.md) for installation and local startup. Initialize the database with `hermes.crm.seed.seed()`. Scripts do not run automatically just because they are in this directory.

| Script | Purpose |
| --- | --- |
| `run_cloud_supervised.sh` | Manage PgBouncer, the web service, and Celery workers/beat as a persistent service. |
| `deploy-to-server.sh` | Deployment, backup, migration, seed, verification, and rollback; dry-run by default. |
| `build-pyc-artifact.sh` | Build Python 3.11 bytecode deployment artifacts; called by the deployment script. |
| `check-hermes-patches-against.sh` | Check patch compatibility in a temporary upstream checkout. |
| `regen-hermes-patches.sh` | Regenerate patches from the current Hermes changes. |
| `verify-patch-content.sh` | Check required skills and tools in the patches. |
| `upgrade-acceptance.sh` | Verify contracts after a Hermes upgrade. |
| `sync-profile-skills.sh` | Update existing copies of built-in skills in profiles during deployment. |
| `push_skill_to_active_profiles.sh` | Synchronize a selected skill to profiles; dry-run by default, with backup support. |
| `sync-kacrm-guide.sh` | Capture the usage-guide skill in the patch and synchronize it to profiles. |
| `sync-onboarding-persona.py` | Synchronize the onboarding persona and profile SOUL/system instructions. |
| `dump-style-portrait.sh` | Export a profile's writing-style and sales-methodology portrait for comparison. |
| `purge_polluted_memories.py` | Remove memories polluted by older execution traces; dry-run by default. |
| `reconcile_memory_entities.py` | Merge older CRM entity references; dry-run by default. |
| `seed_crm_commerce.py` | Seed development demo products, orders, quotes, and payments. |
| `seed_crm_interactions.py` | Seed development demo CRM interactions. |
| `seed_dashboard_demo.py` | Seed dashboard demo data in a development database. |
| `verify-sales-coach-rollout.sh` | Run Coach-card verification SQL. |
| `verify-coach-cards.sql` | Check Coach cards and sales-methodology skill references. |
| `measure-coach-feedback.sql` | Analyze Coach cards over a time window; the feedback JOIN is a template to complete manually. |
| `verify_sandbox_isolation.sh` | Verify Hermes session sandbox isolation in Docker. |
| `eval/runner.py` | Evaluate models against a user-supplied JSONL dataset. |
| `eval/judge.py` | Score evaluation trajectories; run with `--help` for command-line options. |

Use the repository's virtual environment for Python scripts and set `PYTHONPATH="$PWD:$PWD/hermes"` from the repository root.

Maintenance and demo scripts need an explicitly selected target database. Demo seed scripts are for development databases only. Patch regeneration modifies the Hermes submodule's index. Remote deployment and synchronization require your own SSH configuration.

The evaluation workflow's historical documentation is available in [Chinese](eval/README.md). Use an isolated evaluation environment: dry-run does not isolate every kind of persistence or prevent all memory/session writes.
