"""Focused tests for the atomic and batched cache primitives."""

import asyncio
import json
import threading
import time
import unittest
from unittest.mock import patch

import redis

from app.services.agent import candidate_store
from app.services import cache


class FakeRedis:
    """Minimal redis.Redis stand-in recording calls for the atomic paths."""

    def __init__(self):
        self.data = {}
        self.expires_at = {}
        self.set_calls = []
        self.get_calls = []
        self.mget_calls = []
        self.eval_calls = []

    def set(self, name, value, nx=False, ex=None):
        self.set_calls.append((name, value, nx, ex))
        if nx and self._unexpired(name):
            return None
        self.data[name] = value
        self.expires_at[name] = time.monotonic() + ex
        return True

    def setex(self, name, ttl_seconds, value):
        self.data[name] = value
        self.expires_at[name] = time.monotonic() + ttl_seconds

    def get(self, name):
        self.get_calls.append(name)
        if not self._unexpired(name):
            return None
        return self.data.get(name)

    def mget(self, names):
        self.mget_calls.append(list(names))
        return [
            self.data.get(name) if self._unexpired(name) else None
            for name in names
        ]

    def eval(self, script, numkeys, key, expected):
        self.eval_calls.append((script, numkeys, key, expected))
        if not self._unexpired(key):
            return 0
        if "pttl" in script:
            self.data[key] = expected
            return 1
        if self.data.get(key) == expected:
            del self.data[key]
            return 1
        return 0

    def _unexpired(self, name):
        return name in self.data and time.monotonic() < self.expires_at.get(name, 0)


class _CandidatePipeline:
    def __init__(self, client):
        self.client = client
        self.queued_set = None

    def __enter__(self):
        return self

    def __exit__(self, _exc_type, _exc, _traceback):
        return False

    def watch(self, _key):
        if self.client.watch_failures:
            self.client.watch_failures -= 1
            raise redis.exceptions.WatchError("candidate changed")
        if self.client.pipeline_error is not None:
            raise self.client.pipeline_error

    def get(self, key):
        return self.client.get(key)

    def multi(self):
        return None

    def setex(self, key, ttl_seconds, value):
        self.queued_set = (key, ttl_seconds, value)

    def execute(self):
        if self.queued_set is not None:
            self.client.setex(*self.queued_set)
        return [True]


class _CandidateRedis(FakeRedis):
    def __init__(self, *, watch_failures=0, pipeline_error=None):
        super().__init__()
        self.watch_failures = watch_failures
        self.pipeline_error = pipeline_error

    def pipeline(self):
        return _CandidatePipeline(self)


class _RejectingRedis:
    def get(self, _key):
        raise redis.exceptions.ResponseError("sensitive provider details")

    def setex(self, _key, _ttl, _value):
        raise redis.exceptions.ResponseError("sensitive provider details")


class _WriteFailsReadMissRedis:
    def get(self, _key):
        return None

    def mget(self, keys):
        return [None for _key in keys]

    def setex(self, _key, _ttl, _value):
        raise redis.exceptions.ResponseError("quota exceeded")


class OptionalProviderCacheTests(unittest.TestCase):
    def setUp(self):
        cache._mem.clear()
        cache._last_fail_open_log = 0.0

    def tearDown(self):
        cache._mem.clear()

    def test_fail_open_cache_uses_process_memory_when_redis_rejects_requests(self):
        with patch.object(cache, "redis_client", _RejectingRedis()):
            cache.cache_set("mta:test", b"feed", 30, fail_open=True)
            self.assertEqual(cache.cache_get("mta:test", fail_open=True), b"feed")

    def test_default_cache_behavior_remains_strict(self):
        with patch.object(cache, "redis_client", _RejectingRedis()):
            with self.assertRaises(redis.exceptions.ResponseError):
                cache.cache_get("session:test")
            with self.assertRaises(redis.exceptions.ResponseError):
                cache.cache_set("session:test", b"state", 30)

    def test_fail_open_single_read_uses_mirror_after_write_failure_and_redis_miss(self):
        with patch.object(cache, "redis_client", _WriteFailsReadMissRedis()):
            cache.cache_set("optional:test", "value", 30, fail_open=True)

            self.assertEqual(
                cache.cache_get("optional:test", fail_open=True),
                "value",
            )
            self.assertIsNone(cache.cache_get("optional:test"))

    def test_fail_open_batch_read_uses_mirror_after_write_failure_and_redis_miss(self):
        with patch.object(cache, "redis_client", _WriteFailsReadMissRedis()):
            cache.cache_set("optional:batch", "value", 30, fail_open=True)

            result = cache.cache_get_many(["optional:batch"], fail_open=True)
            strict_result = cache.cache_get_many(["optional:batch"])

        self.assertEqual(result["optional:batch"], "value")
        self.assertIsNone(strict_result["optional:batch"])


