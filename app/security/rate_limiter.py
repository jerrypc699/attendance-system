import time
import threading
from collections import defaultdict, deque
from fastapi import HTTPException, Request, status
from app.security.auth import get_client_ip


class SlidingWindowRateLimiter:
    def __init__(self):
        self._lock = threading.Lock()
        self._buckets: dict[str, deque[float]] = defaultdict(deque)

    def check(self, key: str, max_requests: int, window_seconds: int):
        now = time.monotonic()
        cutoff = now - window_seconds
        with self._lock:
            q = self._buckets[key]
            while q and q[0] < cutoff:
                q.popleft()
            if len(q) >= max_requests:
                raise HTTPException(
                    status_code=status.HTTP_429_TOO_MANY_REQUESTS,
                    detail="تم تجاوز الحد المسموح من الطلبات مؤقتاً، يرجى الانتظار قليلاً ثم المحاولة.",
                )
            q.append(now)

    def reset(self):
        with self._lock:
            self._buckets.clear()


rate_limiter = SlidingWindowRateLimiter()


def limit_requests(bucket_name: str, max_requests: int, window_seconds: int):
    def _dep(request: Request):
        ip = get_client_ip(request)
        rate_limiter.check(f"{bucket_name}:{ip}", max_requests=max_requests, window_seconds=window_seconds)
    return _dep

