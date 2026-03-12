from __future__ import annotations

import io
import zipfile
from typing import cast

import httpx
import pytest

from gutenbit import __version__
from gutenbit._http import gutenberg_request_headers
from gutenbit.download import (
    ALEPH_PGLAF_HOST,
    GUTENBERG_PGLAF_HOST,
    MAIN_SITE_ZIP_TIMEOUT,
    MIRROR_TIMEOUT,
    _main_site_html_zip_url,
    _mirror_html_url,
    download_html,
    get_last_download_source,
)


class _FakeResponse:
    def __init__(self, *, text: str = "", content: bytes = b"") -> None:
        self.text = text
        self.content = content

    def raise_for_status(self) -> None:
        return None


def _http_status_error(url: str, status_code: int) -> httpx.HTTPStatusError:
    request = httpx.Request("GET", url)
    response = httpx.Response(status_code, request=request)
    return httpx.HTTPStatusError(
        f"{status_code} while requesting {url}",
        request=request,
        response=response,
    )


def _zip_payload(filename: str, html: str) -> bytes:
    buffer = io.BytesIO()
    with zipfile.ZipFile(buffer, "w") as archive:
        archive.writestr(filename, html)
    return buffer.getvalue()


def test_mirror_html_url_construction_examples():
    assert (
        _mirror_html_url(ALEPH_PGLAF_HOST, 10)
        == "https://aleph.pglaf.org/cache/epub/10/pg10-images.html"
    )
    assert _mirror_html_url(GUTENBERG_PGLAF_HOST, 84) == (
        "https://gutenberg.pglaf.org/cache/epub/84/pg84-images.html"
    )
    assert _mirror_html_url(ALEPH_PGLAF_HOST, 1342) == (
        "https://aleph.pglaf.org/cache/epub/1342/pg1342-images.html"
    )


def test_download_html_prefers_aleph_pglaf_html(monkeypatch):
    calls: list[tuple[str, dict[str, object]]] = []
    aleph_url = _mirror_html_url(ALEPH_PGLAF_HOST, 1342)

    def _fake_get(url: str, **kwargs: object) -> _FakeResponse:
        calls.append((url, kwargs))
        if url == aleph_url:
            return _FakeResponse(text="<html>Aleph PGLAF</html>")
        raise AssertionError(f"Unexpected URL: {url}")

    monkeypatch.setattr("gutenbit.download.httpx.get", _fake_get)

    html = download_html(1342)

    assert html == "<html>Aleph PGLAF</html>"
    assert calls == [
        (
            aleph_url,
            {
                "follow_redirects": True,
                "headers": gutenberg_request_headers(),
                "timeout": MIRROR_TIMEOUT,
            },
        )
    ]
    assert get_last_download_source() == ALEPH_PGLAF_HOST


def test_download_html_falls_back_to_gutenberg_pglaf_html(monkeypatch):
    calls: list[tuple[str, dict[str, object]]] = []
    aleph_url = _mirror_html_url(ALEPH_PGLAF_HOST, 84)
    gutenberg_url = _mirror_html_url(GUTENBERG_PGLAF_HOST, 84)

    def _fake_get(url: str, **kwargs: object) -> _FakeResponse:
        calls.append((url, kwargs))
        if url == aleph_url:
            raise _http_status_error(url, 404)
        if url == gutenberg_url:
            return _FakeResponse(text="<html>Gutenberg PGLAF</html>")
        raise AssertionError(f"Unexpected URL: {url}")

    monkeypatch.setattr("gutenbit.download.httpx.get", _fake_get)

    html = download_html(84)

    assert html == "<html>Gutenberg PGLAF</html>"
    assert calls == [
        (
            aleph_url,
            {
                "follow_redirects": True,
                "headers": gutenberg_request_headers(),
                "timeout": MIRROR_TIMEOUT,
            },
        ),
        (
            gutenberg_url,
            {
                "follow_redirects": True,
                "headers": gutenberg_request_headers(),
                "timeout": MIRROR_TIMEOUT,
            },
        ),
    ]
    assert get_last_download_source() == GUTENBERG_PGLAF_HOST


