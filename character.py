from character_lore import build_public_character_context
from llm.config import load_character_prompt


# 既存コードとの互換性を保ちながら、人格設定本体は外部ファイルで管理します。
CHARACTER_PROMPT = (
    f"{load_character_prompt()}\n\n{build_public_character_context()}"
)