class CacheAddInMemoryTests(unittest.TestCase):
    def setUp(self):
        cache._mem.clear()

    def test_only_one_concurrent_caller_acquires(self):
        callers = 8
        barrier = threading.Barrier(callers)
        results = []
        results_lock = threading.Lock()

        def attempt(index):
            barrier.wait()
            won = cache.cache_add("lock:single-winner", f"owner-{index}", 60)
            with results_lock:
                results.append(won)

        threads = [threading.Thread(target=attempt, args=(i,)) for i in range(callers)]
        for thread in threads:
            thread.start()
        for thread in threads:
            thread.join()

        self.assertEqual(results.count(True), 1)
        self.assertEqual(results.count(False), callers - 1)
        self.assertIn(cache.cache_get("lock:single-winner"), [f"owner-{i}" for i in range(callers)])

    def test_expired_entry_can_be_reacquired(self):
        key = "lock:expired"
        cache.cache_set(key, "stale-owner", 60)
        _value, _expiry = cache._mem[key]
        cache._mem[key] = (_value, time.monotonic() - 1)

        self.assertTrue(cache.cache_add(key, "fresh-owner", 60))
        self.assertEqual(cache.cache_get(key), "fresh-owner")


class CacheDeleteIfValueInMemoryTests(unittest.TestCase):
    def setUp(self):
        cache._mem.clear()

    def test_wrong_owner_cannot_release(self):
        key = "lock:owner"
        cache.cache_set(key, "owner-a", 60)

        self.assertFalse(cache.cache_delete_if_value(key, "owner-b"))
        self.assertEqual(cache.cache_get(key), "owner-a")

    def test_right_owner_releases(self):
        key = "lock:owner"
        cache.cache_set(key, "owner-a", 60)

        self.assertTrue(cache.cache_delete_if_value(key, "owner-a"))
        self.assertIsNone(cache.cache_get(key))

    def test_expired_entry_counts_as_absent(self):
        key = "lock:expired-delete"
        cache.cache_set(key, "owner-a", 60)
        _value, _expiry = cache._mem[key]
        cache._mem[key] = (_value, time.monotonic() - 1)

        self.assertFalse(cache.cache_delete_if_value(key, "owner-a"))

    def test_string_owner_releases_bytes_value(self):
        key = "lock:bytes-owner"
        cache.cache_set(key, b"owner-a", 60)

        self.assertTrue(cache.cache_delete_if_value(key, "owner-a"))
        self.assertIsNone(cache.cache_get(key))


class CachePreserveTtlInMemoryTests(unittest.TestCase):
    def setUp(self):
        cache._mem.clear()

    def test_replaces_value_without_extending_expiry(self):
        key = "session:preserve"
        cache.cache_set(key, "before", 60)
        original_expiry = time.monotonic() + 10
        _value, _expiry = cache._mem[key]
        cache._mem[key] = (_value, original_expiry)

        self.assertTrue(cache.cache_set_preserve_ttl(key, "after"))
        self.assertEqual(cache._mem[key], ("after", original_expiry))

    def test_missing_or_expired_key_is_not_created(self):
        key = "session:expired"
        self.assertFalse(cache.cache_set_preserve_ttl(key, "after"))
        cache.cache_set(key, "before", 60)
        _value, _expiry = cache._mem[key]
        cache._mem[key] = (_value, time.monotonic() - 1)

        self.assertFalse(cache.cache_set_preserve_ttl(key, "after"))
        self.assertNotIn(key, cache._mem)

