from __future__ import annotations

import os
import ssl
from pathlib import Path
from urllib.parse import quote

import httpx


def is_certificate_verification_error(error: BaseException | None) -> bool:
    """Return True when *error* (or any chained cause) is a TLS verify failure."""
    while error is not None:
        if isinstance(error, ssl.SSLCertVerificationError) or (
            "CERTIFICATE_VERIFY_FAILED" in str(error)
        ):
            return True
        # httpx wraps the stdlib error; walk both __cause__ and __context__.
        error = error.__cause__ or error.__context__
    return False


def _candidate_ca_files() -> list[str]:
    """Collect explicit CA bundle paths from environment (QGIS + corporate)."""
    candidates: list[str] = []
    for name in (
        "ROUTEGIS_CA_BUNDLE",
        "ROUTEGIS_CA_FILE",
        "SSL_CERT_FILE",
        "CERT_FILE",
    ):
        value = os.getenv(name, "").strip().strip('"').strip("'")
        if value and value not in candidates:
            candidates.append(value)
    return candidates


def _candidate_ca_dirs() -> list[str]:
    candidates: list[str] = []
    for name in ("ROUTEGIS_CA_DIR", "SSL_CERT_DIR"):
        value = os.getenv(name, "").strip().strip('"').strip("'")
        if value and value not in candidates:
            candidates.append(value)
    return candidates


def create_ssl_context() -> ssl.SSLContext:
    """Combine HTTPX/certifi defaults with OS trust (corporate inspection CAs).

    Corporate PCs commonly trust an inspection proxy CA via the Windows
    ROOT store, while QGIS sets ``SSL_CERT_FILE`` to its bundled public roots.
    ``httpx.create_ssl_context()`` alone honours the env bundle but omits the
    Windows store; ``load_default_certs()`` alone may honour only one side
    depending on the Python/OpenSSL build. Load both explicitly, plus
    ``certifi`` and any ``ROUTEGIS_CA_BUNDLE`` override, so all company PCs
    verify with the same context.
    """
    context = httpx.create_ssl_context()
    # Explicit certifi bundle: QGIS may override SSL_CERT_FILE, so do not rely
    # solely on httpx defaults to include the public roots.
    try:
        import certifi  # type: ignore[import-not-found]

        bundle = certifi.where()
        if isinstance(bundle, str) and Path(bundle).is_file():
            try:
                context.load_verify_locations(cafile=bundle)
            except (OSError, ssl.SSLError):
                pass
    except ImportError:
        pass
    # Explicit extra bundles (corporate CA exported from browser, QGIS bundle).
    for candidate in _candidate_ca_files():
        try:
            if Path(candidate).is_file():
                context.load_verify_locations(cafile=candidate)
        except (OSError, ssl.SSLError):
            continue
    for candidate in _candidate_ca_dirs():
        try:
            if Path(candidate).is_dir():
                context.load_verify_locations(capath=candidate)
        except (OSError, ssl.SSLError):
            continue
    # Windows ROOT/CA stores used by corporate HTTPS inspection, plus the
    # default verify paths. Keep this last so system trust is additive even
    # when QGIS points SSL_CERT_FILE at its bundled public roots.
    try:
        context.load_default_certs()
    except (OSError, ssl.SSLError):
        pass
    return context


def _redact_proxy_password(proxy_url: str) -> str:
    """Hide proxy password for logs (keep host/port visible for debugging)."""
    try:
        from urllib.parse import urlsplit, urlunsplit

        parts = urlsplit(proxy_url)
        if parts.password:
            netloc = parts.hostname or ""
            if parts.username:
                netloc = f"{parts.username}:***@{netloc}"
            if parts.port:
                netloc = f"{netloc}:{parts.port}"
            return urlunsplit((parts.scheme, netloc, parts.path, parts.query, parts.fragment))
    except ValueError:
        pass
    return proxy_url


def _qgis_proxy_url() -> str | None:
    """Read QGIS Settings proxy (Options > Network) when running inside QGIS."""
    try:
        from qgis.PyQt.QtCore import QSettings  # type: ignore[import-not-found]
    except ImportError:
        return None
    try:
        settings = QSettings()
        enabled = settings.value("proxy/proxyEnabled", False)
        if isinstance(enabled, str):
            enabled = enabled.strip().lower() in {"1", "true", "yes", "on"}
        if not enabled:
            return None
        proxy_type = str(settings.value("proxy/proxyType", "HttpProxy") or "HttpProxy")
        host = str(settings.value("proxy/proxyHost", "") or "").strip()
        port = str(settings.value("proxy/proxyPort", "") or "").strip()
        user = str(settings.value("proxy/proxyUser", "") or "").strip()
        password = str(settings.value("proxy/proxyPassword", "") or "")
        if not host:
            return None
        scheme = "socks5h" if "socks" in proxy_type.lower() else "http"
        auth = ""
        if user:
            auth = quote(user, safe="") + (f":{quote(password, safe='')}" if password else "") + "@"
        port_suffix = f":{port}" if port else ""
        return f"{scheme}://{auth}{host}{port_suffix}"
    except Exception:
        return None


def resolve_proxy_url() -> str | None:
    """Return an explicit proxy URL for httpx, or None to rely on env/trust_env.

    Precedence: explicit HTTPS_PROXY/HTTP_PROXY env (httpx handles via
    trust_env, so return None) > QGIS Settings proxy > Windows/registry proxy
    via urllib (covers corporate PAC/manual proxy without env vars).
    """
    for name in ("HTTPS_PROXY", "https_proxy", "HTTP_PROXY", "http_proxy", "ALL_PROXY", "all_proxy"):
        if os.getenv(name, "").strip():
            return None
    qgis_proxy = _qgis_proxy_url()
    if qgis_proxy:
        return qgis_proxy
    try:
        import urllib.request

        proxies = urllib.request.getproxies()
        # Prefer https proxy for our https endpoints; fall back to http.
        for key in ("https", "http"):
            url = proxies.get(key, "").strip() if isinstance(proxies, dict) else ""
            if url:
                return url
    except Exception:
        pass
    return None


def create_httpx_client(
    *,
    timeout: float,
    follow_redirects: bool = False,
) -> httpx.Client:
    """Build a corporate-PC-safe httpx client (system trust + proxy)."""
    proxy = resolve_proxy_url()
    kwargs: dict = {
        "timeout": httpx.Timeout(timeout),
        "follow_redirects": follow_redirects,
        "verify": create_ssl_context(),
        "trust_env": True,
    }
    if proxy:
        kwargs["proxy"] = proxy
    return httpx.Client(**kwargs)


CERT_GUIDANCE = (
    "HTTPS 인증서 검증에 실패했습니다. 사내 보안 프록시(HTTPS 검사)의 "
    "루트 인증서가 Windows의 '신뢰할 수 있는 루트 인증 기관'에 등록되어 있는지 "
    "전산 담당자에게 확인해 주세요. 인증서 등록 후 QGIS를 완전히 종료하고 다시 실행해 주세요. "
    "프록시를 사용 중이면 QGIS '설정 > 옵션 > 네트워크' 프록시와 Windows 프록시 설정을 확인해 주세요. "
    "추가 CA 파일이 있으면 ROUTEGIS_CA_BUNDLE 환경변수에 경로를 지정할 수 있습니다."
)
