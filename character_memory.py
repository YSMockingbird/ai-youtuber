import os
import sqlite3
import uuid
from datetime import datetime, timedelta, timezone
from pathlib import Path

from local_storage import secure_sqlite_storage
from llm.config import PROJECT_ROOT
from llm.memory import is_safe_memory_content


ALLOWED_CHARACTER_MEMORY_CATEGORIES = {
    "episode",
    "relationship",
    "belief_change",
}
ALLOWED_CHARACTER_MEMORY_STATUSES = {"draft", "approved", "rejected"}


class CharacterMemoryRepository:
    def __init__(
        self,
        database_path,
        max_drafts=200,
        rejected_retention_days=30,
        now=None,
    ):
        self.database_path = Path(database_path)
        self.max_drafts = int(max_drafts)
        self.rejected_retention_days = int(rejected_retention_days)
        if not 1 <= self.max_drafts <= 2000:
            raise ValueError("キャラクター記憶の下書き保持数は1〜2000件にしてください。")
        if not 1 <= self.rejected_retention_days <= 3650:
            raise ValueError("却下済み記憶の保持日数は1〜3650日にしてください。")
        self.now = now or (lambda: datetime.now(timezone.utc))
        secure_sqlite_storage(self.database_path)
        self._initialize()
        self._cleanup()
        secure_sqlite_storage(self.database_path)

    def _connect(self):
        return sqlite3.connect(str(self.database_path))

    def _initialize(self):
        try:
            with self._connect() as connection:
                connection.execute(
                    """
                    CREATE TABLE IF NOT EXISTS character_memories (
                        memory_id TEXT PRIMARY KEY,
                        content TEXT NOT NULL UNIQUE,
                        category TEXT NOT NULL,
                        status TEXT NOT NULL,
                        importance REAL NOT NULL,
                        source TEXT NOT NULL,
                        created_at TEXT NOT NULL,
                        reviewed_at TEXT,
                        last_used_at TEXT NOT NULL
                    )
                    """
                )
                connection.execute(
                    "CREATE INDEX IF NOT EXISTS idx_character_memories_status "
                    "ON character_memories(status, importance DESC, last_used_at DESC)"
                )
        except sqlite3.Error as exc:
            raise RuntimeError(
                "キャラクター記憶データベースを初期化できません: "
                f"{self.database_path}"
            ) from exc

    def save_draft(self, content, category, importance, source):
        normalized_content = str(content).strip()
        normalized_source = str(source).strip()
        if not is_safe_memory_content(normalized_content):
            raise ValueError("キャラクター記憶の内容に保存できない情報が含まれます。")
        if category not in ALLOWED_CHARACTER_MEMORY_CATEGORIES:
            raise ValueError(f"キャラクター記憶のcategoryが不正です: {category}")
        if not 0 <= float(importance) <= 1:
            raise ValueError(
                "キャラクター記憶のimportanceは0.0〜1.0で指定してください。"
            )
        if not normalized_source:
            raise ValueError("キャラクター記憶のsourceが空です。")

        now_datetime = self._current_time()
        now = now_datetime.isoformat()
        memory_id = uuid.uuid4().hex
        try:
            with self._connect() as connection:
                connection.execute(
                    """
                    INSERT INTO character_memories (
                        memory_id, content, category, status, importance,
                        source, created_at, reviewed_at, last_used_at
                    ) VALUES (?, ?, ?, 'draft', ?, ?, ?, NULL, ?)
                    ON CONFLICT(content) DO UPDATE SET
                        importance = MAX(character_memories.importance, excluded.importance),
                        category = CASE
                            WHEN character_memories.status = 'draft'
                            THEN excluded.category
                            ELSE character_memories.category
                        END,
                        source = CASE
                            WHEN character_memories.status = 'draft'
                            THEN excluded.source
                            ELSE character_memories.source
                        END
                    """,
                    (
                        memory_id,
                        normalized_content[:300],
                        category,
                        float(importance),
                        normalized_source[:80],
                        now,
                        now,
                    ),
                )
                self._prune(connection, now_datetime)
        except sqlite3.Error as exc:
            raise RuntimeError("キャラクター記憶の下書きを保存できませんでした。") from exc

    def list(self, status="draft", limit=100):
        if status not in ALLOWED_CHARACTER_MEMORY_STATUSES:
            raise ValueError(f"キャラクター記憶のstatusが不正です: {status}")
        if not 1 <= int(limit) <= 500:
            raise ValueError("キャラクター記憶のlimitは1〜500で指定してください。")
        try:
            with self._connect() as connection:
                self._prune(connection, self._current_time())
                rows = connection.execute(
                    """
                    SELECT memory_id, content, category, status, importance,
                           source, created_at, reviewed_at, last_used_at
                    FROM character_memories
                    WHERE status = ?
                    ORDER BY importance DESC, created_at DESC
                    LIMIT ?
                    """,
                    (status, int(limit)),
                ).fetchall()
        except sqlite3.Error as exc:
            raise RuntimeError("キャラクター記憶をSQLiteから読み込めませんでした。") from exc
        return [self._row_to_memory(row) for row in rows]

    def find_relevant_approved(self, query, limit=1):
        if not 1 <= int(limit) <= 10:
            raise ValueError("参照するキャラクター記憶のlimitは1〜10で指定してください。")
        query_terms = _search_terms(query)
        memories = self.list("approved", limit=500)
        ranked = []
        for memory in memories:
            overlap = len(query_terms & _search_terms(memory["content"]))
            score = memory["importance"] + min(overlap * 0.25, 1.0)
            ranked.append((score, memory))
        ranked.sort(key=lambda item: item[0], reverse=True)
        selected = [memory for _, memory in ranked[: int(limit)]]
        if selected:
            now = self._current_time().isoformat()
            try:
                with self._connect() as connection:
                    connection.executemany(
                        "UPDATE character_memories SET last_used_at = ? "
                        "WHERE memory_id = ?",
                        [(now, memory["memory_id"]) for memory in selected],
                    )
            except sqlite3.Error as exc:
                raise RuntimeError(
                    "キャラクター記憶の利用日時を更新できませんでした。"
                ) from exc
        return selected

    def review(self, memory_id, status):
        normalized_memory_id = str(memory_id).strip()
        if not normalized_memory_id:
            raise ValueError("キャラクター記憶のmemory_idが空です。")
        if status not in {"approved", "rejected"}:
            raise ValueError("キャラクター記憶はapprovedまたはrejectedにしてください。")
        now_datetime = self._current_time()
        now = now_datetime.isoformat()
        try:
            with self._connect() as connection:
                self._prune(connection, now_datetime)
                cursor = connection.execute(
                    """
                    UPDATE character_memories
                    SET status = ?, reviewed_at = ?
                    WHERE memory_id = ? AND status = 'draft'
                    """,
                    (status, now, normalized_memory_id),
                )
        except sqlite3.Error as exc:
            raise RuntimeError("キャラクター記憶の審査結果を保存できませんでした。") from exc
        if cursor.rowcount != 1:
            raise RuntimeError(
                "下書き状態のキャラクター記憶が見つかりません: "
                f"memory_id={normalized_memory_id}"
            )

    def _cleanup(self):
        try:
            with self._connect() as connection:
                self._prune(connection, self._current_time())
        except sqlite3.Error as exc:
            raise RuntimeError("キャラクター記憶を整理できませんでした。") from exc

    def _prune(self, connection, current_time):
        rejected_cutoff = current_time - timedelta(
            days=self.rejected_retention_days
        )
        connection.execute(
            "DELETE FROM character_memories "
            "WHERE status = 'rejected' AND reviewed_at < ?",
            (rejected_cutoff.isoformat(),),
        )
        connection.execute(
            """
            DELETE FROM character_memories
            WHERE status = 'draft' AND memory_id NOT IN (
                SELECT memory_id
                FROM character_memories
                WHERE status = 'draft'
                ORDER BY importance DESC, created_at DESC
                LIMIT ?
            )
            """,
            (self.max_drafts,),
        )

    def _current_time(self):
        current_time = self.now()
        if current_time.tzinfo is None:
            current_time = current_time.replace(tzinfo=timezone.utc)
        return current_time.astimezone(timezone.utc)

    @staticmethod
    def _row_to_memory(row):
        return {
            "memory_id": row[0],
            "content": row[1],
            "category": row[2],
            "status": row[3],
            "importance": float(row[4]),
            "source": row[5],
            "created_at": row[6],
            "reviewed_at": row[7],
            "last_used_at": row[8],
        }


def get_character_memory_repository():
    database_path = os.getenv(
        "CHARACTER_MEMORY_DB_PATH",
        str(PROJECT_ROOT / "data" / "character_memory.db"),
    ).strip()
    if not database_path:
        raise RuntimeError("CHARACTER_MEMORY_DB_PATHが空です。")
    return CharacterMemoryRepository(Path(database_path))


def _search_terms(text):
    normalized = "".join(str(text or "").lower().split())
    terms = set()
    for index in range(max(len(normalized) - 1, 0)):
        terms.add(normalized[index : index + 2])
    return terms
