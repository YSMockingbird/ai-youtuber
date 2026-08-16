import os
import shutil
import socket
import subprocess
import time
from pathlib import Path

import requests


DEFAULT_AIVIS_APP_PATH = "/Applications/AivisSpeech.app"
DEFAULT_AIVIS_API_URL = "http://127.0.0.1:10101"
DEFAULT_AITUBER_DIRECTORY = "~/my-aituber"
DEFAULT_AITUBER_URL = "http://localhost:5173/?mode=broadcast"
DEFAULT_OBS_APP_PATH = "/Applications/OBS.app"


class LocalServiceSession:
    def __init__(self, aituber_process=None):
        self.aituber_process = aituber_process

    def stop(self):
        # このPythonプロセスが起動したViteだけを終了し、既存プロセスには触れません。
        process = self.aituber_process
        if process is None or process.poll() is not None:
            return
        process.terminate()
        try:
            process.wait(timeout=5)
        except subprocess.TimeoutExpired:
            process.kill()
            process.wait(timeout=5)
        print("AITuber OnAirの自動起動プロセスを停止しました。")


def ensure_live_local_services():
    timeout_seconds = _get_start_timeout_seconds()
    _ensure_aivis_speech(timeout_seconds)
    aituber_process = _ensure_aituber_onair(timeout_seconds)
    _ensure_obs(timeout_seconds)
    return LocalServiceSession(aituber_process=aituber_process)


def wait_for_obs_ready(obs_websocket_client):
    # ポートが開いた後もOBS内部の初期化が続くため、実際の操作成功まで待機します。
    timeout_seconds = _get_start_timeout_seconds()
    deadline = time.monotonic() + timeout_seconds
    last_error = None
    waiting_message_printed = False
    while time.monotonic() < deadline:
        try:
            output_active = obs_websocket_client.get_stream_status()
            print("OBSの操作準備が完了しました。")
            return output_active
        except RuntimeError as exc:
            if not _is_retryable_obs_startup_error(exc):
                raise
            last_error = exc
            if not waiting_message_printed:
                print("OBS内部の初期化完了を待っています。")
                waiting_message_printed = True
            time.sleep(0.5)
    raise RuntimeError(
        f"OBSが{timeout_seconds}秒以内に操作可能になりませんでした。"
        f"最後のエラー={last_error}"
    ) from last_error


def _ensure_aivis_speech(timeout_seconds):
    api_url = os.getenv("AIVIS_API_URL", DEFAULT_AIVIS_API_URL).strip().rstrip("/")
    version_url = f"{api_url}/version"
    if _service_available(version_url):
        print("AivisSpeechはすでに起動しています。")
        return
    if not _get_boolean_env("AUTO_START_AIVIS", True):
        raise RuntimeError(
            "AivisSpeechが起動していません。AUTO_START_AIVIS=falseのため"
            "自動起動も行いません。"
        )

    app_path = Path(
        os.getenv("AIVIS_APP_PATH", DEFAULT_AIVIS_APP_PATH).strip()
    ).expanduser()
    if not app_path.is_dir():
        raise RuntimeError(f"AivisSpeechアプリが見つかりません: {app_path}")
    try:
        subprocess.run(
            ["open", str(app_path)],
            check=True,
            timeout=10,
        )
    except (OSError, subprocess.SubprocessError) as exc:
        raise RuntimeError(f"AivisSpeechを自動起動できませんでした: {exc}") from exc
    print(f"AivisSpeechを起動しました。APIの準備を待っています: {version_url}")
    _wait_for_service(version_url, "AivisSpeech", timeout_seconds)


def _ensure_aituber_onair(timeout_seconds):
    url = os.getenv("AITUBER_ONAIR_URL", DEFAULT_AITUBER_URL).strip()
    if _service_available(url):
        print("AITuber OnAirはすでに起動しています。")
        return None
    if not _get_boolean_env("AUTO_START_AITUBER", True):
        raise RuntimeError(
            "AITuber OnAirが起動していません。AUTO_START_AITUBER=falseのため"
            "自動起動も行いません。"
        )

    project_directory = Path(
        os.getenv("AITUBER_DIRECTORY", DEFAULT_AITUBER_DIRECTORY).strip()
    ).expanduser()
    vite_script = project_directory / "node_modules" / "vite" / "bin" / "vite.js"
    if not (project_directory / "package.json").is_file():
        raise RuntimeError(
            f"AITuber OnAirのpackage.jsonが見つかりません: {project_directory}"
        )
    if not vite_script.is_file():
        raise RuntimeError(
            "AITuber OnAirのViteが見つかりません。npm installを確認してください。"
            f" path={vite_script}"
        )
    node_path = shutil.which("node")
    if not node_path:
        raise RuntimeError("Node.jsが見つからないためAITuber OnAirを起動できません。")

    try:
        process = subprocess.Popen(
            [node_path, str(vite_script), "--host", "localhost"],
            cwd=str(project_directory),
        )
    except OSError as exc:
        raise RuntimeError(f"AITuber OnAirを自動起動できませんでした: {exc}") from exc
    print(f"AITuber OnAirを起動しました。準備を待っています: {url}")
    try:
        _wait_for_service(
            url,
            "AITuber OnAir",
            timeout_seconds,
            process=process,
        )
    except RuntimeError:
        if process.poll() is None:
            process.terminate()
            process.wait(timeout=5)
        raise
    return process