class CacheAtomicRedisPathTests(unittest.TestCase):
    def setUp(self):
        cache._mem.clear()
        self.fake = FakeRedis()
        self.original_client = cache.redis_client
        cache.redis_client = self.fake

    def tearDown(self):
        cache.redis_client = self.original_client

    def test_redis_add_uses_single_atomic_nx_set_with_expiry(self):
        key = "lock:redis-add"

        self.assertTrue(cache.cache_add(key, "owner-1", 60))
        self.assertFalse(cache.cache_add(key, "owner-2", 60))
        self.assertEqual(self.fake.data[key], "owner-1")

        self.assertEqual(len(self.fake.set_calls), 2)
        for name, _value, nx, ex in self.fake.set_calls:
            self.assertEqual(name, key)
            self.assertIs(nx, True)
            self.assertEqual(ex, 60)
        # SET NX is one call: no separate get/exists probe.
        self.assertEqual(self.fake.get_calls, [])

    def test_redis_release_uses_atomic_compare_delete(self):
        key = "lock:redis-release"
        self.fake.data[key] = b"token-1"  # Redis stores and returns bytes.
        self.fake.expires_at[key] = time.monotonic() + 60

        # A different owner (string form) must not release the lock.
        self.assertFalse(cache.cache_delete_if_value(key, "token-2"))
        self.assertEqual(self.fake.data[key], b"token-1")

        # The owning string token releases the byte-stored value.
        self.assertTrue(cache.cache_delete_if_value(key, "token-1"))
        self.assertNotIn(key, self.fake.data)

        self.assertEqual(len(self.fake.eval_calls), 2)
        for script, numkeys, name, expected in self.fake.eval_calls:
            self.assertIn("redis.call", script)
            self.assertEqual(numkeys, 1)
            self.assertEqual(name, key)
            self.assertIsInstance(expected, bytes)

    def test_redis_preserve_ttl_uses_atomic_replace(self):
        key = "session:redis-preserve"
        self.fake.data[key] = "before"
        original_expiry = time.monotonic() + 60
        self.fake.expires_at[key] = original_expiry

        self.assertTrue(cache.cache_set_preserve_ttl(key, "after"))
        self.assertEqual(self.fake.data[key], "after")
        self.assertEqual(self.fake.expires_at[key], original_expiry)
        self.assertEqual(len(self.fake.eval_calls), 1)
        script, numkeys, name, value = self.fake.eval_calls[0]
        self.assertIn("pttl", script)
        self.assertEqual(numkeys, 1)
        self.assertEqual(name, key)
        self.assertEqual(value, "after")
        self.assertEqual(self.fake.get_calls, [])
        # One atomic compare-delete call: no GET followed by DELETE.
        self.assertEqual(self.fake.get_calls, [])


class CandidateStoreMarkPresentedAtomicTests(unittest.TestCase):
    """The one-time presentation reservation stays atomic under concurrency."""

    def setUp(self):
        cache._mem.clear()
        self._original_client = cache.redis_client
        cache.redis_client = None

    def tearDown(self):
        cache.redis_client = self._original_client
        cache._mem.clear()

    def test_concurrent_mark_presented_yields_at_most_one_success(self):
        set_id = candidate_store.store_candidate_set(
            session_id="sess-race",
            payload={
                "parsed_routes": [[{"type": "SUBWAY", "route_id": "Q"}]],
                "candidates": [{"candidate_id": "cd_race", "index": 0}],
            },
        )
        callers = 8
        barrier = threading.Barrier(callers)
        results = []
        results_lock = threading.Lock()

        def attempt(_index):
            barrier.wait()
            error = candidate_store.mark_presented(
                set_id,
                "cd_race",
                session_id="sess-race",
            )
            with results_lock:
                results.append(error)

        threads = [
            threading.Thread(target=attempt, args=(index,)) for index in range(callers)
        ]
        for thread in threads:
            thread.start()
        for thread in threads:
            thread.join()

        self.assertEqual(results.count(None), 1)
        self.assertEqual(len(results) - results.count(None), callers - 1)
        record = candidate_store.load_candidate_set(set_id, session_id="sess-race")
        self.assertTrue(record["presented"])
        self.assertEqual(record["selected_candidate_id"], "cd_race")
        for error in results:
            if error:
                self.assertIn("already presented", error)

    def test_mark_presented_reserves_once_and_rejects_duplicates(self):
        set_id = candidate_store.store_candidate_set(
            session_id="sess-dup",
            payload={
                "parsed_routes": [[{"type": "SUBWAY", "route_id": "Q"}]],
                "candidates": [{"candidate_id": "cd_one", "index": 0}],
            },
        )
        self.assertIsNone(
            candidate_store.mark_presented(set_id, "cd_one", session_id="sess-dup")
        )
        duplicate = candidate_store.mark_presented(
            set_id,
            "cd_one",
            session_id="sess-dup",
        )
        self.assertIn("already presented", duplicate or "")
        # A consumed set rejects every later reservation, even for a
        # different candidate id: presentation is one-time per set.
        other = candidate_store.mark_presented(
            set_id,
            "cd_nope",
            session_id="sess-dup",
        )
        self.assertIn("already presented", other or "")
        record = candidate_store.load_candidate_set(set_id, session_id="sess-dup")
        self.assertEqual(record["selected_candidate_id"], "cd_one")


