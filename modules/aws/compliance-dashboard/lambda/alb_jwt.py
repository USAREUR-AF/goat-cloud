"""
Verification of the signed identity token an Application Load Balancer adds
to a request after its authenticate-oidc action (header x-amzn-oidc-data).

The token is an ES256 JWT. The load balancer publishes the public key for a
key id at https://public-keys.auth.elb.<region>.amazonaws.com/<kid>.

A token is accepted only when all of these hold:
  - the algorithm is ES256,
  - the signature verifies against the published key for its key id,
  - the "signer" header names the expected load balancer,
  - the token has not expired.

The Lambda runtime ships no elliptic-curve library, so P-256 signature
verification is implemented here with integers. Verification handles only
public values (public key, signature, message), so it needs no
constant-time arithmetic.
"""

import base64
import hashlib
import json
import re
import time
import urllib.request

# NIST P-256 (secp256r1) domain parameters.
_P = 0xFFFFFFFF00000001000000000000000000000000FFFFFFFFFFFFFFFFFFFFFFFF
_A = _P - 3
_B = 0x5AC635D8AA3A93E7B3EBBD55769886BC651D06B0CC53B0F63BCE3C3E27D2604B
_N = 0xFFFFFFFF00000000FFFFFFFFFFFFFFFFBCE6FAADA7179E84F3B9CAC2FC632551
_GX = 0x6B17D1F2E12C4247F8BCE6E563A440F277037D812DEB33A0F4A13945D898C296
_GY = 0x4FE342E2FE1A7F9B8EE7EB4A7C0F9E162BCE33576B315ECECBB6406837BF51F5

# DER prefix of a SubjectPublicKeyInfo holding an uncompressed P-256 point.
_SPKI_P256_PREFIX = bytes.fromhex("3059301306072a8648ce3d020106082a8648ce3d030107034200")

_KID_RE = re.compile(r"^[0-9a-fA-F-]{36}$")
_REGION_RE = re.compile(r"^[a-z]{2}(-[a-z]+)+-\d$")
_MAX_TOKEN_LEN = 16384
_CLOCK_SKEW = 60
_KEY_FETCH_TIMEOUT = 3

_key_cache = {}  # (region, kid) -> (x, y)
_verified_cache = {}  # sha256(token) -> (exp, claims)
_VERIFIED_CACHE_MAX = 256


def _b64url_decode(segment):
    """Decode base64url with or without padding (the load balancer pads)."""
    segment = segment.rstrip("=")
    return base64.urlsafe_b64decode(segment + "=" * (-len(segment) % 4))


def _on_curve(x, y):
    return 0 < x < _P and 0 < y < _P and (y * y - (x * x * x + _A * x + _B)) % _P == 0


def _jacobian_double(pt):
    x, y, z = pt
    if not y or not z:
        return (0, 1, 0)
    ysq = y * y % _P
    s = 4 * x * ysq % _P
    m = (3 * x * x + _A * pow(z, 4, _P)) % _P
    nx = (m * m - 2 * s) % _P
    return nx, (m * (s - nx) - 8 * ysq * ysq) % _P, 2 * y * z % _P


def _jacobian_add(p1, p2):
    if not p1[2]:
        return p2
    if not p2[2]:
        return p1
    x1, y1, z1 = p1
    x2, y2, z2 = p2
    z1sq = z1 * z1 % _P
    z2sq = z2 * z2 % _P
    u1 = x1 * z2sq % _P
    u2 = x2 * z1sq % _P
    s1 = y1 * z2sq * z2 % _P
    s2 = y2 * z1sq * z1 % _P
    if u1 == u2:
        return _jacobian_double(p1) if s1 == s2 else (0, 1, 0)
    h = (u2 - u1) % _P
    r = (s2 - s1) % _P
    hsq = h * h % _P
    hcu = hsq * h % _P
    nx = (r * r - hcu - 2 * u1 * hsq) % _P
    return nx, (r * (u1 * hsq - nx) - s1 * hcu) % _P, h * z1 * z2 % _P


def _double_scalar_mul(k1, point1, k2, point2):
    """Return the affine x of k1*point1 + k2*point2, or None at infinity."""
    p1 = (*point1, 1)
    p2 = (*point2, 1)
    both = _jacobian_add(p1, p2)
    acc = (0, 1, 0)
    for bit in range(max(k1.bit_length(), k2.bit_length()) - 1, -1, -1):
        acc = _jacobian_double(acc)
        b1 = (k1 >> bit) & 1
        b2 = (k2 >> bit) & 1
        if b1 and b2:
            acc = _jacobian_add(acc, both)
        elif b1:
            acc = _jacobian_add(acc, p1)
        elif b2:
            acc = _jacobian_add(acc, p2)
    if not acc[2]:
        return None
    z_inv = pow(acc[2], -1, _P)
    return acc[0] * z_inv * z_inv % _P


