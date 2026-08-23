import os
import sqlite3
import uuid
from datetime import datetime, timezone
from pathlib import Path

from llm.config import PROJECT_ROOT


ALLOWED_PLANNING_MODES = {"ai", "manual"}
ALLOWED_PRIVACY_STATUSES = {"public", "unlisted", "private"}
ALLOWED_SCHEDULE_STATUSES = {
    "draft",
    "prepared",
    "starting",
    "youtube_scheduled",
    "live",
    "completed",
    "cancelled",
    "error",
}


class BroadcastScheduleRepository:
    def __init__(self, database_path):
        self.database_path = Path(database_path)
        self.database_path.parent.mkdir(parents=True, exist_ok=True)
        self._initialize()

    def _connect(self):
        connection = sqlite3.connect(str(self.database_path), timeout=5)
        connection.row_factory = sqlite3.Row
        connection.execute("PRAGMA foreign_keys = ON")
        return connection

    def _initialize(self):
        try:
            with self._connect() as connection:
                # 管理画面と配信処理が同時に参照しても待ち時間を抑えます。
                connection.execute("PRAGMA journal_mode = WAL")
                connection.execute(
                    """
                    CREATE TABLE IF NOT EXISTS broadcast_templates (
                        template_id TEXT PRIMARY KEY,
                        name TEXT NOT NULL UNIQUE,
                        title TEXT NOT NULL,
                        description TEXT NOT NULL,
                        privacy_status TEXT NOT NULL,
                        created_at TEXT NOT NULL,
                        updated_at TEXT NOT NULL
                    )
                    """
                )
                connection.execute(
                    """
                    CREATE TABLE IF NOT EXISTS broadcast_schedules (
                        schedule_id TEXT PRIMARY KEY,
                        scheduled_start_at TEXT NOT NULL,
                        planning_mode TEXT NOT NULL,
                        content_request TEXT NOT NULL,
                        prepared_stream_plan TEXT,
                        title TEXT NOT NULL,
                        description TEXT NOT NULL,
                        privacy_status TEXT NOT NULL,
                        auto_start INTEGER NOT NULL DEFAULT 1,
                        template_id TEXT,
                        status TEXT NOT NULL,
                        youtube_video_id TEXT,
                        last_error TEXT,
                        prepared_at TEXT,
                        created_at TEXT NOT NULL,
                        updated_at TEXT NOT NULL,
                        FOREIGN KEY(template_id)
                            REFERENCES broadcast_templates(template_id)
                            ON DELETE SET NULL
                    )
                    """
                )
                schedule_columns = {
                    row[1]
                    for row in connection.execute(
                        "PRAGMA table_info(broadcast_schedules)"
                    ).fetchall()
                }
                if "auto_start" not in schedule_columns:
                    # 既存の予定DBを削除せず、自動開始設定だけを追加します。
                    connection.execute(
                        "ALTER TABLE broadcast_schedules "
                        "ADD COLUMN auto_start INTEGER NOT NULL DEFAULT 1"
                    )
                connection.execute(
                    "CREATE INDEX IF NOT EXISTS idx_broadcast_schedules_start "
                    "ON broadcast_schedules(scheduled_start_at ASC)"
                )
                connection.execute(
                    "CREATE INDEX IF NOT EXISTS idx_broadcast_schedules_status "
                    "ON broadcast_schedules(status, scheduled_start_at ASC)"
                )
        except sqlite3.Error as exc:
            raise RuntimeError(
                "配信予定データベースを初期化できません: "
                f"{self.database_path}"
            ) from exc

    def create_template(
        self,
        name,
        title,
        description,
        privacy_status="unlisted",
    ):
        values = _validate_template_values(
            name=name,
            title=title,
            description=description,
            privacy_status=privacy_status,
        )
        template_id = uuid.uuid4().hex
        now = _utc_now()
        try:
            with self._connect() as connection:
                connection.execute(
                    """
                    INSERT INTO broadcast_templates (
                        template_id, name, title, description, privacy_status,
                        created_at, updated_at
                    ) VALUES (?, ?, ?, ?, ?, ?, ?)
                    """,
                    (
                        template_id,
                        values["name"],
                        values["title"],
                        values["description"],
                        values["privacy_status"],
                        now,
                        now,
                    ),
                )
        except sqlite3.IntegrityError as exc:
            raise ValueError(
                f"同じ名前の配信テンプレートがすでにあります: {values['name']}"
            ) from exc
        except sqlite3.Error as exc:
            raise RuntimeError("配信テンプレートをSQLiteへ保存できませんでした。") from exc
        return self.get_template(template_id)

    def list_templates(self):
        try:
            with self._connect() as connection:
                rows = connection.execute(
                    """
                    SELECT template_id, name, title, description, privacy_status,
                           created_at, updated_at
                    FROM broadcast_templates
                    ORDER BY name COLLATE NOCASE ASC
                    """
                ).fetchall()
        except sqlite3.Error as exc:
            raise RuntimeError(
                "配信テンプレートをSQLiteから読み込めませんでした。"
            ) from exc
        return [dict(row) for row in rows]

    def get_template(self, template_id):
        normalized_id = _required_text(template_id, "template_id", 64)
        try:
            with self._connect() as connection:
                row = connection.execute(
                    """
                    SELECT template_id, name, title, description, privacy_status,
                           created_at, updated_at
                    FROM broadcast_templates
                    WHERE template_id = ?
                    """,
                    (normalized_id,),
                ).fetchone()
        except sqlite3.Error as exc:
            raise RuntimeError(
                "配信テンプレートをSQLiteから読み込めませんでした。"
            ) from exc
        if row is None:
            raise KeyError(
                f"配信テンプレートが見つかりません: template_id={normalized_id}"
            )
        return dict(row)

    def update_template(
        self,
        template_id,
        *,
        name,
        title,
        description,
        privacy_status,
    ):
        normalized_id = _required_text(template_id, "template_id", 64)
        values = _validate_template_values(
            name=name,
            title=title,
            description=description,
            privacy_status=privacy_status,
        )
        try:
            with self._connect() as connection:
                cursor = connection.execute(
                    """
                    UPDATE broadcast_templates
                    SET name = ?, title = ?, description = ?,
                        privacy_status = ?, updated_at = ?
                    WHERE template_id = ?
                    """,
                    (
                        values["name"],
                        values["title"],
                        values["description"],
                        values["privacy_status"],
                        _utc_now(),
                        normalized_id,
                    ),
                )
        except sqlite3.IntegrityError as exc:
            raise ValueError(
                f"同じ名前の配信テンプレートがすでにあります: {values['name']}"
            ) from exc
        except sqlite3.Error as exc:
            raise RuntimeError("配信テンプレートを更新できませんでした。") from exc
        if cursor.rowcount != 1:
            raise KeyError(
                f"配信テンプレートが見つかりません: template_id={normalized_id}"
            )
        return self.get_template(normalized_id)

    def delete_template(self, template_id):
        normalized_id = _required_text(template_id, "template_id", 64)
        try:
            with self._connect() as connection:
                cursor = connection.execute(
                    "DELETE FROM broadcast_templates WHERE template_id = ?",
                    (normalized_id,),
                )
        except sqlite3.Error as exc:
            raise RuntimeError("配信テンプレートを削除できませんでした。") from exc
        if cursor.rowcount != 1:
            raise KeyError(
                f"配信テンプレートが見つかりません: template_id={normalized_id}"
            )

    def create_schedule(
        self,
        *,
        scheduled_start_at,
        planning_mode,
        content_request,
        title,
        description,
        privacy_status="unlisted",
        auto_start=True,
        template_id=None,
    ):
        values = _validate_schedule_values(
            scheduled_start_at=scheduled_start_at,
            planning_mode=planning_mode,
            content_request=content_request,
            title=title,
            description=description,
            privacy_status=privacy_status,
            auto_start=auto_start,
            template_id=template_id,
        )
        if values["template_id"] is not None:
            self.get_template(values["template_id"])
        schedule_id = uuid.uuid4().hex
        now = _utc_now()
        try:
            with self._connect() as connection:
                connection.execute(
                    """
                    INSERT INTO broadcast_schedules (
                        schedule_id, scheduled_start_at, planning_mode,
                        content_request, prepared_stream_plan, title,
                        description, privacy_status, auto_start, template_id, status,
                        youtube_video_id, last_error, prepared_at,
                        created_at, updated_at
                    ) VALUES (?, ?, ?, ?, NULL, ?, ?, ?, ?, ?, 'draft',
                              NULL, NULL, NULL, ?, ?)
                    """,
                    (
                        schedule_id,
                        values["scheduled_start_at"],
                        values["planning_mode"],
                        values["content_request"],
                        values["title"],
                        values["description"],
                        values["privacy_status"],
                        int(values["auto_start"]),
                        values["template_id"],
                        now,
                        now,
                    ),
                )
        except sqlite3.Error as exc:
            raise RuntimeError("配信予定をSQLiteへ保存できませんでした。") from exc
        return self.get_schedule(schedule_id)

    def list_schedules(self, start_at=None, end_at=None, limit=500):
        normalized_limit = int(limit)
        if not 1 <= normalized_limit <= 1000:
            raise ValueError("配信予定のlimitは1〜1000で指定してください。")
        conditions = []
        parameters = []
        if start_at is not None:
            conditions.append("scheduled_start_at >= ?")
            parameters.append(_normalize_datetime(start_at, "start_at"))
        if end_at is not None:
            conditions.append("scheduled_start_at < ?")
            parameters.append(_normalize_datetime(end_at, "end_at"))
        where_clause = f"WHERE {' AND '.join(conditions)}" if conditions else ""
        parameters.append(normalized_limit)
        try:
            with self._connect() as connection:
                rows = connection.execute(
                    f"""
                    SELECT {_SCHEDULE_COLUMNS}
                    FROM broadcast_schedules
                    {where_clause}
                    ORDER BY scheduled_start_at ASC
                    LIMIT ?
                    """,
                    parameters,
                ).fetchall()
        except sqlite3.Error as exc:
            raise RuntimeError("配信予定をSQLiteから読み込めませんでした。") from exc
        return [_schedule_row_to_dict(row) for row in rows]

    def get_schedule(self, schedule_id):
        normalized_id = _required_text(schedule_id, "schedule_id", 64)
        try:
            with self._connect() as connection:
                row = connection.execute(
                    f"""
                    SELECT {_SCHEDULE_COLUMNS}
                    FROM broadcast_schedules
                    WHERE schedule_id = ?
                    """,
                    (normalized_id,),
                ).fetchone()
        except sqlite3.Error as exc:
            raise RuntimeError("配信予定をSQLiteから読み込めませんでした。") from exc
        if row is None:
            raise KeyError(f"配信予定が見つかりません: schedule_id={normalized_id}")
        return _schedule_row_to_dict(row)

    def update_schedule(self, schedule_id, **changes):
        normalized_id = _required_text(schedule_id, "schedule_id", 64)
        if not changes:
            raise ValueError("更新する配信予定の項目がありません。")
        allowed_fields = {
            "scheduled_start_at",
            "planning_mode",
            "content_request",
            "prepared_stream_plan",
            "title",
            "description",
            "privacy_status",
            "auto_start",
            "template_id",
            "status",
            "youtube_video_id",
            "last_error",
        }
        unknown_fields = set(changes) - allowed_fields
        if unknown_fields:
            raise ValueError(
                "配信予定に未対応の更新項目があります: "
                f"{','.join(sorted(unknown_fields))}"
            )
        normalized_changes = _normalize_schedule_changes(changes)
        current_schedule = self.get_schedule(normalized_id)
        final_mode = normalized_changes.get(
            "planning_mode",
            current_schedule["planning_mode"],
        )
        final_request = normalized_changes.get(
            "content_request",
            current_schedule["content_request"],
        )
        if final_mode == "manual" and not final_request:
            raise ValueError(
                "自分で配信内容を決める場合は、配信内容を入力してください。"
            )
        if normalized_changes.get("template_id") is not None:
            self.get_template(normalized_changes["template_id"])
        plan_input_changed = any(
            field in normalized_changes
            and normalized_changes[field] != current_schedule[field]
            for field in ("planning_mode", "content_request")
        )
        if plan_input_changed:
            # 配信内容を変えた後に古いAI構成を誤って使わないよう破棄します。
            normalized_changes["prepared_stream_plan"] = None
            normalized_changes["prepared_at"] = None
            normalized_changes["status"] = "draft"
            normalized_changes["youtube_video_id"] = None
            normalized_changes["last_error"] = None
        if "prepared_stream_plan" in normalized_changes:
            normalized_changes["prepared_at"] = (
                _utc_now() if normalized_changes["prepared_stream_plan"] else None
            )
        normalized_changes["updated_at"] = _utc_now()
        assignments = ", ".join(f"{field} = ?" for field in normalized_changes)
        parameters = list(normalized_changes.values()) + [normalized_id]
        try:
            with self._connect() as connection:
                cursor = connection.execute(
                    f"UPDATE broadcast_schedules SET {assignments} "
                    "WHERE schedule_id = ?",
                    parameters,
                )
        except sqlite3.Error as exc:
            raise RuntimeError("配信予定を更新できませんでした。") from exc
        if cursor.rowcount != 1:
            raise KeyError(f"配信予定が見つかりません: schedule_id={normalized_id}")
        return self.get_schedule(normalized_id)

    def delete_schedule(self, schedule_id):
        normalized_id = _required_text(schedule_id, "schedule_id", 64)
        try:
            with self._connect() as connection:
                cursor = connection.execute(
                    "DELETE FROM broadcast_schedules WHERE schedule_id = ?",
                    (normalized_id,),
                )
        except sqlite3.Error as exc:
            raise RuntimeError("配信予定を削除できませんでした。") from exc
        if cursor.rowcount != 1:
            raise KeyError(f"配信予定が見つかりません: schedule_id={normalized_id}")


