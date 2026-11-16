"""RS256 signing (RSASSA-PKCS1-v1_5 with SHA-256) with the standard library only.

The Lambda runtime has no RSA library, and the stack reaper needs one
signature per dispatch to authenticate as a GitHub App. This module reads an
unencrypted RSA private key in PEM form (PKCS#1 "RSA PRIVATE KEY" or PKCS#8
"PRIVATE KEY") and signs in memory: the key is never written to a file and no
other program is run.

Each signature is blinded with a random factor and checked against the public
key before it is returned.
"""

import base64
import hashlib
import re
import secrets

# DER prefix of the DigestInfo for SHA-256 (RFC 8017, section 9.2, note 1).
_SHA256_DIGEST_INFO = bytes.fromhex("3031300d060960864801650304020105000420")
# rsaEncryption, 1.2.840.113549.1.1.1
_RSA_ENCRYPTION_OID = bytes.fromhex("2a864886f70d010101")
_PEM_RE = re.compile(r"-----BEGIN (RSA PRIVATE KEY|PRIVATE KEY)-----(.*?)-----END \1-----", re.DOTALL)


class KeyReadError(ValueError):
    """The private key could not be read."""


def _read_tlv(data, offset):
    """(tag, value, next offset) of the DER element at offset."""
    if offset + 2 > len(data):
        raise KeyReadError("truncated DER")
    tag = data[offset]
    length = data[offset + 1]
    offset += 2
    if length & 0x80:
        count = length & 0x7F
        if count == 0 or count > 4 or offset + count > len(data):
            raise KeyReadError("bad DER length")
        length = int.from_bytes(data[offset : offset + count], "big")
        offset += count
    end = offset + length
    if end > len(data):
        raise KeyReadError("truncated DER")
    return tag, data[offset:end], end


def _elements(body):
    """(tag, value) of each DER element in body."""
    items, offset = [], 0
    while offset < len(body):
        tag, value, offset = _read_tlv(body, offset)
        items.append((tag, value))
    return items


def _sequence(data):
    """Elements of a DER SEQUENCE given its full encoding."""
    tag, body, end = _read_tlv(data, 0)
    if tag != 0x30 or end != len(data):
        raise KeyReadError("not a DER SEQUENCE")
    return _elements(body)


def _integer(item):
    tag, value = item
    if tag != 0x02 or not value:
        raise KeyReadError("not a DER INTEGER")
    return int.from_bytes(value, "big", signed=True)


def _rsa_numbers(pkcs1):
    """(n, e, d, p, q, dp, dq, qinv) from a PKCS#1 RSAPrivateKey."""
    items = _sequence(pkcs1)
    if len(items) < 9 or _integer(items[0]) != 0:
        raise KeyReadError("not a two-prime RSAPrivateKey")
    return tuple(_integer(item) for item in items[1:9])


def load_private_key(pem):
    """Read an unencrypted RSA private key in PEM form; return its numbers."""
    match = _PEM_RE.search(pem or "")
    if not match:
        raise KeyReadError("no unencrypted RSA private key in PEM form")
    try:
        der = base64.b64decode("".join(match.group(2).split()), validate=True)
    except (ValueError, TypeError) as e:
        raise KeyReadError("bad base64 in PEM") from e
    if match.group(1) == "PRIVATE KEY":
        items = _sequence(der)
        if len(items) < 3 or items[1][0] != 0x30 or items[2][0] != 0x04:
            raise KeyReadError("not a PKCS#8 PrivateKeyInfo")
        algorithm = _elements(items[1][1])
        if not algorithm or algorithm[0] != (0x06, _RSA_ENCRYPTION_OID):
            raise KeyReadError("not an RSA key")
        der = items[2][1]
    numbers = _rsa_numbers(der)
    n, e, d, p, q = numbers[:5]
    if p * q != n or n.bit_length() < 2048 or e < 3:
        raise KeyReadError("inconsistent or weak RSA key")
    return numbers


def sign(pem, message):
    """RSASSA-PKCS1-v1_5 signature of message (bytes) with SHA-256."""
    n, e, d, p, q, dp, dq, qinv = load_private_key(pem)
    k = (n.bit_length() + 7) // 8
    t = _SHA256_DIGEST_INFO + hashlib.sha256(message).digest()
    if k < len(t) + 11:
        raise KeyReadError("key too short")
    encoded = b"\x00\x01" + b"\xff" * (k - len(t) - 3) + b"\x00" + t
    m = int.from_bytes(encoded, "big")

    # Blind, sign with the CRT, unblind, then check the result.
    while True:
        r = secrets.randbelow(n - 2) + 2
        try:
            r_inv = pow(r, -1, n)
            break
        except ValueError:
            continue
    blinded = (m * pow(r, e, n)) % n
    s1 = pow(blinded, dp, p)
    s2 = pow(blinded, dq, q)
    h = (qinv * (s1 - s2)) % p
    s = ((s2 + h * q) * r_inv) % n
    if pow(s, e, n) != m:
        raise RuntimeError("RSA signature check failed")
    return s.to_bytes(k, "big")
