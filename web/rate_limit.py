"""Project adapter over the shared AI Lodge rate-limit engine.

rate_limit_engine.py is vendored unmodified from the club's shared engine; this
adapter supplies the project slug and its buckets. The engine handles
X-Forwarded-For, central limits, event mode, the kill switch (503) and fail-open.
Without Upstash keys it is off (local development).

  ask     one hit per question, however many model calls answering it takes
          (rewrite, route, embed, answer, support check, number check)
  upload  one hit per file added: embedding its passages is a model call
"""
import os

from rate_limit_engine import RateLimiter


def _env_int(name, default):
    try:
        return int(os.environ.get(name, "").strip() or default)
    except ValueError:
        return default


limiter = RateLimiter(
    # Slug = full project folder name (matches the central control sheet rows).
    project="ail-ay2627-s1-elton-anchor",
    buckets={
        "ask": _env_int("RATE_LIMIT_ASK_MAX", 5),
        "upload": _env_int("RATE_LIMIT_UPLOAD_MAX", 3),
    },
    redis_url=os.environ.get("UPSTASH_REDIS_REST_URL") or os.environ.get("UPSTASH_REDIS_URL") or "",
    redis_token=os.environ.get("UPSTASH_REDIS_REST_TOKEN") or os.environ.get("UPSTASH_REDIS_TOKEN") or "",
    default_window=_env_int("RATE_LIMIT_WINDOW_SECONDS", 432000),
)


def check_rate_limit(request, bucket):
    limiter.enforce(request, bucket)
