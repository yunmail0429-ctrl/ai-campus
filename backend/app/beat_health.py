"""Beat health tracks successful dispatch publication, never mere process existence."""
import time
from pathlib import Path

from celery.beat import PersistentScheduler

HEARTBEAT = Path("/app/local-data/beat-heartbeat")


class HeartbeatScheduler(PersistentScheduler):
    def apply_async(self, entry, producer=None, advance=True, **kwargs):
        result = super().apply_async(entry, producer=producer, advance=advance, **kwargs)
        if entry.task == "xflj.dispatch":
            HEARTBEAT.write_text(str(time.monotonic()), encoding="ascii")
        return result


def healthy(path=HEARTBEAT):
    try:
        age = time.monotonic() - float(path.read_text(encoding="ascii"))
        return 0 <= age < 60
    except (OSError, ValueError):
        return False


if __name__ == "__main__":
    raise SystemExit(0 if healthy() else 1)
