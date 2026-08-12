import os
from pathlib import Path

from character import CHARACTER_PROMPT
from character_memory import get_character_memory_repository
from llm.config import PROJECT_ROOT, load_llm_config
from llm.context_builder import ContextBuilder
from llm.conversation import ConversationState
from llm.memory import SQLiteMemoryRepository, is_safe_memory_content


class StreamContextManager:
    def __init__(
        self,
        config=None,
        memory_repository=None,
        character_memory_repository=None,
    ):
        self.config = config or load_llm_config()
        context_config = self.config.get("context", {})
        self.conversation = ConversationState(
            recent_message_count=int(
                context_config.get("recent_message_count", 8)
            ),
            summary_max_characters=int(
                context_config.get("summary_max_characters", 700)
            ),
        )
        if memory_repository is None:
            database_path = os.getenv(
                "MEMORY_DB_PATH",
                str(PROJECT_ROOT / "data" / "memory.db"),
            ).strip()
            if not database_path:
                raise RuntimeError("MEMORY_DB_PATHが空です。")
            memory_repository = SQLiteMemoryRepository(Path(database_path))
        self.memory_repository = memory_repository
        self.character_memory_repository = (
            character_memory_repository or get_character_memory_repository()
        )
        self.context_builder = ContextBuilder(
            character_prompt=CHARACTER_PROMPT,
            config=self.config,
            conversation=self.conversation,
            memory_repository=self.memory_repository,
        )

    def record_comment_exchange(
        self,
        user_id,
        user_name,
        comment,
        ai_response,
    ):
        self.conversation.add(
            "user",
            comment,
            user_id=user_id,
            user_name=user_name,
        )
        self.conversation.add("assistant", ai_response["text"])
        self._save_memory_candidate(
            user_id,
            user_name,
            ai_response.get("memory_candidate"),
        )
        self._save_character_event_candidate(
            ai_response.get("character_event_candidate"),
            source="comment_reply",
        )

    def record_ai_speech(
        self,
        text,
        character_event_candidate=None,
        source="autonomous_speech",
    ):
        self.conversation.add("assistant", text)
        self._save_character_event_candidate(
            character_event_candidate,
            source=source,
        )

    def _save_memory_candidate(self, user_id, user_name, candidate):
        if not candidate or not str(user_id or "").strip():
            return False
        minimum_importance = float(
            self.config.get("memory", {}).get("minimum_importance", 0.65)
        )
        importance = float(candidate.get("importance", 0))
        content = str(candidate.get("content", "")).strip()
        if importance < minimum_importance or not is_safe_memory_content(content):
            return False
        self.memory_repository.save(
            user_id=user_id,
            user_name=user_name,
            content=content,
            category=candidate.get("category", "profile"),
            importance=importance,
        )
        return True

    def _save_character_event_candidate(self, candidate, source):
        if not candidate:
            return False
        minimum_importance = float(
            self.config.get("character_memory", {}).get(
                "minimum_importance",
                0.65,
            )
        )
        importance = float(candidate.get("importance", 0))
        if importance < minimum_importance:
            return False
        self.character_memory_repository.save_draft(
            content=candidate.get("content", ""),
            category=candidate.get("category", "episode"),
            importance=importance,
            source=source,
        )
        return True
