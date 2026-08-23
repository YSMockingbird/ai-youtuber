import threading
import traceback


class LiveServiceController:
    def __init__(self, runtime, live_callback, prepare_callback=None):
        self.runtime = runtime
        self.live_callback = live_callback
        self.prepare_callback = prepare_callback
        self._lock = threading.Lock()
        self._thread = None
        self._starting = False
        self._preparing = False
        self._prepared_schedule_id = None
        self._prepared_context = None

    def is_running(self):
        with self._lock:
            return self._starting or (
                self._thread is not None and self._thread.is_alive()
            )

    def is_prepared(self, schedule_id=None):
        normalized_schedule_id = str(schedule_id or "").strip()
        with self._lock:
            if self._prepared_context is None:
                return False
            return (
                not normalized_schedule_id
                or self._prepared_schedule_id == normalized_schedule_id
            )

    def prepare(self, schedule_id):
        normalized_schedule_id = str(schedule_id or "").strip()
        if not normalized_schedule_id:
            raise ValueError("事前準備する配信予定IDが空です。")
        if self.prepare_callback is None:
            raise RuntimeError("ライブ制御の事前準備処理が設定されていません。")

        stale_context = None
        with self._lock:
            if self._starting or (
                self._thread is not None and self._thread.is_alive()
            ):
                raise RuntimeError("ライブ制御の実行中は事前準備できません。")
            if self._preparing:
                raise RuntimeError("別の配信予定を事前準備中です。")
            if (
                self._prepared_context is not None
                and self._prepared_schedule_id == normalized_schedule_id
            ):
                return False
            stale_context = self._prepared_context
            self._prepared_context = None
            self._prepared_schedule_id = None
            self._preparing = True

        self._cleanup_context(stale_context)
        self.runtime.update_admin_status(
            available=True,
            phase="preparing_local_services",
            message="AivisSpeech、AITuber OnAir、OBSを事前準備しています。",
        )
        try:
            prepared_context = self.prepare_callback()
        except Exception as exc:
            with self._lock:
                self._preparing = False
            self.runtime.update_admin_status(
                available=True,
                phase="service_idle",
                message=f"配信アプリの事前準備に失敗しました: {exc}",
            )
            raise

        with self._lock:
            self._prepared_schedule_id = normalized_schedule_id
            self._prepared_context = prepared_context
            self._preparing = False
        self.runtime.update_admin_status(
            available=True,
            phase="service_idle",
            message="配信アプリの事前準備が完了しました。予定時刻を待っています。",
        )
        return True

    def discard_preparation(self, schedule_id=None):
        normalized_schedule_id = str(schedule_id or "").strip()
        with self._lock:
            if self._prepared_context is None:
                return False
            if (
                normalized_schedule_id
                and self._prepared_schedule_id != normalized_schedule_id
            ):
                return False
            prepared_context = self._prepared_context
            self._prepared_context = None
            self._prepared_schedule_id = None
        self._cleanup_context(prepared_context)
        return True

    def start(self, schedule_id=None):
        normalized_schedule_id = str(schedule_id or "").strip()
        stale_context = None
        with self._lock:
            if self._starting or (
                self._thread is not None and self._thread.is_alive()
            ):
                raise RuntimeError("ライブ制御はすでに起動しています。")
            if self._preparing:
                raise RuntimeError("配信アプリの事前準備が完了していません。")
            prepared_context = None
            if (
                self._prepared_context is not None
                and normalized_schedule_id
                and self._prepared_schedule_id == normalized_schedule_id
            ):
                prepared_context = self._prepared_context
            else:
                stale_context = self._prepared_context
            self._prepared_context = None
            self._prepared_schedule_id = None
            self._starting = True

        try:
            self._cleanup_context(stale_context)
        except Exception:
            with self._lock:
                self._starting = False
            raise

        with self._lock:
            self._thread = threading.Thread(
                target=self._run,
                args=(prepared_context,),
                name="ai-youtuber-live-service",
                daemon=True,
            )
            try:
                self._thread.start()
            except Exception:
                self._thread = None
                self._starting = False
                raise
            self._starting = False
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

    def _run(self, prepared_context):
        self.runtime.update_admin_status(
            available=True,
            phase="starting_live_control",
            message=(
                "事前準備済みの配信アプリでライブ制御を開始しています。"
                if prepared_context is not None
                else "AivisSpeech、AITuber OnAir、OBSを準備しています。"
            ),
        )
        try:
            self.live_callback(prepared_context)
        except Exception as exc:
            # 常駐スレッドの例外をログへ残し、管理画面にも原因を表示します。
            traceback.print_exc()
            self.runtime.fail_pending_broadcast_starts(exc)
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

    @staticmethod
    def _cleanup_context(context):
        if context is None:
            return
        stop = getattr(context, "stop", None)
        if not callable(stop):
            raise RuntimeError("事前準備した配信サービスを終了できません。")
        stop()
