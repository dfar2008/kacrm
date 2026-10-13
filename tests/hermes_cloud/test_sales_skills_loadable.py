"""
Smoke test for the 8 crm-sales-* methodology skills.

Three checks per skill (24 test cases total):
- YAML frontmatter parses with required fields
- Required body sections present
- No full Chinese draft sentences outside 反例/❌/禁止 contexts
  (enforces the layered-orthogonal rule: methodology skills only
   produce skeleton hooks; concrete sentences are filled in at
   runtime by the USER.md 话术风格画像 layer)
"""
import re
from pathlib import Path

import pytest
import yaml

SKILLS_DIR = Path(__file__).resolve().parents[2] / "hermes" / "skills"

# NOTE: `crm-daily-coach` is intentionally **excluded** from this list.
# It is the orchestration layer that explicitly teaches the agent to write
# final customer-facing Chinese text (post-2026-06-01 gate 2 rewrite). Its
# SKILL.md contains positive examples like
# "王总,看到您这边方案我们这边发过去 3 天了。想确认下..." which DO match the
# `_FULL_DRAFT_HEURISTIC` regex — that's by design (it's the合格 example for
# the integration layer). The 8 methodology skills below are the reference
# material that should stay free of pitch sentences; daily-coach is where
# pitches get composed at runtime.
SALES_SKILLS = [
    "crm-sales-discovery",
    "crm-sales-meddpicc",
    "crm-sales-challenger",
    "crm-sales-pipeline",
    "crm-sales-cadence",
    "crm-sales-account",
    "crm-sales-proposal",
    "crm-sales-engineering",
]

REQUIRED_SECTIONS = ["Draft 骨架", "reasoning 输出标准", "反例"]


def _read_skill(skill_name: str) -> str:
    return (SKILLS_DIR / skill_name / "SKILL.md").read_text(encoding="utf-8")


@pytest.mark.parametrize("skill_name", SALES_SKILLS)
def test_skill_md_exists_and_yaml_valid(skill_name):
    path = SKILLS_DIR / skill_name / "SKILL.md"
    assert path.exists(), f"missing {path}"
    text = path.read_text(encoding="utf-8")
    parts = text.split("---", 2)
    assert len(parts) >= 3, f"frontmatter delimiter not found in {path}"
    fm = yaml.safe_load(parts[1])
    assert fm is not None, f"frontmatter is empty in {path}"
    assert fm["name"] == skill_name, f"frontmatter name mismatch in {path}"
    assert fm.get("description"), f"description missing in {path}"
    assert fm.get("when_to_use"), f"when_to_use missing in {path}"


@pytest.mark.parametrize("skill_name", SALES_SKILLS)
def test_skill_contains_required_sections(skill_name):
    text = _read_skill(skill_name)
    for section in REQUIRED_SECTIONS:
        assert section in text, (
            f"{skill_name} missing required section: {section}"
        )


# Heuristic 1: a "draft pitch sentence" looks like
#   <honorific><greeting/comma>,<tail ≥ 20 chars containing some CJK>
# e.g. "李总您好，上周您提到 20 套 3D 设计软件" — honorific (chen/li/wang+总)
# + 您好/，/, + tail of 20+ chars (digits / English / ASCII punct OK in tail
# as long as at least some CJK is present somewhere in the tail).
#
# Previous version required the tail to be 20+ consecutive CJK/fullwidth-only,
# which is mathematically unsatisfiable on natural sales drafts (they almost
# always have a digit / latin / ASCII comma somewhere in the tail). That made
# the test a no-op. The new version flags any line that opens with an
# honorific and trails into a substantive sentence.
_FULL_DRAFT_HEURISTIC = re.compile(
    r"[一-龥]{1,3}(总|姐|哥|先生|女士)(您好|总，|总,|，|,).{20,}"
)

# Heuristic 2: "fake pretext" — implying prior contact when there might be
# none. Flagged across any line (regardless of preceding honorific) because
# these tokens shouldn't appear in methodology skill bodies outside
# counter-example context. Pattern matches "上周/上次/之前/最近/前几天/咱们…"
# + (within 8 chars) "您提到/您说/聊过/谈过…".
_FAKE_PRETEXT = re.compile(
    r"(上周|上次|之前|前面|上回|前几天|这几天|最近|近期|先前|咱们)"
    r".{0,8}"
    r"(您提到|您说|您讲|您原来|聊过|谈过|回顾|承接)"
)

# Lines that are explicitly counter-examples (allowed to contain the bad pattern)
_COUNTER_EXAMPLE_MARKERS = ("❌", "反例", "禁止", "非法", "should NOT", "Should NOT", "不合格", "不对")


@pytest.mark.parametrize("skill_name", SALES_SKILLS)
def test_skill_has_no_full_chinese_draft_sentences(skill_name):
    """Methodology skill bodies should not contain customer-facing draft
    sentences (honorific + pitch) or fake-pretext fabrications, EXCEPT
    inside explicit counter-example blocks.

    Skill TEACHING material (example questions like "如果再持续 6-12 个月,
    代价是什么?" or analytic prose) is allowed — those don't trigger the
    regex because they have no honorific opener.
    """
    text = _read_skill(skill_name)
    in_counter_example_block = False
    illegal_lines = []

    for line in text.splitlines():
        stripped = line.strip()

        # Track whether we're inside a counter-example block
        # (a heading like "## 反例" opens it; the next heading closes it)
        if stripped.startswith("#"):
            in_counter_example_block = any(
                m in stripped for m in _COUNTER_EXAMPLE_MARKERS
            )
            continue

        # Per-line markers (table rows or inline annotations)
        if any(m in line for m in _COUNTER_EXAMPLE_MARKERS):
            continue

        if in_counter_example_block:
            continue

        if _FULL_DRAFT_HEURISTIC.search(line) or _FAKE_PRETEXT.search(line):
            illegal_lines.append(line)

    assert not illegal_lines, (
        f"{skill_name} contains pitch / fake-pretext line(s) "
        f"outside 反例/❌/禁止 context:\n  "
        + "\n  ".join(illegal_lines[:5])
    )
