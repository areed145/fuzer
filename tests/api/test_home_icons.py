"""The installed app's icon on an iPhone: in the page itself, and at the root addresses iOS asks for."""

import base64

from fastapi.testclient import TestClient

from fuzer.api import home


def test_the_page_carries_its_touch_icon_and_the_root_serves_it(tmp_path, monkeypatch):
    (tmp_path / "assets").mkdir()
    png = b"\x89PNG\r\n\x1a\nicon"
    (tmp_path / "assets" / "apple-touch-icon.png").write_bytes(png)
    (tmp_path / "assets" / "favicon-32.png").write_bytes(png + b"32")
    (tmp_path / "index.html").write_text('<link rel="apple-touch-icon" sizes="180x180" href="/assets/apple-touch-icon.png" />'
                                         '<link rel="manifest" href="/assets/manifest.webmanifest" />')
    monkeypatch.setattr(home, "UI_DIST", tmp_path)
    home._page.cache_clear()
    from fuzer.api import app
    client = TestClient(app)
    page = client.get("/").text
    # inlined: iOS saves a linked touch icon by fetching it again, and got a letter tile instead
    assert f'href="data:image/png;base64,{base64.b64encode(png).decode()}"' in page
    assert 'href="/assets/manifest.webmanifest"' in page
    for path in ("/apple-touch-icon.png", "/apple-touch-icon-precomposed.png", "/apple-touch-icon-180x180.png"):
        for r in (client.get(path), client.head(path)):
            assert (r.status_code, r.headers["content-type"]) == (200, "image/png"), path
    assert client.get("/apple-touch-icon.png").content == png
    assert client.get("/favicon.ico").content == png + b"32"
    assert client.get("/apple-touch-icon.png?name=../index.html").content == png
