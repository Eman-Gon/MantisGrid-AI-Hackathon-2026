#!/usr/bin/env python3
"""Load the repository's local .env before launching a development command.

Existing environment variables take precedence. Secrets are passed through the
environment, never command arguments. The judged entry point stays unchanged.
"""
from __future__ import annotations

import os
import sys
from pathlib import Path

from dotenv import load_dotenv


def main() -> None:
    load_dotenv(Path(__file__).resolve().parents[2] / ".env", override=False)
    if len(sys.argv) < 2:
        raise SystemExit("usage: with_env.py COMMAND [ARG ...]")
    if not os.environ.get("FEATHERLESS_API_KEY"):
        print("Warning: FEATHERLESS_API_KEY is missing; model calls are disabled.",
              file=sys.stderr)
    os.execvp(sys.argv[1], sys.argv[1:])


if __name__ == "__main__":
    main()
