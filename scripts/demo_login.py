"""Sign in to a deployment as the demo account and save the session as a Playwright storage state.

    FUZER_DEMO_TOKEN=... python scripts/demo_login.py https://<service>.onrender.com demo-state.json

Reads the token from the environment only, and uses only the standard library. Open the deployment signed in with
`browser.new_context(storage_state="demo-state.json")` (Python or Node Playwright). The file holds a session cookie
that lasts at most 12 hours: keep it out of the repository.
"""

import json
import os
import sys
import time
import urllib.error
import urllib.request
from http.cookies import SimpleCookie
from urllib.parse import urlsplit


def main(base: str, state: str) -> None:
    token = os.environ.get("FUZER_DEMO_TOKEN")
    if not token:
        sys.exit("Set FUZER_DEMO_TOKEN in the environment")
    base = base.rstrip("/")
    request = urllib.request.Request(base + "/auth/demo", data=json.dumps({"token": token}).encode(),
                                     headers={"Content-Type": "application/json"}, method="POST")
    try:
        with urllib.request.urlopen(request, timeout=30) as response:
            body = json.load(response)
            cookies = SimpleCookie()
            for header in response.headers.get_all("Set-Cookie") or []:
                cookies.load(header)
    except urllib.error.HTTPError as e:
        sys.exit(f"Demo sign-in refused: HTTP {e.code} {e.read()[:200].decode(errors='replace')}")
    host = urlsplit(base).hostname
    saved = [{"name": name, "value": morsel.value, "domain": host, "path": morsel["path"] or "/",
              "expires": time.time() + int(morsel["max-age"] or 3600), "httpOnly": True,
              "secure": base.startswith("https://"), "sameSite": "Lax"} for name, morsel in cookies.items()]
    with open(state, "w") as f:
        json.dump({"cookies": saved, "origins": []}, f)
    print(f"Signed in as {body['display_name']} ({body['actor']}) in {body['project']}; session saved to {state}")


if __name__ == "__main__":
    if len(sys.argv) != 3:
        sys.exit(__doc__)
    main(sys.argv[1], sys.argv[2])
