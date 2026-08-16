import threading
import unittest
from unittest.mock import Mock

from live_service import LiveServiceController


class LiveServiceControllerTest(unittest.TestCase):
    def test_live_callback_runs_in_background_and_returns_to_idle(self):
        runtime = Mock()
        completed = threading.Event()

        def live_callback():
            completed.set()

        controller = LiveServiceController(runtime, live_callback)

        self.assertTrue(controller.start())
        self.assertTrue(completed.wait(timeout=1))
        controller._thread.join(timeout=1)

        runtime.update_admin_status.assert_any_call(
            available=True,
            phase="service_idle",
            message="管理画面は稼働中です。次のライブ開始を待っています。",
        )

    def test_duplicate_start_is_rejected(self):
        runtime = Mock()
        release = threading.Event()
        started = threading.Event()

        def live_callback():
            started.set()
            release.wait(timeout=1)

        controller = LiveServiceController(runtime, live_callback)
        controller.start()
        self.assertTrue(started.wait(timeout=1))

        with self.assertRaisesRegex(RuntimeError, "すでに起動"):
            controller.start()

        release.set()
        controller._thread.join(timeout=1)

    def test_stop_request_is_sent_to_live_loop(self):
        runtime = Mock()
        runtime.get_admin_status.return_value = {"phase": "waiting_for_youtube"}
        release = threading.Event()
        controller = LiveServiceController(
            runtime,
            lambda: release.wait(timeout=1),
        )
        controller.start()

        controller.request_stop()

        runtime.enqueue_admin_command.assert_called_once_with(
            {"action": "stop_live_control"}
        )
        release.set()
        controller._thread.join(timeout=1)


if __name__ == "__main__":
    unittest.main()
