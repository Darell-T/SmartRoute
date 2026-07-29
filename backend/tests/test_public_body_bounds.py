import unittest
from types import SimpleNamespace

from app import main


class _Request:
    def __init__(self, chunks, content_length=None):
        self.method = "POST"
        self.url = SimpleNamespace(path="/api/trip")
        self.headers = {} if content_length is None else {"content-length": str(content_length)}
        self._chunks = chunks

    async def stream(self):
        for chunk in self._chunks:
            yield chunk


class PublicBodyBoundsTests(unittest.IsolatedAsyncioTestCase):
    async def test_chunked_body_at_limit_replays_exact_bytes_downstream(self):
        body = b'{"payload":"' + b"x" * (main.MAX_PUBLIC_BODY_BYTES - 14) + b'"}'
        request = _Request([body[:97], body[97:]])

        async def downstream(received):
            self.assertEqual(received._body, body)
            return "ok"

        self.assertEqual(await main.reject_oversize_public_json(request, downstream), "ok")

    async def test_chunked_utf8_body_over_limit_is_413_without_downstream(self):
        body = ("\u00e9" * (main.MAX_PUBLIC_BODY_BYTES // 2 + 1)).encode("utf-8")
        request = _Request([body[:31], body[31:]])

        async def downstream(_received):
            raise AssertionError("oversize body must not reach downstream")

        response = await main.reject_oversize_public_json(request, downstream)
        self.assertEqual(response.status_code, 413)

    async def test_content_length_fast_rejection(self):
        request = _Request([], main.MAX_PUBLIC_BODY_BYTES + 1)

        async def downstream(_received):
            raise AssertionError("declared oversize body must not reach downstream")

        response = await main.reject_oversize_public_json(request, downstream)
        self.assertEqual(response.status_code, 413)
