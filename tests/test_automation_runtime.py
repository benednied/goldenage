from threading import Event

from goldenage.adapters.automation_runtime import AutomationSchedulerWorker


def test_scheduler_worker_ticks_once_and_stops_cleanly() -> None:
    ticked = Event()

    def tick() -> None:
        ticked.set()

    worker = AutomationSchedulerWorker(tick, interval_seconds=5)
    worker.start()
    assert ticked.wait(1)
    worker.stop()
    assert worker._thread is not None
    assert not worker._thread.is_alive()


def test_scheduler_worker_keeps_polling_after_a_callback_failure() -> None:
    calls = 0
    failed = Event()
    ticked = Event()

    def tick() -> None:
        nonlocal calls
        calls += 1
        if calls == 1:
            failed.set()
            raise RuntimeError("temporary scheduler error")
        ticked.set()

    worker = AutomationSchedulerWorker(tick, interval_seconds=5)
    worker.start()
    assert failed.wait(1)
    # Invoke one follow-up tick directly to avoid making the test wait five seconds.
    worker._tick()
    assert ticked.is_set()
    assert isinstance(worker.last_error, RuntimeError)
    worker.stop()