_SCHEDULE_COLUMNS = """
schedule_id, scheduled_start_at, planning_mode, content_request,
prepared_stream_plan, title, description, privacy_status, auto_start, template_id,
status, youtube_video_id, last_error, prepared_at, created_at, updated_at
"""


def get_broadcast_schedule_repository():
    database_path = os.getenv(
        "BROADCAST_SCHEDULE_DB_PATH",
        str(PROJECT_ROOT / "data" / "broadcast_schedule.db"),
    ).strip()
    if not database_path:
        raise RuntimeError("BROADCAST_SCHEDULE_DB_PATHが空です。")
    path = Path(database_path).expanduser()
    if not path.is_absolute():
        path = PROJECT_ROOT / path
    return BroadcastScheduleRepository(path)


def _validate_template_values(*, name, title, description, privacy_status):
    return {
        "name": _required_text(name, "テンプレート名", 80),
        "title": _required_text(title, "配信タイトル", 100),
        "description": _optional_text(description, "配信説明", 5000),
        "privacy_status": _allowed_value(
            privacy_status,
            "公開設定",
            ALLOWED_PRIVACY_STATUSES,
        ),
    }


def _validate_schedule_values(
    *,
    scheduled_start_at,
    planning_mode,
    content_request,
    title,
    description,
    privacy_status,
    auto_start,
    template_id,
):
    normalized_mode = _allowed_value(
        planning_mode,
        "配信内容の決め方",
        ALLOWED_PLANNING_MODES,
    )
    normalized_request = _optional_text(
        content_request,
        "配信内容の希望",
        200,
    )
    if normalized_mode == "manual" and not normalized_request:
        raise ValueError("自分で配信内容を決める場合は、配信内容を入力してください。")
    return {
        "scheduled_start_at": _normalize_datetime(
            scheduled_start_at,
            "配信開始日時",
        ),
        "planning_mode": normalized_mode,
        "content_request": normalized_request,
        "title": _required_text(title, "配信タイトル", 100),
        "description": _optional_text(description, "配信説明", 5000),
        "privacy_status": _allowed_value(
            privacy_status,
            "公開設定",
            ALLOWED_PRIVACY_STATUSES,
        ),
        "auto_start": _normalize_bool(auto_start, "予定時刻の自動開始"),
        "template_id": (
            _required_text(template_id, "template_id", 64)
            if template_id is not None
            else None
        ),
    }


