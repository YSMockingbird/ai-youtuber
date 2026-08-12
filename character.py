from llm.config import load_character_prompt


# 既存コードとの互換性を保ちながら、人格設定本体は外部ファイルで管理します。
CHARACTER_PROMPT = load_character_prompt()
