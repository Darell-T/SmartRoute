"""Thinking endpoint: cached route-start phrase audio."""

import json
import sys
import types
import unittest
from unittest.mock import patch

elevenlabs_module = types.ModuleType("elevenlabs")
elevenlabs_client_module = types.ModuleType("elevenlabs.client")


class _FakeElevenLabs:
    def __init__(self, *args, **kwargs):
        self.text_to_speech = types.SimpleNamespace(stream=lambda **_: [b""])


elevenlabs_client_module.ElevenLabs = _FakeElevenLabs
sys.modules.setdefault("elevenlabs", elevenlabs_module)
sys.modules.setdefault("elevenlabs.client", elevenlabs_client_module)

fastapi_module = types.ModuleType("fastapi")
fastapi_responses_module = types.ModuleType("fastapi.responses")


class _FakeAPIRouter:
    def post(self, *_args, **_kwargs):
        return lambda fn: fn


class _FakeJSONResponse:
    def __init__(self, content=None, status_code=200, headers=None):
        self.body = json.dumps(content or {}).encode("utf-8")
        self.status_code = status_code
        self.headers = headers or {}


fastapi_module.APIRouter = _FakeAPIRouter
fastapi_responses_module.JSONResponse = _FakeJSONResponse
sys.modules.setdefault("fastapi", fastapi_module)
sys.modules.setdefault("fastapi.responses", fastapi_responses_module)

from app.routers import thinking


class ThinkingAudioTests(unittest.IsolatedAsyncioTestCase):
    def setUp(self):
        thinking._audio_cache.clear()
        thinking._phrase_queue.clear()

    async def _call(self):
        response = await thinking.thinking_audio()
        return json.loads(response.body)

    async def test_reuses_cached_audio_for_later_route_start_requests(self):
        phrases = iter(["Scanning the first route.", "Scanning a second route."])

        with (
            patch.object(thinking, "_next_phrase", side_effect=lambda: next(phrases)),
            patch.object(thinking, "generate_speech", return_value=b"mp3") as tts,
        ):
            first = await self._call()
            second = await self._call()

        self.assertEqual(first, second)
        self.assertEqual(tts.call_count, 1)


if __name__ == "__main__":
    unittest.main()
