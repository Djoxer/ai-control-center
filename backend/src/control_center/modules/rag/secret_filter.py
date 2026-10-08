"""Secret filter: keeps files with credentials out of the vector store.

Why it exists: everything in Qdrant can be read back through the MCP search tools by anyone in the
LAN. The old scripts only skipped ".env" and ".htpasswd" - a database password in a PHP config array
or a token in a YAML file went straight into the index.

What it does: file-name rules plus content rules on exactly the text that WOULD be stored (after the
max_chars cut). A hit skips the whole file; the report names file, line and rule - never the value.
It is a filter, not a scanner: simple rules, rather one false alarm too many (the file can be allowed
in [modules.rag] secret_allow) than a key in the index. Reviewing hits in the UI comes later.
"""
from __future__ import annotations

import fnmatch
import re
from dataclasses import dataclass

# File names that hold secrets by convention (case-insensitive glob on the bare file name)
SECRET_NAMES: tuple[str, ...] = (
    ".env", ".env.*", "*.env", ".htpasswd", ".netrc", ".pgpass", ".npmrc", ".pypirc",
    "*.pem", "*.key", "*.p12", "*.pfx", "*.jks", "*.keystore", "id_rsa*", "id_dsa*", "id_ecdsa*", "id_ed25519*",
    "credentials", "credentials.*", "secrets.*", "*.secret", "*.secrets",
)

# Values that look like a secret assignment but are obviously placeholders or references
_PLACEHOLDER = re.compile(
    r"^(?:\*+|x+|\.{3,}|<[^>]*>|\$\{[^}]*\}|\{\{[^}]*\}\}|%[^%]+%|env\(.*\)|"
    r"(?:change[-_ ]?me|example|dummy|sample|placeholder|your[-_ ].*|test|testing|secret|password|"
    r"passwort|null|none|todo|tbd|required|optional)(?:\b.*)?)$",
    re.IGNORECASE,
)

# (rule name shown in the report, pattern). A rule with a group named "value" is checked against
# the placeholder list; the others are specific enough on their own.
_RULES: tuple[tuple[str, re.Pattern[str]], ...] = (
    ("private-key", re.compile(r"-----BEGIN (?:RSA |EC |DSA |OPENSSH |ENCRYPTED |PGP )?PRIVATE KEY(?: BLOCK)?-----")),
    ("aws-access-key", re.compile(r"\b(?:AKIA|ASIA)[0-9A-Z]{16}\b")),
    ("github-token", re.compile(r"\b(?:gh[pousr]_[A-Za-z0-9]{36,}|github_pat_[A-Za-z0-9_]{60,})\b")),
    ("gitlab-token", re.compile(r"\bglpat-[A-Za-z0-9_-]{20,}\b")),
    ("slack-token", re.compile(r"\bxox[abprs]-[A-Za-z0-9-]{10,}\b")),
    ("google-api-key", re.compile(r"\bAIza[0-9A-Za-z_-]{35}\b")),
    ("stripe-key", re.compile(r"\b(?:sk|rk)_live_[0-9A-Za-z]{20,}\b")),
    ("openai-key", re.compile(r"\bsk-(?:proj-|ant-)?[A-Za-z0-9_-]{32,}\b")),
    ("jwt", re.compile(r"\beyJ[A-Za-z0-9_-]{10,}\.eyJ[A-Za-z0-9_-]{10,}\.[A-Za-z0-9_-]{10,}\b")),
    # scheme://user:password@host - database DSNs, SMTP and Redis URLs
    ("url-credentials", re.compile(r"\b[a-z][a-z0-9+.-]{1,20}://[^\s:/@'\"]{1,64}:(?P<value>[^\s@/'\"]{3,})@[^\s'\"]+",
                                   re.IGNORECASE)),
    # password = "…", 'db_pass' => '…', apiKey: "…", SECRET_KEY='…' - a quoted literal, not a variable
    ("assignment", re.compile(
        r"""(?ix)
        (?:^|[^a-z0-9])
        ['"]?[a-z0-9_.-]*(?:passw(?:or)?d|passwort|pwd|pass|secret|api[_-]?key|apikey|access[_-]?key|
            auth[_-]?token|access[_-]?token|refresh[_-]?token|client[_-]?secret|private[_-]?key|token|
            credentials?)['"]?
        \s*(?:=>|:=|=|:)\s*
        (?P<quote>['"])(?P<value>[^'"\s]{8,})(?P=quote)
        """)),
)


@dataclass(frozen=True)
class SecretHit:
    line: int | None        # 1-based; None = the file name itself is the hit
    rule: str               # e.g. "assignment", "name:.env"


def name_hit(file_name: str) -> str | None:
    """Rule name if the bare file name alone marks a secret file, else None."""
    lower = file_name.lower()
    for pattern in SECRET_NAMES:
        if fnmatch.fnmatchcase(lower, pattern.lower()):
            return f"name:{pattern}"
    return None


def content_hits(text: str, limit: int = 20) -> list[SecretHit]:
    """All rule hits in the text, at most `limit` (one is enough to skip the file; the rest helps reviewing)."""
    hits: list[SecretHit] = []
    for number, line in enumerate(text.splitlines(), start=1):
        if len(line) > 4000:                    # minified line: check the start only, regexes stay fast
            line = line[:4000]
        for rule, pattern in _RULES:
            for m in pattern.finditer(line):
                value = m.groupdict().get("value")
                if value is not None and _PLACEHOLDER.match(value):
                    continue                    # 'password' => 'changeme', '${DB_PASS}' ...
                hits.append(SecretHit(number, rule))
                break                           # one hit per rule and line is enough
            if len(hits) >= limit:
                return hits
    return hits


def check(rel_path: str, text: str) -> list[SecretHit]:
    """Name rule first (no need to read further), then the content rules."""
    rule = name_hit(rel_path.rsplit("/", 1)[-1])
    if rule is not None:
        return [SecretHit(None, rule)]
    return content_hits(text)


def allowed(rel_path: str, patterns: list[str]) -> bool:
    """secret_allow entries are globs on the relative path with '/', e.g. 'config/*.example.php'."""
    return any(fnmatch.fnmatchcase(rel_path, p.replace("\\", "/").lstrip("/")) for p in patterns)
