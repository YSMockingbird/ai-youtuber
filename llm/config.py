import json
from pathlib import Path


PROJECT_ROOT = Path(__file__).resolve().parent.parent
DEFAULT_CONFIG_PATH = PROJECT_ROOT / "config" / "llm_config.json"
DEFAULT_CHARACTER_PROMPT_PATH = PROJECT_ROOT / "config" / "character_prompt.txt"


def load_llm_config(path=None):
    config_path = Path(path) if path else DEFAULT_CONFIG_PATH
    try:
        with config_path.open("r", encoding="utf-8") as config_file:
            config = json.load(config_file)
    except FileNotFoundError as exc:
        raise RuntimeError(f"LLM設定ファイルが見つかりません: {config_path}") from exc
    except json.JSONDecodeError as exc:
        raise RuntimeError(
            f"LLM設定ファイルをJSONとして読み取れません: {config_path}"
        ) from exc
    if not isinstance(config, dict):
        raise RuntimeError("LLM設定ファイルのルートはJSONオブジェクトにしてください。")
    return config


def load_character_prompt(path=None):
    prompt_path = Path(path) if path else DEFAULT_CHARACTER_PROMPT_PATH
    try:
        prompt = prompt_path.read_text(encoding="utf-8").strip()
    except FileNotFoundError as exc:
        raise RuntimeError(
            f"キャラクタープロンプトが見つかりません: {prompt_path}"
        ) from exc
    if not prompt:
        raise RuntimeError("キャラクタープロンプトが空です。")
    return prompt
