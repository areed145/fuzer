"""Apple MapKit JS tokens: short-lived JWTs the browser hands to `mapkit.init`, signed with the deployment's MapKit key.

Off until MAPKIT_TEAM_ID, MAPKIT_KEY_ID and MAPKIT_PRIVATE_KEY (the .p8 file's contents) are all set; the maps then
draw on free tiles with Leaflet instead. The key never leaves the server: only a token that expires within
TOKEN_SECONDS goes to a signed-in browser, and with FUZER_PUBLIC_URL set it is valid only from that origin.
"""

from __future__ import annotations

import os
import time

import jwt

TEAM_ENV, KEY_ENV, PRIVATE_KEY_ENV = "MAPKIT_TEAM_ID", "MAPKIT_KEY_ID", "MAPKIT_PRIVATE_KEY"
TOKEN_SECONDS = 30 * 60


class MapKitError(Exception):
    """The MapKit settings are present but unusable (most often a private key pasted incompletely)."""


def configured() -> bool:
    return all(os.environ.get(name, "").strip() for name in (TEAM_ENV, KEY_ENV, PRIVATE_KEY_ENV))


def _private_key() -> str:
    key = os.environ.get(PRIVATE_KEY_ENV, "").strip()
    # Render and .env files sometimes keep the .p8's line breaks as literal "\n"; restore them.
    if "\\n" in key and "\n" not in key:
        key = key.replace("\\n", "\n")
    if "-----BEGIN" not in key:  # the base64 body alone, without its PEM armour
        key = f"-----BEGIN PRIVATE KEY-----\n{key}\n-----END PRIVATE KEY-----"
    return key


def origin() -> str | None:
    value = os.environ.get("FUZER_PUBLIC_URL", "").strip().rstrip("/")
    return value or None


def token(now: float | None = None) -> dict:
    """A MapKit JS token, or {"token": None} when MapKit is not configured."""
    if not configured():
        return {"token": None}
    issued = int(now if now is not None else time.time())
    claims = {"iss": os.environ[TEAM_ENV].strip(), "iat": issued, "exp": issued + TOKEN_SECONDS}
    if origin():
        claims["origin"] = origin()
    try:
        signed = jwt.encode(claims, _private_key(), algorithm="ES256",
                            headers={"kid": os.environ[KEY_ENV].strip(), "typ": "JWT"})
    except (ValueError, TypeError, jwt.PyJWTError) as e:
        raise MapKitError(f"{PRIVATE_KEY_ENV} is not a usable MapKit private key ({type(e).__name__})") from None
    return {"token": signed, "expires": claims["exp"]}

