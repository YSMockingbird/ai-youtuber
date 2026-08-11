import re
import sqlite3
import uuid
from abc import ABC, abstractmethod
from datetime import datetime, timezone
from pathlib import Path


ALLOWED_MEMORY_CATEGORIES = {
    "preference",
    "event",
    "relationship",
    "profile",
    "stream_event",
}
SENSITIVE_MEMORY_TERMS = {
    "住所",
    "電話番号",
    "メールアドレス",
    "本名",
    "病気",
    "診断",
    "宗教",
    "政党",
    "借金",
    "年収",
    "口座",
    "クレジットカード",
    "性的",
}


class MemoryRepository(ABC):
    @abstractmethod
    def save(self, user_id, user_name, content, category, importance):
        """長期記憶を保存します。"""

    @abstractmethod
    def find_relevant(self, user_id, query, limit):
        """今回の入力に関連する記憶だけを返します。"""


class SQLiteMemoryRepository(MemoryRepository):
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
                    CREATE TABLE IF NOT EXISTS memories (
                        memory_id TEXT PRIMARY KEY,
                        user_id TEXT NOT NULL,
                        user_name TEXT NOT NULL,
                        content TEXT NOT NULL,
                        category TEXT NOT NULL,
                        importance REAL NOT NULL,
                        created_at TEXT NOT NULL,
                        last_used_at TEXT NOT NULL,
                        UNIQUE(user_id, content)
                    )
                    """
                )
                connection.execute(
                    "CREATE INDEX IF NOT EXISTS idx_memories_user "
                    "ON memories(user_id, importance DESC, last_used_at DESC)"
                )
        except sqlite3.Error as exc:
            raise RuntimeError(
                f"長期記憶データベースを初期化できません: {self.database_path}"
            ) from exc

    def save(self, user_id, user_name, content, category, importance):
        normalized_user_id = str(user_id).strip()
        normalized_content = str(content).strip()
        if not normalized_user_id:
            raise ValueError("長期記憶のuser_idが空です。")
        if not normalized_content:
            raise ValueError("長期記憶のcontentが空です。")
        if category not in ALLOWED_MEMORY_CATEGORIES:
            raise ValueError(f"長期記憶のcategoryが不正です: {category}")
        if not 0 <= float(importance) <= 1:
            raise ValueError("長期記憶のimportanceは0.0〜1.0で指定してください。")

        now = datetime.now(timezone.utc).isoformat()
        try:
            with self._connect() as connection:
                connection.execute(
                    """
                    INSERT INTO memories (
                        memory_id, user_id, user_name, content, category,
                        importance, created_at, last_used_at
                    ) VALUES (?, ?, ?, ?, ?, ?, ?, ?)
                    ON CONFLICT(user_id, content) DO UPDATE SET
                        user_name = excluded.user_name,
                        category = excluded.category,
                        importance = MAX(memories.importance, excluded.importance)
                    """,
                    (
                        uuid.uuid4().hex,
                        normalized_user_id,
                        str(user_name).strip(),
                        normalized_content[:300],
                        category,
                        float(importance),
                        now,
                        now,
                    ),
                )
        except sqlite3.Error as exc:
            raise RuntimeError("長期記憶をSQLiteへ保存できませんでした。") from exc

    def find_relevant(self, user_id, query, limit):
        normalized_user_id = str(user_id or "").strip()
        if not normalized_user_id or limit <= 0:
            return []
        try:
            with self._connect() as connection:
                rows = connection.execute(
                    """
                    SELECT memory_id, content, category, importance, last_used_at
                    FROM memories
                    WHERE user_id = ?
                    ORDER BY importance DESC, last_used_at DESC
                    LIMIT 50
                    """,
                    (normalized_user_id,),
                ).fetchall()
        except sqlite3.Error as exc:
            raise RuntimeError("長期記憶をSQLiteから読み込めませんでした。") from exc

        query_terms = _search_terms(query)
        ranked = []
        for memory_id, content, category, importance, last_used_at in rows:
            content_terms = _search_terms(content)
            overlap = len(query_terms & content_terms)
            score = float(importance) + min(overlap * 0.25, 1.0)
            ranked.append(
                (
                    score,
                    {
                        "memory_id": memory_id,
                        "content": content,
                        "category": category,
                        "importance": float(importance),
                        "last_used_at": last_used_at,
                    },
                )
            )
        ranked.sort(key=lambda entry: entry[0], reverse=True)
        selected = [item for _, item in ranked[:limit]]
        if selected:
            now = datetime.now(timezone.utc).isoformat()
            try:
                with self._connect() as connection:
                    connection.executemany(
                        "UPDATE memories SET last_used_at = ? WHERE memory_id = ?",
                        [(now, item["memory_id"]) for item in selected],
                    )
            except sqlite3.Error as exc:
                raise RuntimeError("長期記憶の利用日時を更新できませんでした。") from exc
        return selected


def _search_terms(text):
    normalized = re.sub(r"\s+", "", str(text).lower())
    terms = set(re.findall(r"[a-z0-9]{2,}|[ぁ-んァ-ヶ一-龠]{2,}", normalized))
    terms.update(
        normalized[index : index + 2]
        for index in range(max(len(normalized) - 1, 0))
    )
    return terms


def is_safe_memory_content(content):
    normalized = str(content).strip().lower()
    if not normalized or len(normalized) > 300:
        return False
    return not any(term.lower() in normalized for term in SENSITIVE_MEMORY_TERMS)
