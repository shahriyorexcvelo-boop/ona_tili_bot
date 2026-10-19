import asyncio
import time
from typing import Dict, List, Tuple

class HighLoadRateLimiter:
    """
    Foydalanuvchi tg_id yoki IP bo'yicha so'rovlar chastotasini xavfsiz cheklash (Rate Limiting).
    1-3 soniyalik limit: 1 soniyada 1 tadan, 2 soniyada ko'pi bilan 1 tadan ortiq og'ir so'rov yuborishni bloklaydi.
    """
    def __init__(self):
        self._history: Dict[str, List[float]] = {}
        self._lock = asyncio.Lock()
        self._last_clean = time.time()

    async def check(self, key: str, max_requests: int = 1, window_seconds: float = 2.0) -> Tuple[bool, float]:
        async with self._lock:
            now = time.time()
            if now - self._last_clean > 60.0:
                self._cleanup(now)

            timestamps = self._history.get(key, [])
            recent = [t for t in timestamps if now - t < window_seconds]

            if len(recent) >= max_requests:
                earliest = recent[0]
                retry_after = round(max(0.3, window_seconds - (now - earliest)), 1)
                return False, retry_after

            recent.append(now)
            self._history[key] = recent
            return True, 0.0

    def _cleanup(self, now: float):
        self._last_clean = now
        stale = now - 30.0
        to_del = [k for k, v in self._history.items() if not v or v[-1] < stale]
        for k in to_del:
            del self._history[k]

GLOBAL_RATE_LIMITER = HighLoadRateLimiter()

# Neon PostgreSQL Connection Pool (maxconn=20) ga to'liq moslangan Asinxron Navbat (Queue/Semaphore)
# Bir vaqtda ko'pi bilan 20 ta og'ir DB tranzaksiyasiga ruxsat beriladi.
# 20 tadan ortiq so'rov kelsa, ular xato bermasdan FIFO navbatida asinxron kutadi.
SUBMIT_CONCURRENCY_SEMAPHORE = asyncio.Semaphore(20)
COMPARE_CONCURRENCY_SEMAPHORE = asyncio.Semaphore(15)
