from __future__ import annotations

import argparse
import getpass
import os
from pathlib import Path

from cryptography.fernet import Fernet


def local_key_path() -> Path:
    return (
        Path(os.environ.get("LOCALAPPDATA", Path.home()))
        / "fuel_map_api"
        / "key.key"
    )


def encrypt_api_key(api_key: str, output: Path) -> None:
    api_key = api_key.strip()
    if not api_key:
        raise ValueError("The API key cannot be empty")

    key_path = local_key_path()
    key_path.parent.mkdir(parents=True, exist_ok=True)
    if not key_path.exists():
        key_path.write_bytes(Fernet.generate_key())
        key_path.chmod(0o600)

    output.write_bytes(Fernet(key_path.read_bytes()).encrypt(api_key.encode("utf-8")))
    output.chmod(0o600)


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--output", type=Path, default=Path(".env.key"))
    args = parser.parse_args()

    api_key = getpass.getpass("FreeRoute API key: ")
    encrypt_api_key(api_key, args.output)
    print(f"Encrypted API key written to {args.output}")


if __name__ == "__main__":
    main()
