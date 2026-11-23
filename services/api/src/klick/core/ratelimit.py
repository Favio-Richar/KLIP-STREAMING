"""Limitación de tasa y bloqueo progresivo sobre Redis (docs/05 §1, docs/09)."""

import hashlib
from dataclasses import dataclass

from redis.asyncio import Redis

from klick.core.errors import ApiError


@dataclass(frozen=True)
class RateLimitResult:
    allowed: bool
    retry_after: int


def hash_key(value: str) -> str:
    """Las claves de Redis nunca llevan correos ni IP en claro."""
    return hashlib.sha256(value.lower().encode()).hexdigest()[:32]


class RateLimiter:
    def __init__(self, redis: Redis, prefix: str = "rl") -> None:
        self._redis = redis
        self._prefix = prefix

    async def hit(self, scope: str, subject: str, limit: int, window_s: int) -> RateLimitResult:
        """Ventana fija: cuenta el intento y decide. Atómico (INCR + EXPIRE condicional)."""
        key = f"{self._prefix}:{scope}:{hash_key(subject)}"
        pipe = self._redis.pipeline()
        pipe.incr(key)
        pipe.ttl(key)
        count, ttl = await pipe.execute()
        if ttl < 0:
            await self._redis.expire(key, window_s)
            ttl = window_s
        return RateLimitResult(allowed=count <= limit, retry_after=max(int(ttl), 1))

    async def enforce(self, scope: str, subject: str, limit: int, window_s: int) -> None:
        result = await self.hit(scope, subject, limit, window_s)
        if not result.allowed:
            raise rate_limited(result.retry_after)

    # --- bloqueo progresivo por fallos de autenticación -------------------------------------

    async def lock_remaining(self, scope: str, subject: str) -> int:
        ttl = await self._redis.ttl(f"{self._prefix}:lock:{scope}:{hash_key(subject)}")
        return max(int(ttl), 0)

    async def register_failure(
        self, scope: str, subject: str, free_attempts: int = 5, window_s: int = 900
    ) -> int:
        """Cuenta un fallo. Superados los intentos libres bloquea 1, 2, 4... min (máx. 60).
        Devuelve los segundos de bloqueo aplicados (0 si aún no hay)."""
        sid = hash_key(subject)
        fkey = f"{self._prefix}:fail:{scope}:{sid}"
        count = int(await self._redis.incr(fkey))
        await self._redis.expire(fkey, window_s)
        if count < free_attempts:
            return 0
        lock_s = int(min(60 * 2 ** (count - free_attempts), 3600))
        await self._redis.set(f"{self._prefix}:lock:{scope}:{sid}", "1", ex=lock_s)
        return lock_s

    async def clear_failures(self, scope: str, subject: str) -> None:
        sid = hash_key(subject)
        await self._redis.delete(
            f"{self._prefix}:fail:{scope}:{sid}", f"{self._prefix}:lock:{scope}:{sid}"
        )


def rate_limited(retry_after: int) -> ApiError:
    return ApiError(
        429,
        "rate_limited",
        "Too many requests",
        "Demasiados intentos. Espera un momento antes de reintentar.",
        headers={"Retry-After": str(retry_after)},
    )
