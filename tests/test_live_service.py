import threading
import unittest
from unittest.mock import Mock

from live_service import LiveServiceController


class LiveServiceControllerTest(unittest.TestCase):
    def test_live_callback_runs_in_background_and_returns_to_idle(self):
        runtime = Mock()
        completed = threading.Event()

        def live_callback(_prepared_context):
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

        def live_callback(_prepared_context):
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
            lambda _prepared_context: release.wait(timeout=1),
        )
        controller.start()

        controller.request_stop()

        runtime.enqueue_admin_command.assert_called_once_with(
            {"action": "stop_live_control"}
        )
        release.set()
        controller._thread.join(timeout=1)

    def test_prepared_context_is_reused_by_matching_schedule(self):
        runtime = Mock()
        prepared_context = Mock()
        received = []
        completed = threading.Event()

        def live_callback(context):
            received.append(context)
            completed.set()

        controller = LiveServiceController(
            runtime,
            live_callback,
            prepare_callback=lambda: prepared_context,
        )

        self.assertTrue(controller.prepare("schedule-1"))
        self.assertTrue(controller.is_prepared("schedule-1"))
        self.assertFalse(controller.prepare("schedule-1"))
        controller.start(schedule_id="schedule-1")
        self.assertTrue(completed.wait(timeout=1))
        controller._thread.join(timeout=1)

        self.assertEqual(received, [prepared_context])

    def test_discard_preparation_stops_prepared_context(self):
        runtime = Mock()
        prepared_context = Mock()
        controller = LiveServiceController(
            runtime,
            lambda _prepared_context: None,
            prepare_callback=lambda: prepared_context,
        )
        controller.prepare("schedule-1")

        self.assertTrue(controller.discard_preparation("schedule-1"))

        prepared_context.stop.assert_called_once_with()
        self.assertFalse(controller.is_prepared("schedule-1"))


if __name__ == "__main__":
    unittest.main()
