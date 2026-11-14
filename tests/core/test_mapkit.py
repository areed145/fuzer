"""MapKit JS tokens: off without all three settings, ES256 with the key id, short-lived and bound to the origin."""

import jwt
import pytest
from cryptography.hazmat.primitives import serialization
from cryptography.hazmat.primitives.asymmetric import ec

from fuzer.core import mapkit


@pytest.fixture
def key():
    private = ec.generate_private_key(ec.SECP256R1())
    pem = private.private_bytes(serialization.Encoding.PEM, serialization.PrivateFormat.PKCS8,
                                serialization.NoEncryption()).decode()
    return private, pem


def test_off_until_all_three_settings_are_present(monkeypatch, key):
    for name in (mapkit.TEAM_ENV, mapkit.KEY_ENV, mapkit.PRIVATE_KEY_ENV):
        monkeypatch.delenv(name, raising=False)
    assert mapkit.token() == {"token": None}
    monkeypatch.setenv(mapkit.TEAM_ENV, "TEAM123456")
    monkeypatch.setenv(mapkit.KEY_ENV, "KEY1234567")
    assert mapkit.token() == {"token": None}


@pytest.mark.parametrize("form", ["pem", "escaped", "body"])
def test_signed_token_is_short_lived_and_bound_to_the_public_origin(monkeypatch, key, form):
    private, pem = key
    value = {"pem": pem, "escaped": pem.replace("\n", "\\n"),
             "body": "".join(pem.strip().splitlines()[1:-1])}[form]
    monkeypatch.setenv(mapkit.TEAM_ENV, "TEAM123456")
    monkeypatch.setenv(mapkit.KEY_ENV, "KEY1234567")
    monkeypatch.setenv(mapkit.PRIVATE_KEY_ENV, value)
    monkeypatch.setenv("FUZER_PUBLIC_URL", "https://fuzer.example.com/")
    out = mapkit.token(now=1_800_000_000)
    header = jwt.get_unverified_header(out["token"])
    assert header["alg"] == "ES256" and header["kid"] == "KEY1234567"
    claims = jwt.decode(out["token"], private.public_key(), algorithms=["ES256"], options={"verify_exp": False, "verify_iat": False})
    assert claims == {"iss": "TEAM123456", "iat": 1_800_000_000, "exp": 1_800_000_000 + mapkit.TOKEN_SECONDS,
                      "origin": "https://fuzer.example.com"}
    assert out["expires"] == claims["exp"] and mapkit.TOKEN_SECONDS <= 3600


def test_an_unusable_key_is_an_error_that_does_not_echo_it(monkeypatch):
    monkeypatch.setenv(mapkit.TEAM_ENV, "TEAM123456")
    monkeypatch.setenv(mapkit.KEY_ENV, "KEY1234567")
    monkeypatch.setenv(mapkit.PRIVATE_KEY_ENV, "not-a-key-secretvalue")
    with pytest.raises(mapkit.MapKitError) as e:
        mapkit.token()
    assert "secretvalue" not in str(e.value)
