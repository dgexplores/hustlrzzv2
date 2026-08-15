from backend.security import SlidingWindowLimiter, trusted_origin


def test_sliding_window_limiter_blocks_after_limit():
    limiter = SlidingWindowLimiter()
    assert limiter.allow("user-a", 2)
    assert limiter.allow("user-a", 2)
    assert not limiter.allow("user-a", 2)


def test_trusted_origin_rejects_unrelated_origin(monkeypatch):
    from backend import config

    monkeypatch.setattr(config, "CORS_ORIGINS", ["https://app.example.com"])
    monkeypatch.setattr(config, "CORS_ORIGIN_REGEX", "")
    assert trusted_origin("https://app.example.com")
    assert not trusted_origin("https://evil.example.com")
    assert not trusted_origin(None)
