#!/usr/bin/env python3
from __future__ import annotations

import sys
from pathlib import Path

FORBIDDEN_SUFFIXES = {".pem", ".key", ".p12", ".pfx", ".crt"}
FORBIDDEN_FILENAMES = {
    ".django_secret_key",
    ".env",
    ".envrc",
    "id_ed25519",
    "id_rsa",
}
ALLOWED_ENV_EXAMPLES = {
    ".env.example",
    "infra/.env.example",
}


def is_forbidden(path: Path) -> bool:
    normalized = path.as_posix()
    if normalized.startswith("./"):
        normalized = normalized[2:]
    name = path.name

    if normalized.startswith(".codeassistant/"):
        return True

    if name in FORBIDDEN_FILENAMES:
        return True

    if name.startswith(".env.") and normalized not in ALLOWED_ENV_EXAMPLES:
        return True

    return path.suffix.lower() in FORBIDDEN_SUFFIXES


def main(argv: list[str]) -> int:
    files = [Path(arg) for arg in argv[1:]]
    blocked = sorted({path.as_posix() for path in files if is_forbidden(path)})

    if not blocked:
        return 0

    print("Blocked commit: sensitive/local files detected:")
    for path in blocked:
        print(f"  - {path}")
    print("Move secrets to infra/.env and keep only *.example templates in git.")
    return 1


if __name__ == "__main__":
    raise SystemExit(main(sys.argv))
