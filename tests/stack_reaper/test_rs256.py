"""The stack reaper signs its GitHub App token in memory, with no other program and no key file."""

import base64
import json
import subprocess
import sys
import tempfile
from unittest.mock import patch

import pytest
import rs256
from cryptography.hazmat.primitives import hashes, serialization
from cryptography.hazmat.primitives.asymmetric import padding, rsa


def _pem(key, fmt):
    return key.private_bytes(serialization.Encoding.PEM, fmt, serialization.NoEncryption()).decode()


@pytest.fixture(scope="module")
def key():
    return rsa.generate_private_key(public_exponent=65537, key_size=2048)


@pytest.mark.parametrize(
    "fmt", [serialization.PrivateFormat.TraditionalOpenSSL, serialization.PrivateFormat.PKCS8], ids=["pkcs1", "pkcs8"]
)
def test_signature_matches_the_library_and_verifies(key, fmt):
    message = b"header.payload"
    signature = rs256.sign(_pem(key, fmt), message)
    # PKCS#1 v1.5 is deterministic: the same key and message give the same signature.
    assert signature == key.sign(message, padding.PKCS1v15(), hashes.SHA256())
    key.public_key().verify(signature, message, padding.PKCS1v15(), hashes.SHA256())


def test_many_messages_verify(key):
    pem = _pem(key, serialization.PrivateFormat.TraditionalOpenSSL)
    for i in range(20):
        message = f"message {i}".encode() * (i + 1)
        key.public_key().verify(rs256.sign(pem, message), message, padding.PKCS1v15(), hashes.SHA256())


def test_encrypted_key_is_refused(key):
    pem = key.private_bytes(
        serialization.Encoding.PEM,
        serialization.PrivateFormat.PKCS8,
        serialization.BestAvailableEncryption(b"passphrase"),
    ).decode()
    with pytest.raises(rs256.KeyReadError):
        rs256.sign(pem, b"x")


def test_short_key_is_refused():
    small = rsa.generate_private_key(public_exponent=65537, key_size=1024)
    with pytest.raises(rs256.KeyReadError):
        rs256.sign(_pem(small, serialization.PrivateFormat.TraditionalOpenSSL), b"x")


@pytest.mark.parametrize(
    "pem",
    [
        "",
        "not a key",
        "-----BEGIN RSA PRIVATE KEY-----\nAAAA\n-----END RSA PRIVATE KEY-----",
        "-----BEGIN RSA PRIVATE KEY-----\n!!!!\n-----END RSA PRIVATE KEY-----",
        "-----BEGIN PRIVATE KEY-----\nMAA=\n-----END PRIVATE KEY-----",
    ],
)
def test_malformed_key_is_refused(pem):
    with pytest.raises(rs256.KeyReadError):
        rs256.sign(pem, b"x")


def test_app_token_is_signed_without_a_library_another_program_or_a_key_file(key):
    import github_dispatch

    pem = _pem(key, serialization.PrivateFormat.TraditionalOpenSSL)
    blocked = {name: None for name in list(sys.modules) if name == "cryptography" or name.startswith("cryptography.")}
    blocked["cryptography"] = None

    def refuse(*args, **kwargs):
        raise AssertionError("no other program and no temporary file may be used")

    with (
        patch.dict(sys.modules, blocked),
        patch.object(subprocess, "run", refuse),
        patch.object(tempfile, "NamedTemporaryFile", refuse),
    ):
        token = github_dispatch._generate_jwt(12345, pem)

    header_b64, payload_b64, signature_b64 = token.split(".")
    pad = lambda part: part + "=" * (-len(part) % 4)  # noqa: E731
    assert json.loads(base64.urlsafe_b64decode(pad(header_b64))) == {"alg": "RS256", "typ": "JWT"}
    assert json.loads(base64.urlsafe_b64decode(pad(payload_b64)))["iss"] == "12345"
    key.public_key().verify(
        base64.urlsafe_b64decode(pad(signature_b64)),
        f"{header_b64}.{payload_b64}".encode(),
        padding.PKCS1v15(),
        hashes.SHA256(),
    )
