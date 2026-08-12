from dataclasses import dataclass, field

from pydantic import BaseModel, ConfigDict, Field

from llm.client import create_llm_client
from llm.config import load_llm_config


class StreamThemePlan(BaseModel):
    model_config = ConfigDict(extra="forbid")

    theme: str = Field(min_length=5, max_length=120)
    core_question: str = Field(min_length=5, max_length=160)
    opening_angle: str = Field(min_length=5, max_length=160)


def generate_stream_theme_plan(previous_state=None):
    # 配信開始時とテーマ見直し時だけLLMを呼び、発話とは別にテーマを設計します。
    previous_context = "なし"
    if previous_state is not None:
        covered_points = "\n".join(
            f"- {point}" for point in previous_state.covered_points[-8:]
        ) or "- なし"
        previous_context = (
            f"前のメインテーマ: {previous_state.main_theme}\n"
            f"中心となる問い: {previous_state.core_question}\n"
            f"扱った論点:\n{covered_points}"
        )

    input_text = (
        "AI YouTuberの配信で使う、雑談を10〜15発話ほど展開できるメインテーマを一つ設計してください。\n"
        "ニュース、雑学、身近な観察、インターネット文化、AI、ゲームなどへ自然に枝分かれできるテーマにしてください。\n"
        "キャラクターは世界平和を目的に活動し、デスメタル、ギャンブル、"
        "深夜のインターネットなどのアングラ文化を好みます。"
        "これらは毎回必須ではありませんが、独特な視点を作れる場合は活用してください。\n"
        "専門知識や最新情報がなければ話せない狭すぎるテーマ、単なる一問一答、視聴者がいる前提の企画は避けてください。\n"
        "前のテーマがまだ別の角度から発展できるなら、同じテーマを継続しても構いません。"
        "内容が重複しそうなら、前の話から自然につながる新しいテーマに切り替えてください。\n\n"
        f"前回までの状態:\n{previous_context}"
    )
    client = create_llm_client(load_llm_config())
    return client.generate_structured(
        instructions=(
            "あなたは長時間のAI配信を構成する日本語の番組作家です。"
            "発話本文ではなく、配信テーマの設計情報だけを返してください。"
        ),
        input_text=input_text,
        response_model=StreamThemePlan,
        max_output_tokens=500,
        request_label="stream_theme",
    )


@dataclass
class StreamThemeState:
    main_theme: str
    core_question: str
    current_focus: str
    last_speech: str = ""
    covered_points: list = field(default_factory=list)
    tangent_topic: str = ""
    utterance_count: int = 0
    utterances_since_review: int = 0


