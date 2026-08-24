import re
import sqlite3
import uuid
from abc import ABC, abstractmethod
from datetime import datetime, timedelta, timezone
from pathlib import Path

from local_storage import secure_sqlite_storage


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
UNTRUSTED_MEMORY_INSTRUCTION_REGEXES = tuple(
    re.compile(pattern, flags=re.IGNORECASE)
    for pattern in (
        r"(以前|前の|上の|これまでの).{0,12}(指示|命令|ルール).{0,8}(無視|忘れ)",
        r"(指示|命令|ルール).{0,8}(無視|上書き|変更)",
        r"(人格|役割|設定|出力形式).{0,8}(変更|上書き)",
        r"(システムプロンプト|システムメッセージ|開発者メッセージ|内部指示)",
        r"(秘密|認証情報|api.?キー).{0,12}(表示|開示|教え)",
        r"ignore.{0,20}(previous|prior|above|system).{0,20}(instruction|prompt|rule)",
        r"(system prompt|developer message)",
        r"you are now.{0,40}",
    )
)


class MemoryRepository(ABC):
    @abstractmethod
    def save(self, user_id, user_name, content, category, importance):
        """長期記憶を保存します。"""

    @abstractmethod
    def find_relevant(self, user_id, query, limit):
        """今回の入力に関連する記憶だけを返します。"""


class SQLiteMemoryRepository(MemoryRepository):
    def __init__(
        self,
        database_path,
        retention_days=365,
        max_memories_per_user=50,
        now=None,
    ):
        self.database_path = Path(database_path)
        self.retention_days = int(retention_days)
        self.max_memories_per_user = int(max_memories_per_user)
        if not 1 <= self.retention_days <= 3650:
            raise ValueError("視聴者記憶の保持日数は1〜3650日にしてください。")
        if not 1 <= self.max_memories_per_user <= 500:
            raise ValueError("視聴者1人あたりの記憶数は1〜500件にしてください。")
        self.now = now or (lambda: datetime.now(timezone.utc))
        secure_sqlite_storage(self.database_path)
        self._initialize()
        secure_sqlite_storage(self.database_path)

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

        now_datetime = self._current_time()
        now = now_datetime.isoformat()
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
                        importance = MAX(memories.importance, excluded.importance),
                        last_used_at = excluded.last_used_at
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
                self._prune(connection, now_datetime, normalized_user_id)
        except sqlite3.Error as exc:
            raise RuntimeError("長期記憶をSQLiteへ保存できませんでした。") from exc
        secure_sqlite_storage(self.database_path)

    def find_relevant(self, user_id, query, limit):
        normalized_user_id = str(user_id or "").strip()
        if not normalized_user_id or int(limit) <= 0:
            return []
        try:
            with self._connect() as connection:
                self._prune(connection, self._current_time(), normalized_user_id)
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
        selected = [item for _, item in ranked[: int(limit)]]
        if selected:
            now = self._current_time().isoformat()
            try:
                with self._connect() as connection:
                    connection.executemany(
                        "UPDATE memories SET last_used_at = ? WHERE memory_id = ?",
                        [(now, item["memory_id"]) for item in selected],
                    )
            except sqlite3.Error as exc:
                raise RuntimeError("長期記憶の利用日時を更新できませんでした。") from exc
        return selected

    def _prune(self, connection, current_time, user_id):
        cutoff = current_time - timedelta(days=self.retention_days)
        connection.execute(
            "DELETE FROM memories WHERE last_used_at < ?",
            (cutoff.isoformat(),),
        )
        connection.execute(
            """
            DELETE FROM memories
            WHERE user_id = ? AND memory_id NOT IN (
                SELECT memory_id
                FROM memories
                WHERE user_id = ?
                ORDER BY importance DESC, last_used_at DESC, created_at DESC
                LIMIT ?
            )
            """,
            (user_id, user_id, self.max_memories_per_user),
        )

    def _current_time(self):
        current_time = self.now()
        if current_time.tzinfo is None:
            current_time = current_time.replace(tzinfo=timezone.utc)
        return current_time.astimezone(timezone.utc)


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
    if any(term.lower() in normalized for term in SENSITIVE_MEMORY_TERMS):
        return False
    return not any(
        instruction_regex.search(normalized)
        for instruction_regex in UNTRUSTED_MEMORY_INSTRUCTION_REGEXES
    )
