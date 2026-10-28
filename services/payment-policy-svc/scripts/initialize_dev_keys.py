"""Initialize a local Process C/Process B Ed25519 key pair once."""

from __future__ import annotations

import base64
import os
import subprocess
import sys
from pathlib import Path

from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PrivateKey

REPOSITORY_ROOT = Path(__file__).resolve().parents[3]
PRIVATE_ENV_PATH = Path(__file__).resolve().parents[1] / ".env"
PUBLIC_ENV_PATH = REPOSITORY_ROOT / "services" / "mandate-gate" / ".env"
PRIVATE_ENV_NAME = "MANDATE_SIGNING_PRIVATE_KEY_B64"
PUBLIC_ENV_NAME = "MANDATE_GATE_PUBLIC_KEY_B64"


def _read_key_value(path: Path, name: str) -> str | None:
    if not path.exists():
        return None
    for line in path.read_text(encoding="utf-8").splitlines():
        key, separator, value = line.partition("=")
        if separator and key.strip() == name:
            return value.strip()
    return None


def _is_git_ignored(path: Path) -> bool:
    result = subprocess.run(
        ["git", "check-ignore", "--no-index", "--quiet", str(path)],
        cwd=REPOSITORY_ROOT,
        check=False,
        capture_output=True,
        text=True,
    )
    return result.returncode == 0


def _append_key(path: Path, name: str, value: str) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    prefix = "" if not path.exists() or path.read_bytes().endswith(b"\n") else "\n"
    with path.open("a", encoding="utf-8", newline="\n") as environment_file:
        environment_file.write(f"{prefix}{name}={value}\n")


def initialize_key_pair(
    private_env_path: Path = PRIVATE_ENV_PATH,
    public_env_path: Path = PUBLIC_ENV_PATH,
) -> None:
    private_value = _read_key_value(private_env_path, PRIVATE_ENV_NAME)
    public_value = _read_key_value(public_env_path, PUBLIC_ENV_NAME)
    if private_value is not None and public_value is not None:
        raise RuntimeError("Both key files already contain keys; refusing to overwrite.")
    if private_value is not None or public_value is not None:
        raise RuntimeError(
            "Only one side of the key pair exists; resolve the incomplete setup "
            "before generating a replacement."
        )
    if not _is_git_ignored(private_env_path) or not _is_git_ignored(public_env_path):
        raise RuntimeError("Both .env paths must be Git-ignored before writing keys.")

    private_key = Ed25519PrivateKey.generate()
    private_b64 = base64.b64encode(private_key.private_bytes_raw()).decode("ascii")
    public_b64 = base64.b64encode(private_key.public_key().public_bytes_raw()).decode(
        "ascii"
    )
    _append_key(private_env_path, PRIVATE_ENV_NAME, private_b64)
    _append_key(public_env_path, PUBLIC_ENV_NAME, public_b64)


def main() -> int:
    try:
        initialize_key_pair()
    except (OSError, RuntimeError) as error:
        print(f"Development key initialization stopped: {error}")
        return 1
    print("Development signing and verification key pair initialized.")
    print("Private key was written only to Process C and was not displayed.")
    return 0


if __name__ == "__main__":
    sys.exit(main())
