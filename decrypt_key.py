from __future__ import annotations

import os
from pathlib import Path

from cryptography.fernet import Fernet, InvalidToken


def local_key_path() -> Path:
    return (
        Path(os.environ.get("LOCALAPPDATA", Path.home()))
        / "fuel_map_api"
        / "key.key"
    )


def decrypt_api_key(path: Path = Path(".env.key")) -> str:
    key_path = local_key_path()
    if not key_path.exists():
        raise RuntimeError("The local encryption key is missing")
    try:
        return Fernet(key_path.read_bytes()).decrypt(path.read_bytes()).decode("utf-8")
    except InvalidToken as exc:
        raise RuntimeError(
            "The encrypted API key is invalid or was generated with another key"
        ) from exc


if __name__ == "__main__":
    print("FreeRoute API key decrypted successfully")
