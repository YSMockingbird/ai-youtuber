import os
import sqlite3
import uuid
from datetime import datetime, timezone
from pathlib import Path

from llm.config import PROJECT_ROOT
from llm.memory import is_safe_memory_content


ALLOWED_CHARACTER_MEMORY_CATEGORIES = {
    "episode",
    "relationship",
    "belief_change",
}
ALLOWED_CHARACTER_MEMORY_STATUSES = {"draft", "approved", "rejected"}


class CharacterMemoryRepository:
    def __init__(self, database_path):
        self.database_path = Path(database_path)
        self.database_path.parent.mkdir(parents=True, exist_ok=True)
        self._initialize()

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

        now = datetime.now(timezone.utc).isoformat()
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
        except sqlite3.Error as exc:
            raise RuntimeError("キャラクター記憶の下書きを保存できませんでした。") from exc

    def list(self, status="draft", limit=100):
        if status not in ALLOWED_CHARACTER_MEMORY_STATUSES:
            raise ValueError(f"キャラクター記憶のstatusが不正です: {status}")
        if not 1 <= int(limit) <= 500:
            raise ValueError("キャラクター記憶のlimitは1〜500で指定してください。")
        try:
            with self._connect() as connection:
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
            now = datetime.now(timezone.utc).isoformat()
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
        now = datetime.now(timezone.utc).isoformat()
        try:
            with self._connect() as connection:
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
