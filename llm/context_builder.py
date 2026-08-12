import math
import re


def estimate_tokens(text):
    # プロバイダー未確定のため、日本語を多めに見積もる保守的な近似値を使います。
    value = str(text or "")
    japanese_characters = len(
        re.findall(r"[ぁ-んァ-ヶ一-龠々ー]", value)
    )
    other_characters = max(len(value) - japanese_characters, 0)
    return max(1, math.ceil(japanese_characters * 1.1 + other_characters / 4))


def _take_lines_within_budget(lines, token_budget, newest_first=False):
    source = list(reversed(lines)) if newest_first else list(lines)
    selected = []
    used_tokens = 0
    for line in source:
        line_tokens = estimate_tokens(line)
        if used_tokens + line_tokens > token_budget:
            continue
        selected.append(line)
        used_tokens += line_tokens
    if newest_first:
        selected.reverse()
    return selected, used_tokens


class ContextBuilder:
    def __init__(self, character_prompt, config, conversation, memory_repository):
        self.character_prompt = character_prompt
        self.config = config
        self.conversation = conversation
        self.memory_repository = memory_repository

    def build(self, current_input, user_id="", include_memories=True):
        context_config = self.config.get("context", {})
        total_budget = int(context_config.get("total_token_budget", 2400))
        section_overhead_tokens = estimate_tokens(
            "[Relevant Memories]\n[Stream Summary]\n"
            "[Recent Conversation]\n[Current Input]\n"
        )
        system_prompt_tokens = estimate_tokens(self.character_prompt)
        current_input_tokens = estimate_tokens(current_input)
        mandatory_tokens = (
            system_prompt_tokens
            + current_input_tokens
            + section_overhead_tokens
        )
        if mandatory_tokens >= total_budget:
            raise RuntimeError(
                "System Promptと今回の入力だけでコンテキスト予算を超えました。"
                f" system_prompt_tokens={system_prompt_tokens}"
                f" current_input_tokens={current_input_tokens}"
                f" overhead_tokens={section_overhead_tokens}"
                f" total_tokens={mandatory_tokens} budget={total_budget}"
            )

        remaining_budget = total_budget - mandatory_tokens
        recent_budget = min(
            int(context_config.get("recent_conversation_token_budget", 700)),
            remaining_budget,
        )
        recent_lines, used_recent = _take_lines_within_budget(
            self.conversation.recent_lines(),
            recent_budget,
            newest_first=True,
        )
        remaining_budget -= used_recent

        memory_lines = []
        if include_memories and self.memory_repository is not None and user_id:
            memory_limit = int(context_config.get("relevant_memory_count", 5))
            memories = self.memory_repository.find_relevant(
                user_id,
                current_input,
                memory_limit,
            )
            raw_memory_lines = [f"- {memory['content']}" for memory in memories]
            memory_budget = min(
                int(context_config.get("relevant_memory_token_budget", 300)),
                remaining_budget,
            )
            memory_lines, used_memory = _take_lines_within_budget(
                raw_memory_lines,
                memory_budget,
            )
            remaining_budget -= used_memory

        summary_lines = []
        if self.conversation.summary and remaining_budget > 0:
            summary_budget = min(
                int(context_config.get("stream_summary_token_budget", 300)),
                remaining_budget,
            )
            summary_lines, _ = _take_lines_within_budget(
                self.conversation.summary.splitlines(),
                summary_budget,
                newest_first=True,
            )

        sections = []
        if memory_lines:
            sections.append("[Relevant Memories]\n" + "\n".join(memory_lines))
        if summary_lines:
            sections.append("[Stream Summary]\n" + "\n".join(summary_lines))
        if recent_lines:
            sections.append("[Recent Conversation]\n" + "\n".join(recent_lines))
        sections.append("[Current Input]\n" + current_input)
        prompt = "\n\n".join(sections)
        actual_tokens = estimate_tokens(self.character_prompt) + estimate_tokens(
            prompt
        )
        if actual_tokens > total_budget:
            raise RuntimeError(
                "構築したコンテキストがトークン予算を超えました。"
                f" system_prompt_tokens={system_prompt_tokens}"
                f" current_input_tokens={current_input_tokens}"
                f" recent_tokens={used_recent}"
                f" total_tokens={actual_tokens} budget={total_budget}"
            )
        return prompt
