#!/usr/bin/env python3
"""Pre-commit lint: reject obvious PII in logging calls.

Catches the easy cases — direct interpolation of msisdn / imei / wallet_id /
imsi / account into log lines without going through obs.redact(). Not a full
taint analysis; a defence-in-depth net for the common foot-gun. Real
enforcement happens in obs.log() at runtime.
"""
from __future__ import annotations

import re
import sys
from pathlib import Path

# Match: log.info(f"... {msisdn} ..."), logger.warning(... msisdn=msisdn ...),
# print(... msisdn ...) where msisdn is a bare identifier outside redact().
PII_FIELDS = ("msisdn", "imsi", "imei", "wallet_id", "account_hash", "account_number")
LOG_CALL = re.compile(r"\b(log|logger|logging|print)\s*\.\s*(?:info|warn|warning|error|debug|exception|critical)?\s*\(", re.IGNORECASE)
SAFE = re.compile(r"\bredact\s*\(", re.IGNORECASE)


def scan(path: Path) -> list[tuple[int, str]]:
    issues: list[tuple[int, str]] = []
    try:
        text = path.read_text(encoding="utf-8")
    except (OSError, UnicodeDecodeError):
        return issues
    for lineno, line in enumerate(text.splitlines(), start=1):
        if not LOG_CALL.search(line):
            continue
        if SAFE.search(line):
            continue
        for field in PII_FIELDS:
            # Catches f"{msisdn}", "msisdn=" + bare ref, msisdn=msisdn kwarg
            if re.search(rf"\b{field}\b", line):
                issues.append((lineno, line.strip()))
                break
    return issues


def main() -> int:
    rc = 0
    for arg in sys.argv[1:]:
        p = Path(arg)
        if p.suffix != ".py":
            continue
        for lineno, line in scan(p):
            print(f"{p}:{lineno}: possible PII in log — wrap with redact()")
            print(f"    {line}")
            rc = 1
    return rc


if __name__ == "__main__":
    sys.exit(main())
