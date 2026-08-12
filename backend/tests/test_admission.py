import unittest
from unittest.mock import patch

from app.services import admission


class _SharedRedis:
    """Small multi-instance stand-in for the atomic Redis primitives we use."""

    def __init__(self):
        self.values, self.zsets = {}, {}

    def eval(self, _script, key_count, *args):
        keys = args[:key_count]
        if _script == admission._RELEASE:
            for key in keys: self.zsets.get(key, {}).pop(args[key_count], None)
            return 1
        if _script == admission._REFRESH:
            now, expiry, token = args[key_count:]
            for key in keys: self.zsets.setdefault(key, {}).update({k: v for k, v in self.zsets.get(key, {}).items() if v > now})
            if any(token not in self.zsets.get(key, {}) for key in keys): return 0
            for key in keys: self.zsets[key][token] = expiry
            return 1
        if _script != admission._ACQUIRE: raise AssertionError("unexpected script")
        rp, rg, principal_set, global_set = keys
        pl, gl, pcl, gcl, _window, now, expiry, token = args[key_count:]
        for key in (principal_set, global_set): self.zsets[key] = {k: v for k, v in self.zsets.get(key, {}).items() if v > now}
        if self.values.get(rp, 0) >= pl: return [1, 1]
        if self.values.get(rg, 0) >= gl: return [2, 1]
        if len(self.zsets[principal_set]) >= pcl: return [3, 1]
        if len(self.zsets[global_set]) >= gcl: return [4, 1]
        self.values[rp] = self.values.get(rp, 0) + 1; self.values[rg] = self.values.get(rg, 0) + 1
        self.zsets[principal_set][token] = expiry; self.zsets[global_set][token] = expiry
        return [0, 0]

    def decr(self, key):
        self.values[key] = max(0, self.values.get(key, 0) - 1)

    def set(self, key, _value, nx=False, ex=None):
        if nx and key in self.values:
            return False
        self.values[key] = 1
        return True


class AdmissionTests(unittest.IsolatedAsyncioTestCase):
    async def test_websocket_lease_uses_longer_orphan_recovery_ttl(self):
        admission._memory_leases.clear()
        admission._memory_requests.clear()
        principal = "v1.principal-one-123456"
        with patch.object(admission, "_redis_client", None), patch.dict(
            "os.environ", {"REDIS_URL": ""}
        ), patch.object(
            admission.runtime, "allows_mock_modes", return_value=True
        ):
            request_lease = await admission.acquire(principal, "chat")
            socket_lease = await admission.acquire(principal, "ws")

        self.assertEqual(request_lease.ttl_s, admission.LEASE_TTL_S)
        self.assertEqual(socket_lease.ttl_s, admission.WEBSOCKET_LEASE_TTL_S)
        self.assertGreater(socket_lease.ttl_s, request_lease.ttl_s)

    async def test_saturated_socket_pool_does_not_block_normal_requests(self):
        admission._memory_leases.clear()
        admission._memory_requests.clear()
        principal = "v1.principal-one-123456"
        with patch.object(admission, "_redis_client", None), patch.dict(
            "os.environ", {"REDIS_URL": ""}
        ), patch.object(
            admission.runtime, "allows_mock_modes", return_value=True
        ), patch.object(admission, "WEBSOCKET_CONCURRENT_PER_PRINCIPAL", 2):
            socket_leases = [await admission.acquire(principal, "ws") for _ in range(2)]
            with self.assertRaises(admission.AdmissionDenied):
                await admission.acquire(principal, "ws")
            request_leases = [
                await admission.acquire(principal, kind)
                for kind in ("trip", "chat")
            ]
            for lease in socket_leases + request_leases:
                await admission.release(lease)

    async def test_orphaned_lease_prunes_while_another_refreshes(self):
        admission._memory_leases.clear()
        admission._memory_requests.clear()
        with patch.object(admission, "_redis_client", None), patch.dict("os.environ", {"REDIS_URL": ""}), patch.object(
            admission.runtime, "allows_mock_modes", return_value=True
        ), patch.object(admission, "CONCURRENT_PER_PRINCIPAL", 2):
            first = await admission.acquire("v1.principal-one-123456", "trip")
            second = await admission.acquire("v1.principal-one-123456", "chat")
            admission._memory_leases[first.token] = (
                first.principal,
                admission._admission_pool(first.kind),
                0,
            )
            self.assertTrue(await admission.refresh(second))
            third = await admission.acquire("v1.principal-one-123456", "ws")
            await admission.release(third)
            await admission.release(third)
            self.assertFalse(await admission.refresh(first))

    async def test_shared_redis_enforces_global_and_releases_concurrency(self):
        shared = _SharedRedis()
        with patch.object(admission, "_redis_client", shared), patch.object(
            admission, "REQUESTS_GLOBAL", 2
        ), patch.object(admission, "CONCURRENT_GLOBAL", 1):
            first = await admission.acquire("v1.principal-one-123456", "trip")
            with self.assertRaises(admission.AdmissionDenied) as denied:
                await admission.acquire("v1.principal-two-123456", "chat")
            self.assertEqual(denied.exception.status_code, 503)
            await admission.release(first)
            second = await admission.acquire("v1.principal-two-123456", "chat")
            await admission.release(second)

    async def test_shared_redis_rate_limits_are_separate_by_pool(self):
        shared = _SharedRedis()
        principal = "v1.principal-one-123456"
        with patch.object(admission, "_redis_client", shared), patch.object(
            admission, "REQUESTS_PER_PRINCIPAL", 1
        ), patch.object(admission, "REQUESTS_GLOBAL", 2), patch.object(
            admission, "WEBSOCKET_CONNECTIONS_PER_PRINCIPAL", 1
        ):
            await admission.acquire(principal, "trip")
            with self.assertRaises(admission.AdmissionDenied):
                await admission.acquire(principal, "chat")
            await admission.acquire(principal, "ws")
            with self.assertRaises(admission.AdmissionDenied):
                await admission.acquire(principal, "ws")

    async def test_redis_loss_fails_closed_outside_local_test(self):
        with patch.object(admission, "_redis_client", None), patch.dict("os.environ", {"REDIS_URL": ""}), patch.object(
            admission.runtime, "allows_mock_modes", return_value=False
        ):
            with self.assertRaises(admission.AdmissionDenied) as denied:
                await admission.acquire("v1.principal-one-123456", "trip")
            self.assertEqual(denied.exception.status_code, 503)

    async def test_ticket_nonce_is_single_use(self):
        shared = _SharedRedis()
        with patch.object(admission, "_redis_client", shared):
            self.assertEqual(await admission.consume_nonce("random-ticket-nonce", 30), "consumed")
            self.assertEqual(await admission.consume_nonce("random-ticket-nonce", 30), "replay")
