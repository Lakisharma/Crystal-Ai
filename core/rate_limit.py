"""
Production-grade lightweight security utilities:
- Client IP resolution behind reverse proxies (Render, Cloudflare, AWS ALB)
- Cache-backed brute-force rate-limiting for authentication endpoints
"""

import logging
from django.core.cache import cache

logger = logging.getLogger(__name__)


def get_client_ip(request) -> str:
    """
    Safely resolves the true client IP address, handling reverse proxies.
    Precedence: X-Forwarded-For (left-most client IP) -> REMOTE_ADDR.
    """
    x_forwarded_for = request.META.get('HTTP_X_FORWARDED_FOR')
    if x_forwarded_for:
        ip = x_forwarded_for.split(',')[0].strip()
        if ip:
            return ip
    return request.META.get('REMOTE_ADDR', '127.0.0.1')


def is_rate_limited(key: str, max_attempts: int = 5, timeout_seconds: int = 300) -> bool:
    """
    Checks if an action identified by `key` has exceeded `max_attempts`.
    Does NOT increment the counter; use `record_failed_attempt` on failure.
    """
    attempts = cache.get(key, 0)
    return attempts >= max_attempts


def record_failed_attempt(key: str, timeout_seconds: int = 300) -> int:
    """
    Increments failed attempt count by 1. Resets TTL to timeout_seconds.
    Returns the new attempt count.
    """
    attempts = cache.get(key, 0) + 1
    cache.set(key, attempts, timeout=timeout_seconds)
    return attempts


def clear_failed_attempts(key: str) -> None:
    """Clears rate limit counter upon successful operation (e.g. successful login)."""
    cache.delete(key)