def verify_p256_sha256(public_point, message, signature):
    """Return True when signature (raw r||s, 64 bytes) is valid for message."""
    if len(signature) != 64 or public_point is None or not _on_curve(*public_point):
        return False
    r = int.from_bytes(signature[:32], "big")
    s = int.from_bytes(signature[32:], "big")
    if not (0 < r < _N and 0 < s < _N):
        return False
    digest = int.from_bytes(hashlib.sha256(message).digest(), "big")
    s_inv = pow(s, -1, _N)
    x = _double_scalar_mul(digest * s_inv % _N, (_GX, _GY), r * s_inv % _N, public_point)
    return x is not None and x % _N == r


def parse_p256_public_key_pem(pem):
    """Return the (x, y) point from a PEM SubjectPublicKeyInfo, or None."""
    body = "".join(line for line in pem.strip().splitlines() if not line.startswith("-----"))
    try:
        der = base64.b64decode(body, validate=True)
    except ValueError:
        return None
    if len(der) != len(_SPKI_P256_PREFIX) + 65 or not der.startswith(_SPKI_P256_PREFIX) or der[26] != 4:
        return None
    point = (int.from_bytes(der[27:59], "big"), int.from_bytes(der[59:91], "big"))
    return point if _on_curve(*point) else None


def _fetch_public_key(region, kid):
    """Fetch and cache the load balancer public key for a key id."""
    cached = _key_cache.get((region, kid))
    if cached:
        return cached
    # Host and scheme are fixed; region and kid are format-checked by the caller.
    url = f"https://public-keys.auth.elb.{region}.amazonaws.com/{kid}"
    with urllib.request.urlopen(url, timeout=_KEY_FETCH_TIMEOUT) as resp:  # nosec B310 - fixed https host
        pem = resp.read(4096).decode("ascii")
    point = parse_p256_public_key_pem(pem)
    if point:
        _key_cache[(region, kid)] = point
    return point


def verified_claims(token, expected_signer, region, expected_client="", expected_issuer="", now=None):
    """Return the claims of a valid load balancer token, or None.

    expected_signer is the ARN of the load balancer that must have signed the
    token; expected_client and expected_issuer are the OIDC client id and
    issuer of the sign-in rule that issued it (the token header's `client`
    and `iss`), so a token the same load balancer issued for another
    application's rule is refused. An empty expected value or region rejects
    every token.
    """
    if not token or not expected_signer or not region or len(token) > _MAX_TOKEN_LEN:
        return None
    if not expected_client or not expected_issuer:
        return None
    now = time.time() if now is None else now

    cache_key = hashlib.sha256(f"{expected_signer}|{expected_client}|{expected_issuer}|{token}".encode()).hexdigest()
    cached = _verified_cache.get(cache_key)
    if cached:
        if cached[0] + _CLOCK_SKEW > now:
            return cached[1]
        _verified_cache.pop(cache_key, None)

    try:
        header_b64, payload_b64, signature_b64 = token.split(".")
        header = json.loads(_b64url_decode(header_b64))
        claims = json.loads(_b64url_decode(payload_b64))
        signature = _b64url_decode(signature_b64)
        if not isinstance(header, dict) or not isinstance(claims, dict):
            return None
        if header.get("alg") != "ES256" or header.get("signer") != expected_signer:
            return None
        if header.get("client") != expected_client or header.get("iss") != expected_issuer:
            return None
        kid = header.get("kid", "")
        if not isinstance(kid, str) or not _KID_RE.match(kid) or not _REGION_RE.match(region):
            return None
        exp = float(claims.get("exp", header.get("exp", 0)))
        if exp + _CLOCK_SKEW <= now:
            return None
        public_point = _fetch_public_key(region, kid)
        if not verify_p256_sha256(public_point, f"{header_b64}.{payload_b64}".encode("ascii"), signature):
            return None
    except Exception as e:
        print(f"WARN: load balancer identity token rejected: {type(e).__name__}")
        return None

    if len(_verified_cache) >= _VERIFIED_CACHE_MAX:
        _verified_cache.clear()
    _verified_cache[cache_key] = (exp, claims)
    return claims