def _normalize_schedule_changes(changes):
    normalized = {}
    for field, value in changes.items():
        if field == "scheduled_start_at":
            normalized[field] = _normalize_datetime(value, "配信開始日時")
        elif field == "planning_mode":
            normalized[field] = _allowed_value(
                value,
                "配信内容の決め方",
                ALLOWED_PLANNING_MODES,
            )
        elif field == "content_request":
            normalized[field] = _optional_text(value, "配信内容の希望", 200)
        elif field == "prepared_stream_plan":
            normalized[field] = _nullable_text(value, "配信構成表", 12000)
        elif field == "title":
            normalized[field] = _required_text(value, "配信タイトル", 100)
        elif field == "description":
            normalized[field] = _optional_text(value, "配信説明", 5000)
        elif field == "privacy_status":
            normalized[field] = _allowed_value(
                value,
                "公開設定",
                ALLOWED_PRIVACY_STATUSES,
            )
        elif field == "auto_start":
            normalized[field] = _normalize_bool(value, "予定時刻の自動開始")
        elif field == "template_id":
            normalized[field] = (
                _required_text(value, "template_id", 64)
                if value is not None
                else None
            )
        elif field == "status":
            normalized[field] = _allowed_value(
                value,
                "配信予定の状態",
                ALLOWED_SCHEDULE_STATUSES,
            )
        elif field == "youtube_video_id":
            normalized[field] = _nullable_text(value, "YouTube動画ID", 64)
        elif field == "last_error":
            normalized[field] = _nullable_text(value, "配信予定のエラー", 1000)
    return normalized


