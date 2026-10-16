"""Corporate-PC TLS/proxy regression tests (no network, no optional deps)."""
from __future__ import annotations

import ssl

import httpx
import pytest

from routegis.errors import ProviderError
from routegis.providers.download import FetchContext
from routegis.providers.ecology.client import EcologyClient
from routegis.providers.overpass.client import OverpassClient
from routegis.providers.tls import (
    create_httpx_client,
    create_ssl_context,
    is_certificate_verification_error,
    resolve_proxy_url,
)
from routegis.providers.vworld.client import VWorldClient


def _chained_cert_error(request: httpx.Request):
    try:
        raise ssl.SSLCertVerificationError("self-signed certificate in certificate chain")
    except ssl.SSLCertVerificationError as exc:
        raise httpx.ConnectError("TLS failed", request=request) from exc


def test_ssl_context_requires_verification_and_hostname():
    context = create_ssl_context()
    assert context.verify_mode == ssl.CERT_REQUIRED
    assert context.check_hostname is True


def test_ssl_context_loads_alongside_qgis_bundle_env(monkeypatch, tmp_path):
    # QGIS points SSL_CERT_FILE at its bundled public roots; system trust must
    # still be additive and must not crash when the file is missing/invalid.
    monkeypatch.setenv("SSL_CERT_FILE", str(tmp_path / "missing.pem"))
    context = create_ssl_context()
    assert context.verify_mode == ssl.CERT_REQUIRED


def test_is_certificate_error_detects_plain_and_chained():
    request = httpx.Request("GET", "https://example.test")
    plain = httpx.ConnectError("[SSL: CERTIFICATE_VERIFY_FAILED] fail", request=request)
    assert is_certificate_verification_error(plain) is True
    try:
        _chained_cert_error(request)
    except httpx.ConnectError as exc:
        assert is_certificate_verification_error(exc) is True
    other = httpx.ConnectError("[Errno 11001] getaddrinfo failed", request=request)
    assert is_certificate_verification_error(other) is False
    assert is_certificate_verification_error(None) is False


def test_resolve_proxy_defers_to_explicit_env(monkeypatch):
    monkeypatch.setenv("HTTPS_PROXY", "http://proxy.example:8080")
    assert resolve_proxy_url() is None
    monkeypatch.delenv("HTTPS_PROXY")
    monkeypatch.delenv("HTTP_PROXY", raising=False)
    monkeypatch.delenv("https_proxy", raising=False)
    monkeypatch.delenv("http_proxy", raising=False)
    # No env proxy on CI; Windows/QGIS proxies are environment-dependent, so
    # only assert the helper does not crash and returns str or None.
    assert resolve_proxy_url() is None or isinstance(resolve_proxy_url(), str)


def test_create_httpx_client_uses_system_trust():
    with create_httpx_client(timeout=5) as client:
        pool = getattr(getattr(client, "_transport", None), "_pool", None)
        context = getattr(pool, "_ssl_context", None)
        assert isinstance(context, ssl.SSLContext)
        assert context.verify_mode == ssl.CERT_REQUIRED


@pytest.mark.parametrize("chained", [True, False])
def test_vworld_cert_error_fails_fast_with_guidance(chained):
    requests: list[httpx.Request] = []
    sleeps: list[float] = []

    def handler(request: httpx.Request):
        requests.append(request)
        if chained:
            return _chained_cert_error(request)
        raise httpx.ConnectError(
            "[SSL: CERTIFICATE_VERIFY_FAILED] certificate verify failed",
            request=request,
        )

    with httpx.Client(transport=httpx.MockTransport(handler)) as http_client:
        client = VWorldClient("secret-key", client=http_client, sleep=sleeps.append)
        with pytest.raises(ProviderError) as caught:
            client.discover_layers()
    message = str(caught.value)
    assert "Windows" in message
    assert "인증서" in message
    assert "QGIS" in message or "프록시" in message
    assert "secret-key" not in message
    assert len(requests) == 1
    assert sleeps == []


def test_ecology_cert_error_fails_fast_with_guidance():
    requests: list[httpx.Request] = []

    def handler(request: httpx.Request):
        requests.append(request)
        return _chained_cert_error(request)

    with httpx.Client(transport=httpx.MockTransport(handler)) as http_client:
        client = EcologyClient("secret-key", client=http_client, sleep=lambda _: None)
        context = FetchContext(provider="Ecology", request_budget=10)
        with pytest.raises(ProviderError) as caught:
            client._response_bytes({"serviceKey": "secret-key"}, context)
    message = str(caught.value)
    assert "인증서" in message
    assert "secret-key" not in message
    assert len(requests) == 1


def test_overpass_cert_error_fails_fast_with_guidance():
    requests: list[httpx.Request] = []
    sleeps: list[float] = []

    def handler(request: httpx.Request):
        requests.append(request)
        return _chained_cert_error(request)

    with httpx.Client(transport=httpx.MockTransport(handler)) as http_client:
        client = OverpassClient(client=http_client, sleep=sleeps.append)
        context = FetchContext(provider="Overpass", request_budget=10)
        with pytest.raises(ProviderError) as caught:
            client._response_bytes("[out:json];out;", context)
    message = str(caught.value)
    assert "인증서" in message
    assert len(requests) == 1
    assert sleeps == []


def test_ecology_wrong_feature_message_stays_debuggable():
    from xml.etree import ElementTree as ET

    client = EcologyClient("secret-key-xyz")
    try:
        xml = (
            '<wfs:FeatureCollection xmlns:wfs="http://www.opengis.net/wfs" '
            'xmlns:gml="http://www.opengis.net/gml">'
            '<gml:featureMember><wrong:foo xmlns:wrong="http://example.com">x</wrong:foo>'
            "</gml:featureMember></wfs:FeatureCollection>"
        )
        root = ET.fromstring(xml)
        members = EcologyClient._feature_members(root)
        with pytest.raises(ProviderError) as caught:
            client._parse_feature_member(members[0], "EPSG:5186")
        message = client._redact(str(caught.value))
        assert "tbl_opn_eczm" in message
        assert "foo" in message
        assert "[redacted URL]" not in message
        assert "secret-key-xyz" not in message
    finally:
        client.close()
