import json

import httpx
from cloudflare import Cloudflare

from cfdns.cf_client import CloudflareClient


def _ok(result, **extra):
    return {"success": True, "errors": [], "messages": [], "result": result, **extra}


def test_update_record_preserves_proxied() -> None:
    """Regression: an edit without `proxied` makes Cloudflare turn the proxy off."""
    sent: list[dict] = []

    def handler(req: httpx.Request) -> httpx.Response:
        if req.method == "GET":
            if "page=2" in str(req.url):
                return httpx.Response(200, json=_ok([], result_info={}))
            rec = {"id": "a", "name": "www.kulimi.tw", "type": "A", "content": "1.1.1.1", "proxied": True, "ttl": 1}
            return httpx.Response(200, json=_ok([rec], result_info={"page": 1, "per_page": 100, "count": 1, "total_count": 1, "total_pages": 1}))
        body = json.loads(req.content)
        sent.append(body)
        return httpx.Response(200, json=_ok({"id": "a", **body}))

    client = CloudflareClient("a@b.c", "key", "zone")
    client._cf = Cloudflare(api_email="a@b.c", api_key="key", http_client=httpx.Client(transport=httpx.MockTransport(handler)))

    (rec,) = client.list_records(type="A")
    assert rec.proxied is True
    updated = client.update_record(rec, content="2.2.2.2")

    assert sent == [{"content": "2.2.2.2", "name": "www.kulimi.tw", "type": "A", "proxied": True, "ttl": 1}]
    assert updated.proxied is True