class CandidateStoreMarkPresentedRedisTests(unittest.TestCase):
    """The Redis transaction preserves the same one-time reservation contract."""

    def setUp(self):
        cache._mem.clear()
        self.original_client = cache.redis_client
        self.fake = _CandidateRedis()
        cache.redis_client = self.fake

    def tearDown(self):
        cache.redis_client = self.original_client
        cache._mem.clear()

    def _store(self):
        return candidate_store.store_candidate_set(
            session_id="sess-redis",
            payload={"candidates": [{"candidate_id": "cd_one"}]},
            ttl_seconds=60,
        )

    def _rewrite_record(self, set_id, **updates):
        key = candidate_store._key(set_id)
        record = json.loads(self.fake.data[key])
        record.update(updates)
        self.fake.data[key] = json.dumps(record)

    def test_redis_transaction_reserves_once(self):
        set_id = self._store()

        self.assertIsNone(
            candidate_store.mark_presented(
                set_id,
                "cd_one",
                session_id="sess-redis",
            )
        )
        duplicate = candidate_store.mark_presented(
            set_id,
            "cd_one",
            session_id="sess-redis",
        )

        self.assertIn("already presented", duplicate or "")
        record = candidate_store.load_candidate_set(
            set_id,
            session_id="sess-redis",
        )
        self.assertEqual(record["selected_candidate_id"], "cd_one")

    def test_redis_transaction_retries_watch_conflicts(self):
        self.fake.watch_failures = 2
        set_id = self._store()

        self.assertIsNone(
            candidate_store.mark_presented(
                set_id,
                "cd_one",
                session_id="sess-redis",
            )
        )

    def test_redis_transaction_reports_repeated_watch_conflicts(self):
        self.fake.watch_failures = 3
        set_id = self._store()

        error = candidate_store.mark_presented(
            set_id,
            "cd_one",
            session_id="sess-redis",
        )

        self.assertIn("candidate set changed", error or "")

    def test_redis_miss_uses_the_process_memory_mirror(self):
        set_id = self._store()
        self.fake.data.pop(candidate_store._key(set_id))

        self.assertIsNone(
            candidate_store.mark_presented(
                set_id,
                "cd_one",
                session_id="sess-redis",
            )
        )

    def test_redis_errors_use_the_process_memory_mirror(self):
        set_id = self._store()
        self.fake.pipeline_error = redis.exceptions.ResponseError("quota exceeded")

        self.assertIsNone(
            candidate_store.mark_presented(
                set_id,
                "cd_one",
                session_id="sess-redis",
            )
        )

    def test_redis_transaction_rejects_wrong_owner_and_unknown_candidate(self):
        set_id = self._store()

        owner_error = candidate_store.mark_presented(
            set_id,
            "cd_one",
            session_id="another-session",
        )
        candidate_error = candidate_store.mark_presented(
            set_id,
            "cd_unknown",
            session_id="sess-redis",
        )

        self.assertIn("not owned", owner_error or "")
        self.assertIn("candidate id is unknown", candidate_error or "")

    def test_redis_transaction_rejects_expired_or_invalid_records(self):
        expired_set_id = self._store()
        self._rewrite_record(expired_set_id, expires_at=time.time() - 1)
        expired_error = candidate_store.mark_presented(
            expired_set_id,
            "cd_one",
            session_id="sess-redis",
        )

        invalid_set_id = self._store()
        self._rewrite_record(invalid_set_id, expires_at="invalid")
        invalid_error = candidate_store.mark_presented(
            invalid_set_id,
            "cd_one",
            session_id="sess-redis",
        )

        self.assertIn("expired", expired_error or "")
        self.assertIn("expiry is invalid", invalid_error or "")

    def test_unexpected_pipeline_failure_is_a_bounded_store_error(self):
        set_id = self._store()
        self.fake.pipeline_error = RuntimeError("broken fake")

        error = candidate_store.mark_presented(
            set_id,
            "cd_one",
            session_id="sess-redis",
        )

        self.assertEqual(error, "candidate presentation store unavailable")


