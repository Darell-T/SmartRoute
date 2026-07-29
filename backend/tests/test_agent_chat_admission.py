from types import SimpleNamespace
import unittest
from unittest.mock import AsyncMock, Mock, patch

from fastapi import HTTPException

from app.routers import agent_chat
from app.services import admission


PRINCIPAL = "v1.test-principal-opaque-123456"


def _request(principal: str | None):
    return SimpleNamespace(
        headers={} if principal is None else {"X-SmartRoute-Principal": principal},
        app=SimpleNamespace(state=SimpleNamespace(gtfs=None)),
    )


class AgentChatAdmissionTests(unittest.IsolatedAsyncioTestCase):
    async def test_missing_principal_rejects_before_session_mutation(self):
        payload = agent_chat.AgentChatRequest(message="hello")
        with patch.object(agent_chat, "AGENT_ALLOW_MEMORY_SESSIONS", True), patch.object(
            agent_chat.session_module, "new_session", Mock()
        ) as new_session:
            with self.assertRaises(HTTPException) as error:
                await agent_chat.agent_chat(_request(None), payload)
        self.assertEqual(error.exception.status_code, 403)
        new_session.assert_not_called()

    async def test_admission_denial_rejects_before_session_mutation(self):
        payload = agent_chat.AgentChatRequest(message="hello")
        with patch.object(agent_chat, "AGENT_ALLOW_MEMORY_SESSIONS", True), patch.object(
            agent_chat.admission, "acquire", new_callable=AsyncMock, side_effect=admission.AdmissionDenied(503, "busy", 1)
        ), patch.object(agent_chat.session_module, "new_session", Mock()) as new_session:
            with self.assertRaises(HTTPException) as error:
                await agent_chat.agent_chat(_request(PRINCIPAL), payload)
        self.assertEqual(error.exception.status_code, 503)
        new_session.assert_not_called()

    async def test_post_acquire_setup_exception_releases_once(self):
        lease = admission.AdmissionLease(PRINCIPAL, "chat", "lease")
        payload = agent_chat.AgentChatRequest(message="hello")
        with patch.object(agent_chat, "AGENT_ALLOW_MEMORY_SESSIONS", True), patch.object(
            agent_chat.admission, "acquire", AsyncMock(return_value=lease)
        ), patch.object(agent_chat.admission, "release", AsyncMock()) as release, patch.object(
            agent_chat.session_module, "new_session", Mock(return_value=("session", {}))
        ), patch.object(agent_chat.session_module, "next_turn_id", Mock(side_effect=RuntimeError("setup failed"))):
            with self.assertRaises(RuntimeError):
                await agent_chat.agent_chat(_request(PRINCIPAL), payload)
        release.assert_awaited_once_with(lease)
