import os
import tempfile
import unittest
from pathlib import Path
from unittest.mock import Mock, patch

from local_services import (
    LocalServiceSession,
    ensure_live_local_services,
    wait_for_obs_ready,
)


class LocalServicesTest(unittest.TestCase):
    @patch("local_services._tcp_service_available", return_value=True)
    @patch("local_services.subprocess.Popen")
    @patch("local_services.subprocess.run")
    @patch("local_services._service_available", return_value=True)
    def test_reuses_services_that_are_already_running(
        self,
        service_available_mock,
        subprocess_run_mock,
        popen_mock,
        tcp_service_available_mock,
    ):
        with patch.dict(os.environ, {"OBS_WEBSOCKET_ENABLED": "true"}):
            session = ensure_live_local_services()

        self.assertIsNone(session.aituber_process)
        self.assertEqual(service_available_mock.call_count, 2)
        tcp_service_available_mock.assert_called_once_with("127.0.0.1", 4455)
        subprocess_run_mock.assert_not_called()
        popen_mock.assert_not_called()

    @patch(
        "local_services._tcp_service_available",
        side_effect=[False, True],
    )
    @patch("local_services.shutil.which", return_value="/usr/local/bin/node")
    @patch("local_services.subprocess.Popen")
    @patch("local_services.subprocess.run")
    @patch(
        "local_services._service_available",
        side_effect=[False, True, False, True],
    )
    def test_starts_aivis_and_aituber_without_opening_a_web_browser(
        self,
        service_available_mock,
        subprocess_run_mock,
        popen_mock,
        which_mock,
        tcp_service_available_mock,
    ):
        process = Mock()
        process.poll.return_value = None
        popen_mock.return_value = process

        with tempfile.TemporaryDirectory() as temp_directory:
            root = Path(temp_directory)
            app_path = root / "AivisSpeech.app"
            app_path.mkdir()
            obs_app_path = root / "OBS.app"
            obs_app_path.mkdir()
            aituber_directory = root / "my-aituber"
            vite_script = aituber_directory / "node_modules/vite/bin/vite.js"
            vite_script.parent.mkdir(parents=True)
            vite_script.touch()
            (aituber_directory / "package.json").touch()
            environment = {
                "AIVIS_APP_PATH": str(app_path),
                "AITUBER_DIRECTORY": str(aituber_directory),
                "AITUBER_ONAIR_URL": "http://localhost:5173/?mode=broadcast",
                "OBS_APP_PATH": str(obs_app_path),
                "OBS_WEBSOCKET_ENABLED": "true",
            }

            with patch.dict(os.environ, environment):
                session = ensure_live_local_services()

        subprocess_run_mock.assert_any_call(
            ["open", str(app_path)],
            check=True,
            timeout=10,
        )
        subprocess_run_mock.assert_any_call(
            ["open", str(obs_app_path)],
            check=True,
            timeout=10,
        )
        self.assertEqual(subprocess_run_mock.call_count, 2)
        popen_mock.assert_called_once_with(
            ["/usr/local/bin/node", str(vite_script), "--host", "localhost"],
            cwd=str(aituber_directory),
        )
        self.assertIs(session.aituber_process, process)
        self.assertEqual(service_available_mock.call_count, 4)
        self.assertEqual(tcp_service_available_mock.call_count, 2)
        which_mock.assert_called_once_with("node")

    def test_stops_only_the_aituber_process_owned_by_the_session(self):
        process = Mock()
        process.poll.return_value = None
        session = LocalServiceSession(aituber_process=process)

        session.stop()

        process.terminate.assert_called_once_with()
        process.wait.assert_called_once_with(timeout=5)

    @patch.dict(os.environ, {"AUTO_START_AIVIS": "sometimes"})
    @patch("local_services._service_available", return_value=False)
    def test_rejects_invalid_auto_start_setting(self, service_available_mock):
        with self.assertRaisesRegex(RuntimeError, "trueまたはfalse"):
            ensure_live_local_services()

    @patch("local_services.time.sleep")
    def test_waits_until_obs_accepts_status_requests(self, sleep_mock):
        client = Mock()
        client.get_stream_status.side_effect = [
            RuntimeError(
                "OBS GetStreamStatusに失敗しました。 "
                "code=207 comment=OBS is not ready to perform the request."
            ),
            False,
        ]

        output_active = wait_for_obs_ready(client)

        self.assertFalse(output_active)
        self.assertEqual(client.get_stream_status.call_count, 2)
        sleep_mock.assert_called_once_with(0.5)

    @patch("local_services.time.sleep")
    def test_does_not_retry_obs_authentication_errors(self, sleep_mock):
        client = Mock()
        client.get_stream_status.side_effect = RuntimeError(
            "OBS WebSocketの認証に失敗しました。"
        )

        with self.assertRaisesRegex(RuntimeError, "認証に失敗"):
            wait_for_obs_ready(client)

        client.get_stream_status.assert_called_once_with()
        sleep_mock.assert_not_called()


if __name__ == "__main__":
    unittest.main()