class StreamThemeManager:
    def __init__(
        self,
        config,
        manual_theme=None,
        plan_generator=generate_stream_theme_plan,
    ):
        theme_config = config.get("stream_theme", {})
        self.review_utterance_count = int(
            theme_config.get("review_utterance_count", 20)
        )
        if not 5 <= self.review_utterance_count <= 30:
            raise ValueError(
                "stream_theme.review_utterance_countは5〜30で設定してください。"
            )
        self.plan_generator = plan_generator
        normalized_manual_theme = str(manual_theme or "").strip()
        if len(normalized_manual_theme) > 200:
            raise ValueError("stream_topicは200文字以内で指定してください。")

        if normalized_manual_theme:
            self.state = StreamThemeState(
                main_theme=normalized_manual_theme,
                core_question=(
                    f"{normalized_manual_theme}を、複数の角度から考えると何が見えるか"
                ),
                current_focus="最初にテーマの面白い入口を示す",
            )
            self.manual_theme = True
        else:
            self.state = self._state_from_plan(self.plan_generator())
            self.manual_theme = False

    def build_context(self, speech_type):
        self._review_if_needed()
        points = "\n".join(
            f"- {point}" for point in self.state.covered_points[-1:]
        ) or "- まだなし"
        mode_instruction = {
            "observation": (
                "現在の枝にある対象か違和感を一つ引き継ぎ、具体化、例、反論、"
                "妙な仮説のどれかで一段だけ進める"
            ),
            "character_thought": (
                "現在の枝をキャラクターの気まぐれな視点で見る。"
                "別の話題を始めず、設定語や大きな価値観を持ち出さない"
            ),
            "trivia": (
                "現在の枝に直接役立つか連想できる雑学だけを短く足す。"
                "接点がない雑学へ切り替えない"
            ),
            "news": (
                "現在の枝と具体的な接点がある場合だけニュースへ触れる。"
                "接点を捏造したり、記事を使って話題をリセットしたりしない"
            ),
        }.get(speech_type, "メインテーマの流れを自然に前へ進める")
        tangent = self.state.tangent_topic or "なし"
        previous_speech = self.state.last_speech or "なし（今回は最初の一言）"
        return (
            "[配信の会話の流れ]\n"
            f"メインテーマ: {self.state.main_theme}\n"
            f"中心となる問い: {self.state.core_question}\n"
            f"直前のガン奈の発言: {previous_speech}\n"
            f"一時的な脱線: {tangent}\n"
            f"すでに扱った論点:\n{points}\n"
            f"今回の展開方法: {mode_instruction}\n"
            "メインテーマを最優先にし、テーマ名は毎回読み上げない。直前の発言が"
            "テーマ外なら、脱線を一言だけ回収してテーマへ戻る。\n"
            "直前の発言がテーマ内にある場合、その続きを話す。直前の発言の具体物、"
            "違和感、仮説、結論のどれかを一つ残し、具体化、例、反論、連想、少し横の脱線の"
            "いずれかで進める。無関係な名詞から新しい話を始めない。\n"
            "テーマを言い直さず、数発話は同じ枝を育てる。枝を変える場合も、前の枝との"
            "具体的な接点を本文中に残す。"
        )

    def record_autonomous_speech(self, text, speech_type):
        compact_text = " ".join(str(text).split())[:80]
        if compact_text:
            self.state.covered_points.append(compact_text)
            self.state.covered_points = self.state.covered_points[-12:]
            self.state.current_focus = compact_text
            self.state.last_speech = compact_text
        self.state.tangent_topic = (
            compact_text if speech_type in {"news", "trivia"} else ""
        )
        self.state.utterance_count += 1
        self.state.utterances_since_review += 1

    def record_comment_exchange(self, comment, response_text):
        normalized_comment = " ".join(str(comment).split())[:100]
        normalized_response = " ".join(str(response_text).split())[:80]
        self.state.tangent_topic = (
            f"コメントからの枝分かれ: {normalized_comment}"
            if normalized_comment
            else "コメントからの枝分かれ"
        )
        if normalized_response:
            self.state.current_focus = normalized_response
            self.state.last_speech = normalized_response
            self.state.covered_points.append(normalized_response)
            self.state.covered_points = self.state.covered_points[-12:]
        self.state.utterance_count += 1
        self.state.utterances_since_review += 1

    def describe(self):
        return (
            f"メインテーマ：{self.state.main_theme}\n"
            f"中心となる問い：{self.state.core_question}\n"
            f"最初の論点：{self.state.current_focus}"
        )

    def _review_if_needed(self):
        if self.manual_theme:
            return
        if self.state.utterances_since_review < self.review_utterance_count:
            return

        previous_theme = self.state.main_theme
        plan = self.plan_generator(self.state)
        new_state = self._state_from_plan(plan)
        new_state.utterance_count = self.state.utterance_count
        self.state = new_state
        if self.state.main_theme == previous_theme:
            print(f"配信テーマを継続します：{self.state.main_theme}")
        else:
            print(
                "配信テーマを変更します："
                f"{previous_theme} → {self.state.main_theme}"
            )

    @staticmethod
    def _state_from_plan(plan):
        return StreamThemeState(
            main_theme=plan.theme.strip(),
            core_question=plan.core_question.strip(),
            current_focus=plan.opening_angle.strip(),
        )
