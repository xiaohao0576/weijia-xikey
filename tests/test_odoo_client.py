import httpx
import pytest

from odoo_client import OdooClient, OdooError


def test_json2_request_uses_named_arguments_and_bearer(monkeypatch):
    seen = {}

    def post(url, **kwargs):
        seen.update(url=url, **kwargs)
        return httpx.Response(200, json=[7], request=httpx.Request("POST", url))

    monkeypatch.setattr(httpx, "post", post)
    client = OdooClient("https://example.invalid/", "company", "secret")
    assert client.call("res.partner", "create", vals_list=[{"name": "Shop"}]) == [7]
    assert seen["url"] == "https://example.invalid/json/2/res.partner/create"
    assert seen["headers"]["Authorization"] == "bearer secret"
    assert seen["headers"]["X-Odoo-Database"] == "company"
    assert seen["json"] == {"vals_list": [{"name": "Shop"}]}


def test_network_timeout_is_uncertain(monkeypatch):
    def post(url, **kwargs):
        raise httpx.ReadTimeout("response lost")

    monkeypatch.setattr(httpx, "post", post)
    with pytest.raises(OdooError) as error:
        OdooClient("https://example.invalid", "company", "secret").call("res.partner", "create", vals_list=[])
    assert error.value.uncertain


@pytest.mark.parametrize(("status", "uncertain"), [(403, False), (500, True)])
def test_http_error_classification(monkeypatch, status, uncertain):
    def post(url, **kwargs):
        return httpx.Response(status, request=httpx.Request("POST", url))

    monkeypatch.setattr(httpx, "post", post)
    with pytest.raises(OdooError) as error:
        OdooClient("https://example.invalid", "company", "secret").call("res.partner", "create", vals_list=[])
    assert error.value.uncertain is uncertain
