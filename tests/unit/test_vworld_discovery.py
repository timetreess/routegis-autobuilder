from __future__ import annotations

from unittest.mock import Mock

import pytest

from routegis.app.project_service import ProjectService
from routegis.errors import RouteGISError
from routegis.providers import VWorldClient


@pytest.mark.parametrize("failure", [False, True])
def test_discovery_closes_the_client_on_success_and_failure(monkeypatch, failure):
    client = Mock(spec=VWorldClient)
    client.discover_layers.return_value = ["layer"]
    if failure:
        client.discover_layers.side_effect = RuntimeError("catalog unavailable")
    service = ProjectService()
    monkeypatch.setattr(service, "_provider", Mock(return_value=client))

    if failure:
        with pytest.raises(RuntimeError, match="catalog unavailable"):
            service.discover_vworld_layers("key")
    else:
        assert service.discover_vworld_layers("key") == ["layer"]
    client.close.assert_called_once()


def test_discovery_rejects_a_non_vworld_provider(monkeypatch):
    service = ProjectService()
    monkeypatch.setattr(service, "_provider", Mock(return_value=object()))
    with pytest.raises(RouteGISError):
        service.discover_vworld_layers("key")
