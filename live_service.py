import threading
import traceback


class LiveServiceController:
    def __init__(self, runtime, live_callback):
        self.runtime = runtime
        self.live_callback = live_callback
        self._lock = threading.Lock()
        self._thread = None

    def is_running(self):
        with self._lock:
            return self._thread is not None and self._thread.is_alive()

    def start(self):
        with self._lock:
            if self._thread is not None and self._thread.is_alive():
                raise RuntimeError("ライブ制御はすでに起動しています。")
            self._thread = threading.Thread(
                target=self._run,
                name="ai-youtuber-live-service",
                daemon=True,
            )
            self._thread.start()
        return True

    def request_stop(self):
        if not self.is_running():
            raise RuntimeError("ライブ制御は起動していません。")
        phase = self.runtime.get_admin_status().get("phase")
        if phase not in {
            "starting_live_control",
            "waiting_for_obs",
            "waiting_for_youtube",
            "preparing_broadcast_draft",
        }:
            raise RuntimeError(
                "配信開始後は「終了挨拶して配信終了」を使用してください。"
            )
        self.runtime.enqueue_admin_command({"action": "stop_live_control"})
        self.runtime.update_admin_status(
            phase="stopping_live_control",
            message="ライブ制御の停止を受け付けました。",
        )
        return True

    def wait(self, timeout=None):
        with self._lock:
            thread = self._thread
        if thread is not None:
            thread.join(timeout=timeout)

    def _run(self):
        self.runtime.update_admin_status(
            available=True,
            phase="starting_live_control",
            message="AivisSpeech、AITuber OnAir、OBSを準備しています。",
        )
        try:
            self.live_callback()
        except Exception as exc:
            # 常駐スレッドの例外をログへ残し、管理画面にも原因を表示します。
            traceback.print_exc()
            self.runtime.update_admin_status(
                available=True,
                phase="live_control_error",
                message=f"ライブ制御がエラーで停止しました: {exc}",
            )
            print(f"ライブ制御エラー: {exc}")
            return
        finally:
            with self._lock:
                self._thread = None
        self.runtime.update_admin_status(
            available=True,
            phase="service_idle",
            message="管理画面は稼働中です。次のライブ開始を待っています。",
        )