class CacheGetManyInMemoryTests(unittest.TestCase):
    def setUp(self):
        cache._mem.clear()

    def test_batch_read_deduplicates_and_keeps_order(self):
        cache.cache_set("k1", "v1", 60)
        cache.cache_set("k2", b"v2", 60)

        result = cache.cache_get_many(["k1", "k2", "k3", "k1"])

        self.assertEqual(list(result), ["k1", "k2", "k3"])
        self.assertEqual(result["k1"], "v1")
        self.assertEqual(result["k2"], b"v2")
        self.assertIsNone(result["k3"])

    def test_empty_batch_read_is_a_noop(self):
        self.assertEqual(cache.cache_get_many([]), {})


class CacheGetManyRedisPathTests(unittest.TestCase):
    def setUp(self):
        cache._mem.clear()
        self.fake = FakeRedis()
        self.original_client = cache.redis_client
        cache.redis_client = self.fake

    def tearDown(self):
        cache.redis_client = self.original_client

    def test_redis_batch_read_uses_one_mget(self):
        self.fake.data["a"] = b"va"
        self.fake.data["b"] = b"vb"
        self.fake.expires_at["a"] = time.monotonic() + 60
        self.fake.expires_at["b"] = time.monotonic() + 60

        result = cache.cache_get_many(["a", "b", "missing", "a"])

        self.assertEqual(len(self.fake.mget_calls), 1)
        self.assertEqual(self.fake.mget_calls[0], ["a", "b", "missing"])
        self.assertEqual(result["a"], b"va")
        self.assertEqual(result["b"], b"vb")
        self.assertIsNone(result["missing"])
        # Batch reads must not fall back to per-key GET round trips.
        self.assertEqual(self.fake.get_calls, [])

    def test_redis_batch_read_fail_open_falls_back_to_memory(self):
        self.fake.data["a"] = b"va"
        self.fake.expires_at["a"] = time.monotonic() + 60
        # A fail-open caller opts into mirrored writes, so the process-memory
        # fallback can serve the batch when the Redis read fails.
        cache.cache_set("mem", "value", 60, fail_open=True)

        def _explode(_names):
            raise cache.redis.exceptions.RedisError("down")

        with patch.object(self.fake, "mget", side_effect=_explode):
            result = cache.cache_get_many(["a", "mem"], fail_open=True)

        # Fail-open degrades the whole batch to process memory, never partial.
        self.assertIsNone(result["a"])
        self.assertEqual(result["mem"], "value")


class CacheGetManyAsyncTests(unittest.IsolatedAsyncioTestCase):
    def setUp(self):
        cache._mem.clear()
        self.original_client = cache.redis_client
        cache.redis_client = None

    def tearDown(self):
        cache.redis_client = self.original_client
        cache._mem.clear()

    async def test_async_batch_read_matches_sync(self):
        cache.cache_set("k1", "v1", 60)

        result = await cache.cache_get_many_async(["k1", "missing"])

        self.assertEqual(result["k1"], "v1")
        self.assertIsNone(result["missing"])

    async def test_async_batch_read_is_bounded(self):
        def _slow_memory_get(_key):
            time.sleep(0.2)
            return None

        with patch.object(cache, "_memory_get", side_effect=_slow_memory_get):
            with self.assertRaises(asyncio.TimeoutError):
                await cache.cache_get_many_async(["k"], timeout_s=0.01)
