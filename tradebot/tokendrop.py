"""Encrypted hand-off of the daily Kite access token between machines, through the git repo.

The cloud research session can reach GitHub but nothing else; the droplet and the laptop can pull the
repo. So the token travels as an ``age`` ciphertext committed to ``data/secrets/kite_token.age``,
encrypted to the SSH public keys listed in ``deploy/keys/*.pub`` (the droplet's deploy key, the laptop's
key). Only a holder of one of those private keys can read it; the ciphertext is worthless after the
token expires the next morning. Plaintext never touches the repository."""

from __future__ import annotations

import json
from datetime import datetime, timezone
from pathlib import Path
from typing import Optional

from .envfile import read_env, upsert_env
from .errors import TradebotError

RECIPIENTS_DIR = "deploy/keys"
DROP_PATH = "data/secrets/kite_token.age"
ENV_KEY = "KITE_ACCESS_TOKEN"


class TokenDropError(TradebotError):
    category = "tokendrop"


def _pyrage():
    try:
        from pyrage import decrypt, encrypt, ssh  # type: ignore
    except ImportError as e:  # pragma: no cover
        raise TokenDropError("pyrage is not installed; run: uv pip install -e .", code="missing_dependency") from e
    return encrypt, decrypt, ssh


def recipients(root: str) -> list[tuple[str, str]]:
    """(name, public key line) for every ``deploy/keys/*.pub`` that is an ssh-ed25519 or ssh-rsa key."""
    out = []
    for p in sorted((Path(root) / RECIPIENTS_DIR).glob("*.pub")):
        line = p.read_text().strip().splitlines()[0] if p.read_text().strip() else ""
        if line.startswith(("ssh-ed25519 ", "ssh-rsa ")):
            out.append((p.stem, line))
    return out


def write_drop(root: str, token: Optional[str] = None, env_path: Optional[str] = None) -> dict:
    """Encrypt the current token (from .env unless given) to every recipient and write the drop file."""
    encrypt, _decrypt, ssh = _pyrage()
    env_path = env_path or str(Path(root) / ".env")
    token = token or read_env(env_path, ENV_KEY)
    if not token:
        raise TokenDropError(f"no {ENV_KEY} in {env_path}; log in first", code="no_token")
    recs = recipients(root)
    if not recs:
        raise TokenDropError(f"no recipient keys in {RECIPIENTS_DIR}/*.pub; run scripts/vps-token-sync.sh init on the target machine",
                             code="no_recipients")
    payload = json.dumps({ENV_KEY: token, "issued_at": datetime.now(timezone.utc).isoformat(),
                          "note": "Kite access token; expires around 06:00 IST the next trading day"}).encode()
    ct = encrypt(payload, [ssh.Recipient.from_str(line) for _name, line in recs])
    out = Path(root) / DROP_PATH
    out.parent.mkdir(parents=True, exist_ok=True)
    out.write_bytes(ct)
    return {"path": str(out), "recipients": [n for n, _ in recs], "bytes": len(ct)}


def read_drop(root: str, identity_path: str) -> dict:
    _encrypt, decrypt, ssh = _pyrage()
    drop = Path(root) / DROP_PATH
    if not drop.exists():
        raise TokenDropError(f"no drop file at {drop}", code="no_drop")
    ident_file = Path(identity_path).expanduser()
    if not ident_file.exists():
        raise TokenDropError(f"identity {ident_file} not found", code="no_identity")
    try:
        pt = decrypt(drop.read_bytes(), [ssh.Identity.from_buffer(ident_file.read_bytes())])
    except Exception as e:  # noqa: BLE001
        raise TokenDropError(f"could not decrypt {drop.name} with {ident_file}: {e}", code="decrypt_failed") from e
    return json.loads(pt.decode())


def apply_drop(root: str, identity_path: str, env_path: Optional[str] = None) -> dict:
    """Decrypt the drop and write the token into .env if it differs. Returns {updated, issued_at}."""
    env_path = env_path or str(Path(root) / ".env")
    data = read_drop(root, identity_path)
    token = data.get(ENV_KEY)
    if not token:
        raise TokenDropError("drop file has no token", code="bad_drop")
    current = read_env(env_path, ENV_KEY)
    if current == token:
        return {"updated": False, "issued_at": data.get("issued_at")}
    upsert_env(env_path, ENV_KEY, token)
    return {"updated": True, "issued_at": data.get("issued_at"), "env": env_path}
