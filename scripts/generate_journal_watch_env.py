#!/usr/bin/env python3
"""Generate missing Journal Watch app secrets without mangling the dotenv file."""

from __future__ import annotations

import argparse
import os
import re
import secrets
import sys
import tempfile
from pathlib import Path
from typing import Callable


DEFAULT_TEMPLATE = Path("apps/journal-watch/.env.example")
DEFAULT_OUTPUT = Path("apps/journal-watch/.env")

KEY_VALUE_RE = re.compile(r"^(?P<key>[A-Z0-9_]+)=(?P<value>[^\n\r]*)$")


def gen_token_urlsafe(length: int) -> Callable[[], str]:
    return lambda: secrets.token_urlsafe(length)


def gen_token_hex(length: int) -> Callable[[], str]:
    return lambda: secrets.token_hex(length)


GENERATORS: dict[str, Callable[[], str]] = {
    "DJANGO_SECRET_KEY": gen_token_urlsafe(64),
    "POSTGRES_PASSWORD": gen_token_urlsafe(24),
    "WEBHOOK_SECRET": gen_token_urlsafe(32),
    "CELERY_FLOWER_PASSWORD": gen_token_urlsafe(16),
    "SECRET_KEY": gen_token_hex(32),
    "PLANKA_POSTGRES_PASSWORD": gen_token_urlsafe(24),
    "OIDC_CLIENT_SECRET": gen_token_hex(32),
    "DEFAULT_ADMIN_PASSWORD": gen_token_urlsafe(24),
    "PLANKA_WEBHOOK_SECRET": gen_token_urlsafe(32),
}


class EnvGenerationError(RuntimeError):
    pass


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description="Create or update apps/journal-watch/.env by filling only missing secret values."
    )
    parser.add_argument(
        "--template",
        type=Path,
        default=DEFAULT_TEMPLATE,
        help=f"dotenv template to read (default: {DEFAULT_TEMPLATE})",
    )
    parser.add_argument(
        "--output",
        type=Path,
        default=DEFAULT_OUTPUT,
        help=f"dotenv file to create/update (default: {DEFAULT_OUTPUT})",
    )
    parser.add_argument(
        "--stdout",
        action="store_true",
        help="write the generated dotenv content to stdout instead of updating the output file",
    )
    return parser.parse_args()


def load_content(template_path: Path, output_path: Path) -> str:
    source_path = output_path if output_path.exists() else template_path
    try:
        return source_path.read_text(encoding="utf-8")
    except FileNotFoundError as exc:
        raise EnvGenerationError(f"dotenv source file not found: {source_path}") from exc


def render_content(content: str) -> tuple[str, list[str]]:
    generated_keys: list[str] = []
    rendered_lines: list[str] = []

    for line in content.splitlines(keepends=True):
        match = KEY_VALUE_RE.match(line.rstrip("\r\n"))
        if not match:
            rendered_lines.append(line)
            continue

        key = match.group("key")
        value = match.group("value")
        generator = GENERATORS.get(key)
        if generator is None:
            rendered_lines.append(line)
            continue

        if value.strip():
            rendered_lines.append(line)
            continue

        newline = line[len(line.rstrip("\r\n")) :]
        rendered_lines.append(f"{key}={generator()}{newline}")
        generated_keys.append(key)

    return "".join(rendered_lines), generated_keys


def atomic_write(path: Path, content: str) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with tempfile.NamedTemporaryFile(
        "w", encoding="utf-8", dir=path.parent, delete=False
    ) as temp_file:
        temp_file.write(content)
        temp_name = temp_file.name

    os.replace(temp_name, path)


def main() -> int:
    args = parse_args()

    try:
        content = load_content(args.template, args.output)
        rendered, generated_keys = render_content(content)
    except EnvGenerationError as exc:
        print(f"error: {exc}", file=sys.stderr)
        return 1

    if args.stdout:
        sys.stdout.write(rendered)
    else:
        atomic_write(args.output, rendered)
        print(f"Wrote {args.output}")

    if generated_keys:
        print("Filled missing secrets:", ", ".join(generated_keys), file=sys.stderr)
    else:
        print("No missing allowlisted secrets needed generation.", file=sys.stderr)

    return 0


if __name__ == "__main__":
    raise SystemExit(main())
