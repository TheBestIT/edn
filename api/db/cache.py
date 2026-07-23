from typing import Any, cast, List
from typing_extensions import Self

import os
import redis

from api.misc.logger import Logger
from api.db.models import APIToken, APIUsage, RateLimitResponse

# KEYS[1] should be the token
# ARGV[1] should be the default bucket size
# ARGV[2] should be the cost of the operation
# ARGV[3] should be the refill rate (per second)
# ARGV[4] should be the expire rate (per second)

# had to get a lua crash course just for this btw...

atomic = """
-- Get Current token bucket status
local bucket = redis.call('HMGET', KEYS[1], 'tokens', 'ts')

local tokens = tonumber(bucket[1])
local ts     = tonumber(bucket[2])
local time   = redis.call('TIME')
local now    = tonumber(time[1]) + (tonumber(time[2]) / 1000000)

local capacity = ARGV[1]

-- Check if bucket expired and refill if it did
if tokens == nil or ts == nil then
    tokens = capacity
    ts     = now
    redis.call('HSET', KEYS[1], 'tokens', tokens, 'ts', ts)
end

local cost        = ARGV[2]
local refill_rate = ARGV[3]
local expire_rate = ARGV[4]

-- Calculate refilling
local time_diff = now - ts
tokens = math.min(capacity, tokens + (time_diff * refill_rate))

-- Subtract cost
local allowed = true
local retry_after = 0
tokens = tokens - cost
if tokens <= 0 then
    allowed = false
    -- Compute Retry-After Header
    retry_after = (tokens*-1)/refill_rate
    tokens = tokens + cost
end

redis.call('HSET', KEYS[1], 'tokens', tokens, 'ts', now)
redis.call('EXPIRE', KEYS[1], math.ceil(expire_rate))

return {allowed, tostring(tokens), tostring(retry_after)}
"""

class Cache:

    instance = None

    def __new__(cls) -> Self:
        if cls.instance is None:
            cls.instance = super().__new__(cls)
        return cls.instance

    def __init__(self) -> None:
        if getattr(self, "_initialized", False):
            return
        self._initialized = True

        self.logger = Logger("cache.master")

        dockerized = os.environ.get("DOCKERIZED", "0") == "1"
        uri = os.environ.get("EDN_REDIS_URL_DOCKER") if dockerized else os.environ.get("EDN_REDIS_URL")
        if uri is None: self.logger.fail("No Redis URI set. Define EDN_REDIS_URL in your .env")

        self.client = redis.Redis.from_url(
            uri,
            decode_responses=True,
            socket_connect_timeout=3,
            socket_timeout=3,
            health_check_interval=30,
        )

        self.check_rate = self.client.register_script(atomic)

    def validate_request(self, token: APIToken, cost: int) -> RateLimitResponse:
        allowed, tokens, retry_after = cast(
            "list[Any]",
            self.check_rate(
                keys=[f"ratelimit:{token.token}"],
                args=[
                    token.usage.bucket_size,
                    cost,
                    token.usage.bucket_refill_rate,
                    token.usage.bucket_expire_rate,
                ],
            ),
        )

        self.logger.log(f"({token.token}) {allowed=} ({cost=}, {tokens=}); retry after {float(retry_after):.2f}s")
        return RateLimitResponse(allowed=allowed, tokens=float(tokens), retry_after=float(retry_after), usage_policy=token.usage)

    def build_headers(self, rate_limit_query: RateLimitResponse) -> dict:
        headers = {
            "X-RateLimit-Limit": rate_limit_query.usage_policy.bucket_size,
            "X-RateLimit-Remaining": rate_limit_query.tokens,
            "X-RateLimit-Reset": (rate_limit_query.usage_policy.bucket_size - rate_limit_query.tokens) / rate_limit_query.usage_policy.bucket_refill_rate
        }
        if not rate_limit_query.allowed:
            headers["Retry-After"] = rate_limit_query.retry_after
        return headers

    def ping(self) -> bool:
        """Report whether Redis is reachable (for health endpoints)."""
        try:
            return bool(self.client.ping())
        except redis.RedisError as error:
            self.logger.log(f"Redis ping failed: {error}")
            return False
