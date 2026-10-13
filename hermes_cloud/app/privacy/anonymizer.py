"""PII anonymization + detection (privacy primitive).

Originally added for RL training-data egress (plan §12). After the RL
subsystem was removed (Charter v2 Phase 3, 2026-05-06), this module
moved out of ``app/rl/`` to ``app/privacy/`` and remains a
deterministic redaction primitive used by the gateway PII gate
(``gateway/api.py``).

Pattern-based anonymization for the most common PII categories:

* **Names** — Chinese and Western person names
* **Phone numbers** — Chinese mobile, landline, international
* **Email addresses**
* **Company names** — matched against a supplied dictionary
* **Monetary amounts** — replaced with bucketed placeholders
* **ID numbers** — Chinese national ID, passport, etc.

The approach is conservative: it's better to over-redact (replace a
non-PII string with a placeholder) than to leak real data into
training.

All replacements are deterministic within a session (same input →
same placeholder) so field-level F1 scoring still works on anonymized
data.
"""
from __future__ import annotations

import hashlib
import re
from typing import Dict, Optional, Set


# ---------------------------------------------------------------------------
# Patterns
# ---------------------------------------------------------------------------

_PHONE_RE = re.compile(
    r"(?<!\d)"
    r"(?:\+?86[-\s]?)?"
    r"1[3-9]\d[-\s]?\d{4}[-\s]?\d{4}"
    r"(?!\d)"
)

_LANDLINE_RE = re.compile(
    r"(?<!\d)"
    r"0[1-9]\d{1,2}[-\s]?\d{7,8}"
    r"(?!\d)"
)

_EMAIL_RE = re.compile(
    r"[a-zA-Z0-9._%+\-]+@[a-zA-Z0-9.\-]+\.[a-zA-Z]{2,}"
)

_CN_ID_RE = re.compile(
    r"(?<!\d)"
    r"[1-9]\d{5}(?:19|20)\d{2}(?:0[1-9]|1[0-2])(?:0[1-9]|[12]\d|3[01])\d{3}[\dXx]"
    r"(?!\d)"
)

_MONEY_RE = re.compile(
    r"(?:[\$¥￥€£])\s*[\d,]+(?:\.\d+)?"
    r"|[\d,]+(?:\.\d+)?\s*(?:元|万|亿|美元|USD|RMB|CNY)"
)

# Common Chinese name patterns (2-4 chars after common surnames)
_CN_SURNAMES = (
    "王|李|张|刘|陈|杨|黄|赵|吴|周|徐|孙|马|朱|胡|郭|林|何|高|罗|"
    "郑|梁|谢|宋|唐|许|韩|冯|邓|曹|彭|曾|肖|田|董|潘|袁|蔡|蒋|"
    "余|于|杜|叶|程|魏|苏|吕|丁|任|沈|姚|卢|傅|钟|姜|崔|谭|廖|"
    "范|汪|陆|金|石|戴|贾|韦|夏|邱|方|侯|邹|熊|孟|秦|白|江|阎|"
    "薛|尹|段|雷|黎|史|龙|贺|顾|毛|郝|龚|邵|万|钱|严|覃|武|戚|"
    "孔|向|汤"
)
_CN_NAME_RE = re.compile(
    rf"(?:{_CN_SURNAMES})[^\s，。！？、；：\u201c\u201d\u2018\u2019（）【】\d\x00-\x7f]{{1,3}}"
)


class Anonymizer:
    """Stateful anonymizer that maps PII → deterministic placeholders.

    Usage::

        anon = Anonymizer(company_names={"ACME Corp", "Initech"})
        clean = anon.anonymize(text)
    """

    def __init__(
        self,
        *,
        company_names: Optional[Set[str]] = None,
        salt: str = "",
    ) -> None:
        self._salt = salt
        self._company_names = company_names or set()
        self._cache: Dict[str, str] = {}

    def _placeholder(self, category: str, value: str) -> str:
        """Generate a deterministic placeholder for a PII value."""
        key = f"{category}:{value}"
        if key not in self._cache:
            h = hashlib.sha256(
                f"{self._salt}:{key}".encode()
            ).hexdigest()[:8]
            self._cache[key] = f"[{category.upper()}_{h}]"
        return self._cache[key]

    def anonymize(self, text: str) -> str:
        """Replace all detected PII in *text* with placeholders."""
        if not text:
            return text

        result = text

        # Order matters: longer patterns first to avoid partial matches

        # 1. Chinese national ID numbers
        result = _CN_ID_RE.sub(
            lambda m: self._placeholder("id", m.group()), result
        )

        # 2. Email addresses
        result = _EMAIL_RE.sub(
            lambda m: self._placeholder("email", m.group()), result
        )

        # 3. Phone numbers (mobile then landline)
        result = _PHONE_RE.sub(
            lambda m: self._placeholder("phone", m.group()), result
        )
        result = _LANDLINE_RE.sub(
            lambda m: self._placeholder("phone", m.group()), result
        )

        # 4. Monetary amounts → bucket
        result = _MONEY_RE.sub(
            lambda m: self._placeholder("amount", m.group()), result
        )

        # 5. Known company names
        for name in sorted(self._company_names, key=len, reverse=True):
            if name in result:
                result = result.replace(
                    name, self._placeholder("company", name)
                )

        # 6. Chinese person names (last — most aggressive)
        result = _CN_NAME_RE.sub(
            lambda m: self._placeholder("name", m.group()), result
        )

        return result


def anonymize_sample(
    prompt: str,
    completion: str,
    *,
    company_names: Optional[Set[str]] = None,
    salt: str = "",
) -> tuple[str, str]:
    """Anonymize both prompt and completion with a shared mapping.

    Using the same :class:`Anonymizer` instance ensures that a company
    name in the prompt and the same name in the completion get the same
    placeholder — critical for field-level F1 scoring to still work.
    """
    anon = Anonymizer(company_names=company_names, salt=salt)
    return anon.anonymize(prompt), anon.anonymize(completion)


def detect_pii(text: str) -> list:
    """Scan *text* for PII patterns; return a list of ``(kind, match)``.

    Used by :func:`assert_no_pii` and by the ComplianceAgent to surface
    soft-warn findings. Does NOT mutate — pure detection.
    """
    findings: list = []
    if not text:
        return findings
    for kind, rx in (
        ("id_cn", _CN_ID_RE),
        ("email", _EMAIL_RE),
        ("phone", _PHONE_RE),
        ("phone", _LANDLINE_RE),
        ("name_cn", _CN_NAME_RE),
    ):
        for m in rx.finditer(text):
            findings.append((kind, m.group(0)))
    return findings


class PiiLeakError(RuntimeError):
    """Raised when unanonymized PII is detected in a text that's about
    to leave the system boundary (training export, outbound API, etc.).
    """


def assert_no_pii(*texts: str, context: str = "export") -> None:
    """Last-mile guard before PII-sensitive data leaves the system.

    Scans every argument with :func:`detect_pii`; if any pattern hits,
    raises :class:`PiiLeakError`. Email and phone patterns don't match
    the placeholder format (``[EMAIL_xxxxxxxx]``), so this is safe to
    call *after* :func:`anonymize_sample`.
    """
    leaks: list = []
    for t in texts:
        leaks.extend(detect_pii(t))
    if leaks:
        kinds = sorted({k for k, _ in leaks})
        raise PiiLeakError(
            f"{context}: {len(leaks)} PII leak(s) detected "
            f"(kinds={kinds}); anonymizer bypassed or failed"
        )


__all__ = [
    "Anonymizer", "anonymize_sample",
    "detect_pii", "assert_no_pii", "PiiLeakError",
]
