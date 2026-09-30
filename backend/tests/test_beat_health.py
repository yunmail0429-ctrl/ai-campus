from types import SimpleNamespace

import pytest
from celery.beat import PersistentScheduler

from app import beat_health


def test_scheduler_heartbeat_requires_successful_publication(tmp_path, monkeypatch):
    path = tmp_path / "heartbeat"
    monkeypatch.setattr(beat_health, "HEARTBEAT", path)
    monkeypatch.setattr(beat_health.time, "monotonic", lambda: 100)
    scheduler = object.__new__(beat_health.HeartbeatScheduler)
    entry = SimpleNamespace(task="xflj.dispatch")
    assert not beat_health.healthy(path)
    monkeypatch.setattr(PersistentScheduler, "apply_async", lambda *a, **k: "sent")
    assert scheduler.apply_async(entry) == "sent"
    assert beat_health.healthy(path)
    monkeypatch.setattr(beat_health.time, "monotonic", lambda: 161)
    assert not beat_health.healthy(path)

    def failed(*args, **kwargs):
        raise RuntimeError("broker unavailable")

    monkeypatch.setattr(PersistentScheduler, "apply_async", failed)
    with pytest.raises(RuntimeError):
        scheduler.apply_async(entry)
    assert path.read_text() == "100"
    assert not beat_health.healthy(path)
    path.write_text("corrupt")
    assert not beat_health.healthy(path)