def test_download_html_falls_back_to_main_site_zip(monkeypatch):
    calls: list[tuple[str, dict[str, object]]] = []
    aleph_url = _mirror_html_url(ALEPH_PGLAF_HOST, 84)
    gutenberg_url = _mirror_html_url(GUTENBERG_PGLAF_HOST, 84)
    main_site_url = _main_site_html_zip_url(84)

    def _fake_get(url: str, **kwargs: object) -> _FakeResponse:
        calls.append((url, kwargs))
        if url in {aleph_url, gutenberg_url}:
            raise _http_status_error(url, 404)
        if url == main_site_url:
            return _FakeResponse(content=_zip_payload("pg84-images.html", "<html>Main ZIP</html>"))
        raise AssertionError(f"Unexpected URL: {url}")

    monkeypatch.setattr("gutenbit.download.httpx.get", _fake_get)

    html = download_html(84)

    assert html == "<html>Main ZIP</html>"
    assert calls == [
        (
            aleph_url,
            {
                "follow_redirects": True,
                "headers": gutenberg_request_headers(),
                "timeout": MIRROR_TIMEOUT,
            },
        ),
        (
            gutenberg_url,
            {
                "follow_redirects": True,
                "headers": gutenberg_request_headers(),
                "timeout": MIRROR_TIMEOUT,
            },
        ),
        (
            main_site_url,
            {
                "follow_redirects": True,
                "headers": gutenberg_request_headers(),
                "timeout": MAIN_SITE_ZIP_TIMEOUT,
            },
        ),
    ]
    assert get_last_download_source() == "www.gutenberg.org"


def test_download_html_uses_short_mirror_timeouts_before_main_zip(monkeypatch):
    timeouts: dict[str, httpx.Timeout] = {}
    headers: dict[str, dict[str, str]] = {}
    aleph_url = _mirror_html_url(ALEPH_PGLAF_HOST, 84)
    gutenberg_url = _mirror_html_url(GUTENBERG_PGLAF_HOST, 84)
    main_site_url = _main_site_html_zip_url(84)

    def _fake_get(url: str, **kwargs: object) -> _FakeResponse:
        timeout = kwargs.get("timeout")
        assert isinstance(timeout, httpx.Timeout)
        timeouts[url] = timeout
        request_headers = kwargs.get("headers")
        assert isinstance(request_headers, dict)
        headers[url] = cast(dict[str, str], request_headers)
        if url in {aleph_url, gutenberg_url}:
            raise _http_status_error(url, 404)
        if url == main_site_url:
            return _FakeResponse(content=_zip_payload("pg84-images.html", "<html>Main ZIP</html>"))
        raise AssertionError(f"Unexpected URL: {url}")

    monkeypatch.setattr("gutenbit.download.httpx.get", _fake_get)

    html = download_html(84)

    assert html == "<html>Main ZIP</html>"
    assert timeouts[aleph_url] == MIRROR_TIMEOUT
    assert timeouts[gutenberg_url] == MIRROR_TIMEOUT
    assert timeouts[main_site_url] == MAIN_SITE_ZIP_TIMEOUT
    assert headers[aleph_url] == gutenberg_request_headers()
    assert headers[gutenberg_url] == gutenberg_request_headers()
    assert headers[main_site_url] == gutenberg_request_headers()


def test_gutenberg_request_headers_include_runtime_version():
    headers = gutenberg_request_headers()

    assert headers["User-Agent"] == f"gutenbit/{__version__} (+https://gutenbit.textualist.org)"


def test_download_html_raises_when_zip_has_no_html(monkeypatch):
    aleph_url = _mirror_html_url(ALEPH_PGLAF_HOST, 10)
    gutenberg_url = _mirror_html_url(GUTENBERG_PGLAF_HOST, 10)
    main_site_url = _main_site_html_zip_url(10)

    def _fake_get(url: str, **_kwargs: object) -> _FakeResponse:
        if url in {aleph_url, gutenberg_url}:
            raise _http_status_error(url, 404)
        if url == main_site_url:
            return _FakeResponse(content=_zip_payload("10.txt", "plain text"))
        raise AssertionError(f"Unexpected URL: {url}")

    monkeypatch.setattr("gutenbit.download.httpx.get", _fake_get)

    with pytest.raises(ValueError, match="No HTML file found in zip for book 10"):
        download_html(10)
    assert get_last_download_source() is None


def test_gutenberg_request_headers_identify_the_project():
    headers = gutenberg_request_headers()

    assert headers["User-Agent"].startswith("gutenbit/")
    assert "https://gutenbit.textualist.org" in headers["User-Agent"]
