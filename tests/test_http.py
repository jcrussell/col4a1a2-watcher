from col4a1a2.http import RateLimiter


def test_rate_limiter_spaces_requests_per_host():
    now = [0.0]
    slept = []

    def clock():
        return now[0]

    def sleep(s):
        slept.append(s)
        now[0] += s

    rl = RateLimiter(rates={"a.example": 2.0, "b.example": 1.0}, clock=clock, sleeper=sleep)
    for _ in range(3):
        rl.acquire("a.example")
    assert abs(sum(slept) - 1.0) < 1e-9  # 3 requests at 2/s -> two 0.5 s waits
    slept.clear()
    rl.acquire("b.example")  # different host: no wait
    assert slept == []
    rl.acquire("b.example")
    assert abs(slept[0] - 1.0) < 1e-9
    assert rl.interval("unknown.example") == 1.0


def test_cache_sets_incremental_vacuum_and_a_busy_timeout(tmp_path):
    """DELETE alone never shrinks the file, and enabling incremental vacuum
    requires a VACUUM that is cheap now and painful at gigabytes."""
    from col4a1a2.http import HttpCache

    c = HttpCache(tmp_path / "http.sqlite")
    assert c._db.execute("PRAGMA auto_vacuum").fetchone()[0] == 2  # 2 = INCREMENTAL
    assert c._db.execute("PRAGMA busy_timeout").fetchone()[0] == 30000
