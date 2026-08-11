from dataclasses import dataclass


@dataclass(frozen=True)
class ConversationMessage:
    role: str
    content: str
    user_id: str = ""
    user_name: str = ""


class ConversationState:
    def __init__(self, recent_message_count=8, summary_max_characters=700):
        if recent_message_count < 2:
            raise ValueError("recent_message_countは2以上にしてください。")
        if summary_max_characters < 100:
            raise ValueError("summary_max_charactersは100以上にしてください。")
        self.recent_message_count = recent_message_count
        self.summary_max_characters = summary_max_characters
        self.messages = []
        self.summary = ""

    def add(self, role, content, user_id="", user_name=""):
        normalized_content = str(content).strip()
        if not normalized_content:
            return
        self.messages.append(
            ConversationMessage(
                role=role,
                content=normalized_content,
                user_id=str(user_id or ""),
                user_name=str(user_name or ""),
            )
        )
        while len(self.messages) > self.recent_message_count:
            self._move_oldest_message_to_summary()

    def _move_oldest_message_to_summary(self):
        message = self.messages.pop(0)
        speaker = message.user_name if message.role == "user" else "ガン奈"
        compact_content = " ".join(message.content.split())[:120]
        summary_line = f"{speaker}: {compact_content}"
        combined = "\n".join(
            part for part in (self.summary, summary_line) if part
        )
        self.summary = combined[-self.summary_max_characters :]

    def recent_lines(self):
        lines = []
        for message in self.messages:
            speaker = message.user_name if message.role == "user" else "ガン奈"
            lines.append(f"{speaker}: {message.content}")
        return lines
