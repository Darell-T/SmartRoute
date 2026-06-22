"""Switch-narration endpoint: canned phrase, cached TTS, strict validation."""

import unittest
from unittest.mock import patch

from fastapi import HTTPException

from app.routers import switch_narration


class SwitchNarrationTests(unittest.IsolatedAsyncioTestCase):
    def setUp(self):
        switch_narration._audio_cache.clear()

    async def _call(self, route_id):
        payload = switch_narration.SwitchNarrationRequest(route_id=route_id)
        response = await switch_narration.switch_narration(payload)
        import json

        return json.loads(response.body)

    async def test_returns_canned_template_with_line(self):
        with patch.object(switch_narration, "generate_speech", return_value=b"mp3"):
            body = await self._call("e")
        self.assertIn("E", body["text"])
        self.assertTrue(any(body["text"] == t.format(line="E") for t in switch_narration._TEMPLATES))
        self.assertTrue(body["audio"])

    async def test_second_call_hits_cache_without_new_tts(self):
        with patch.object(switch_narration, "generate_speech", return_value=b"mp3") as tts:
            first = await self._call("M15-SBS")
            second = await self._call("M15-SBS")
        self.assertEqual(first, second)
        self.assertEqual(tts.call_count, 1)

    async def test_invalid_route_id_rejected(self):
        for bad in ("<script>", "", "THE QUICK BROWN", "a" * 11):
            with self.assertRaises(HTTPException) as ctx:
                await self._call(bad)
            self.assertEqual(ctx.exception.status_code, 422)

    async def test_tts_failure_returns_text_only(self):
        with patch.object(
            switch_narration, "generate_speech", side_effect=RuntimeError("no tts")
        ):
            body = await self._call("Q")
        self.assertTrue(body["text"])
        self.assertEqual(body["audio"], "")


if __name__ == "__main__":
    unittest.main()