def _normalize_datetime(value, label):
    if isinstance(value, datetime):
        parsed = value
    else:
        normalized = str(value or "").strip()
        if normalized.endswith("Z"):
            normalized = f"{normalized[:-1]}+00:00"
        try:
            parsed = datetime.fromisoformat(normalized)
        except ValueError as exc:
            raise ValueError(f"{label}はISO 8601形式で指定してください。") from exc
    if parsed.tzinfo is None:
        raise ValueError(f"{label}にはタイムゾーンを含めてください。")
    return parsed.astimezone(timezone.utc).isoformat()


def _normalize_bool(value, label):
    if isinstance(value, bool):
        return value
    if value in {0, 1}:
        return bool(value)
    raise ValueError(f"{label}はtrueまたはfalseで指定してください。")


def _schedule_row_to_dict(row):
    schedule = dict(row)
    schedule["auto_start"] = bool(schedule["auto_start"])
    return schedule


def _required_text(value, label, max_length):
    normalized = str(value or "").strip()
    if not normalized:
        raise ValueError(f"{label}が空です。")
    if len(normalized) > max_length:
        raise ValueError(f"{label}は{max_length}文字以内で指定してください。")
    return normalized


def _optional_text(value, label, max_length):
    normalized = str(value or "").strip()
    if len(normalized) > max_length:
        raise ValueError(f"{label}は{max_length}文字以内で指定してください。")
    return normalized


def _nullable_text(value, label, max_length):
    if value is None:
        return None
    normalized = _optional_text(value, label, max_length)
    return normalized or None


def _allowed_value(value, label, allowed_values):
    normalized = str(value or "").strip()
    if normalized not in allowed_values:
        raise ValueError(
            f"{label}が不正です: {normalized or '(空)'} / "
            f"使用可能={','.join(sorted(allowed_values))}"
        )
    return normalized


def _utc_now():
    return datetime.now(timezone.utc).isoformat()