def _ensure_obs(timeout_seconds):
    websocket_enabled = _get_boolean_env("OBS_WEBSOCKET_ENABLED", False)
    host = os.getenv("OBS_WEBSOCKET_HOST", "127.0.0.1").strip()
    if host not in {"127.0.0.1", "localhost", "::1"}:
        raise RuntimeError("OBS WebSocketの接続先はローカルMacだけにしてください。")
    port = _get_port_env("OBS_WEBSOCKET_PORT", 4455)
    if websocket_enabled and _tcp_service_available(host, port):
        print("OBSはすでに起動しています。")
        return
    if not _get_boolean_env("AUTO_START_OBS", True):
        if websocket_enabled:
            raise RuntimeError(
                "OBSが起動していません。AUTO_START_OBS=falseのため"
                "自動起動も行いません。"
            )
        return

    app_path = Path(os.getenv("OBS_APP_PATH", DEFAULT_OBS_APP_PATH).strip()).expanduser()
    if not app_path.is_dir():
        raise RuntimeError(f"OBSアプリが見つかりません: {app_path}")
    try:
        subprocess.run(
            ["open", str(app_path)],
            check=True,
            timeout=10,
        )
    except (OSError, subprocess.SubprocessError) as exc:
        raise RuntimeError(f"OBSを自動起動できませんでした: {exc}") from exc
    print(f"OBSを起動しました: {app_path}")
    if websocket_enabled:
        print(f"OBS WebSocketの準備を待っています: {host}:{port}")
        _wait_for_tcp_service(host, port, "OBS WebSocketポート", timeout_seconds)
    else:
        print(
            "OBS WebSocketは無効です。OBSアプリだけを起動し、"
            "配信出力の開始・停止は自動操作しません。"
        )


def _wait_for_service(url, service_name, timeout_seconds, process=None):
    deadline = time.monotonic() + timeout_seconds
    while time.monotonic() < deadline:
        if process is not None and process.poll() is not None:
            raise RuntimeError(
                f"{service_name}が準備完了前に終了しました。exit_code={process.returncode}"
            )
        if _service_available(url):
            print(f"{service_name}の起動を確認しました。")
            return
        time.sleep(0.5)
    raise RuntimeError(
        f"{service_name}が{timeout_seconds}秒以内に起動しませんでした。URL={url}"
    )


def _service_available(url):
    try:
        response = requests.get(url, timeout=1)
        response.raise_for_status()
        return True
    except requests.exceptions.RequestException:
        return False


def _wait_for_tcp_service(host, port, service_name, timeout_seconds):
    deadline = time.monotonic() + timeout_seconds
    while time.monotonic() < deadline:
        if _tcp_service_available(host, port):
            print(f"{service_name}の起動を確認しました。")
            return
        time.sleep(0.5)
    raise RuntimeError(
        f"{service_name}が{timeout_seconds}秒以内に起動しませんでした。"
        f"接続先={host}:{port}"
    )


def _tcp_service_available(host, port):
    try:
        with socket.create_connection((host, port), timeout=1):
            return True
    except OSError:
        return False


def _is_retryable_obs_startup_error(error):
    message = str(error)
    return (
        "code=207" in message
        or "OBS WebSocketへ接続できませんでした" in message
    )


def _get_boolean_env(name, default):
    raw_value = os.getenv(name, "true" if default else "false").strip().lower()
    if raw_value in {"true", "1", "yes", "on"}:
        return True
    if raw_value in {"false", "0", "no", "off"}:
        return False
    raise RuntimeError(f"{name}はtrueまたはfalseで設定してください。")


def _get_start_timeout_seconds():
    raw_value = os.getenv("LOCAL_SERVICE_START_TIMEOUT_SECONDS", "120").strip()
    try:
        timeout_seconds = int(raw_value)
    except ValueError as exc:
        raise RuntimeError(
            "LOCAL_SERVICE_START_TIMEOUT_SECONDSは整数で設定してください。"
        ) from exc
    if not 10 <= timeout_seconds <= 600:
        raise RuntimeError(
            "LOCAL_SERVICE_START_TIMEOUT_SECONDSは10〜600秒で設定してください。"
        )
    return timeout_seconds


def _get_port_env(name, default):
    raw_value = os.getenv(name, str(default)).strip()
    try:
        port = int(raw_value)
    except ValueError as exc:
        raise RuntimeError(f"{name}は整数で設定してください。") from exc
    if not 1 <= port <= 65535:
        raise RuntimeError(f"{name}は1〜65535で設定してください。")
    return port
