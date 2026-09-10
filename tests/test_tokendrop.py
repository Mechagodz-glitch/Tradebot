from pathlib import Path

import pytest

from tradebot.envfile import read_env, upsert_env
from tradebot.tokendrop import DROP_PATH, RECIPIENTS_DIR, TokenDropError, apply_drop, read_drop, recipients, write_drop

cryptography = pytest.importorskip("cryptography")
pytest.importorskip("pyrage")


def _keypair(tmp_path: Path, name: str) -> tuple[Path, str]:
    from cryptography.hazmat.primitives import serialization as ser
    from cryptography.hazmat.primitives.asymmetric import ed25519
    k = ed25519.Ed25519PrivateKey.generate()
    priv = tmp_path / f"{name}_key"
    priv.write_bytes(k.private_bytes(ser.Encoding.PEM, ser.PrivateFormat.OpenSSH, ser.NoEncryption()))
    pub = k.public_key().public_bytes(ser.Encoding.OpenSSH, ser.PublicFormat.OpenSSH).decode() + f" {name}"
    return priv, pub


def test_drop_roundtrip_two_recipients_and_idempotent_apply(tmp_path):
    root = tmp_path / "repo"
    (root / RECIPIENTS_DIR).mkdir(parents=True)
    droplet_key, droplet_pub = _keypair(tmp_path, "droplet")
    laptop_key, laptop_pub = _keypair(tmp_path, "laptop")
    stranger_key, _ = _keypair(tmp_path, "stranger")
    (root / RECIPIENTS_DIR / "droplet.pub").write_text(droplet_pub + "\n")
    (root / RECIPIENTS_DIR / "laptop.pub").write_text(laptop_pub + "\n")
    (root / RECIPIENTS_DIR / "README.pub").write_text("not a key\n")          # ignored
    src_env = root / ".env"
    upsert_env(src_env, "KITE_ACCESS_TOKEN", "abc123TOKEN")
    assert [n for n, _ in recipients(str(root))] == ["droplet", "laptop"]

    res = write_drop(str(root))
    assert res["recipients"] == ["droplet", "laptop"]
    ct = (root / DROP_PATH).read_bytes()
    assert b"abc123TOKEN" not in ct                                          # never plaintext in the repo

    # each recipient machine has its own .env
    for key in (droplet_key, laptop_key):
        env = tmp_path / f"{key.name}.env"
        upsert_env(env, "KITE_API_KEY", "k")
        assert apply_drop(str(root), str(key), str(env))["updated"] is True
        assert read_env(env, "KITE_ACCESS_TOKEN") == "abc123TOKEN"
        assert read_env(env, "KITE_API_KEY") == "k"                          # other lines untouched
        assert apply_drop(str(root), str(key), str(env))["updated"] is False  # unchanged: no rewrite

    with pytest.raises(TokenDropError) as ei:
        read_drop(str(root), str(stranger_key))
    assert ei.value.code == "decrypt_failed"


def test_drop_errors(tmp_path):
    root = tmp_path / "repo"; root.mkdir()
    with pytest.raises(TokenDropError) as ei:
        write_drop(str(root), token="x")
    assert ei.value.code == "no_recipients"
    (root / RECIPIENTS_DIR).mkdir(parents=True)
    _, pub = _keypair(tmp_path, "d")
    (root / RECIPIENTS_DIR / "d.pub").write_text(pub)
    with pytest.raises(TokenDropError) as ei:
        write_drop(str(root))                                                 # no .env token
    assert ei.value.code == "no_token"
    with pytest.raises(TokenDropError) as ei:
        apply_drop(str(root), str(tmp_path / "missing_key"))
    assert ei.value.code == "no_drop"
