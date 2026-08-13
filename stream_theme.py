from dataclasses import dataclass, field

from pydantic import BaseModel, ConfigDict, Field

from llm.client import create_llm_client
from llm.config import load_llm_config


class StreamSegmentPlan(BaseModel):
    model_config = ConfigDict(extra="forbid")

    title: str = Field(min_length=2, max_length=40)
    talking_points: list[str] = Field(min_length=2, max_length=4)
    tangent_ideas: list[str] = Field(min_length=1, max_length=3)
    target_utterances: int = Field(ge=3, le=5)


class StreamThemePlan(BaseModel):
    # 既存名との互換性を保ちつつ、単一テーマではなく配信構成表を表します。
    model_config = ConfigDict(extra="forbid")

    theme: str = Field(min_length=3, max_length=40)
    core_question: str = Field(min_length=5, max_length=100)
    opening_angle: str = Field(min_length=5, max_length=100)
    opening_greeting: str = Field(min_length=10, max_length=180)
    segments: list[StreamSegmentPlan] = Field(min_length=3, max_length=5)
    closing_direction: str = Field(min_length=5, max_length=100)


def generate_stream_theme_plan(previous_state=None, instruction=None):
    # 配信開始時と構成表を使い切った時だけLLMを呼び、本文ではなく構成を作ります。
    previous_context = "なし"
    if previous_state is not None:
        covered_points = "\n".join(
            f"- {point}" for point in previous_state.covered_points[-6:]
        ) or "- なし"
        previous_context = (
            f"前の企画: {previous_state.main_theme}\n"
            f"扱った内容:\n{covered_points}"
        )
    normalized_instruction = str(instruction or "").strip()
    requested_program = normalized_instruction or "AIにおまかせ"

    input_text = (
        "AI YouTuberの雑談配信について、開始挨拶と3〜5個の話題区間を持つ"
        "配信構成表を作ってください。発話本文を全部書く台本ではありません。\n"
        f"配信者からの指定: {requested_program}\n"
        "指定がある場合は最優先してください。『自己紹介配信』なら、AIとしての仕組み、"
        "性格や趣味、活動目的、今後やりたいことなどを初見向けの順番にしてください。\n"
        "具体的な指定がない場合は、VTuber、アニメ、ゲーム、配信文化、ネット上の出来事など、"
        "実在する人物・作品・発表・騒動を扱う時事雑談番組にしてください。"
        "『コンビニで買う物』『普段ついやること』のような一般的な日常あるあるを"
        "番組の主題にしないでください。構成表の段階では未確認の固有名詞や出来事を作らず、"
        "最新記事から題材を入れられる『注目ニュース』『反応』『関連する過去の出来事』などの"
        "区間を作ってください。哲学、社会設計、世界平和をテーマ名の中心にしないでください。\n"
        "企画名は24文字程度までの普通の日本語にし、コロン、長い副題、疑問文、"
        "『人はなぜ』で始まる学術的な題名を避けてください。\n"
        "各区間は一つの実在する話題を3〜5発話ほど続けられる単位にしてください。"
        "同じ話題の要約、ガン奈が引っかかった点、ネットや業界との関係という順で掘り、"
        "一言ごとに別件へ飛ばないようにしてください。tangent_ideasには、元の話題へ戻れる"
        "関連作品、過去の類似例、視聴者層の反応などを入れてください。\n"
        "opening_greetingは実際に読み上げる2〜3文です。短い挨拶と、今日何を話すかを"
        "初見にも分かる言葉で含め、視聴者がいると断定しないでください。\n"
        "世界平和、デスメタル、麻雀、ギャンブルなどの設定は、指定や話の流れに"
        "関係する時だけ使い、毎回の企画へ無理に入れないでください。\n\n"
        f"前回までの状態:\n{previous_context}"
    )
    client = create_llm_client(load_llm_config())
    return client.generate_structured(
        instructions=(
            "あなたはVTuber・アニメ・ゲーム好き向けの長時間雑談を構成する"
            "日本語の番組作家です。難しそうな題名を避け、実在ネタ中心の"
            "配信構成表だけを返してください。"
        ),
        input_text=input_text,
        response_model=StreamThemePlan,
        max_output_tokens=1200,
        request_label="stream_plan",
    )


@dataclass
class StreamThemeState:
    main_theme: str
    core_question: str
    current_focus: str
    opening_greeting: str
    segments: list
    closing_direction: str
    segment_index: int = 0
    segment_utterance_count: int = 0
    previous_segment_title: str = ""
    last_speech: str = ""
    covered_points: list = field(default_factory=list)
    tangent_topic: str = ""
    utterance_count: int = 0
    needs_new_plan: bool = False


class StreamThemeManager:
    def __init__(
        self,
        config,
        manual_theme=None,
        program_instruction=None,
        plan_generator=generate_stream_theme_plan,
    ):
        self.plan_generator = plan_generator
        normalized_manual_theme = str(manual_theme or "").strip()
        normalized_instruction = str(program_instruction or "").strip()
        if len(normalized_manual_theme) > 200:
            raise ValueError("stream_topicは200文字以内で指定してください。")
        if len(normalized_instruction) > 200:
            raise ValueError("stream_planは200文字以内で指定してください。")
        if normalized_manual_theme and normalized_instruction:
            raise ValueError("stream_topicとstream_planは同時に指定できません。")

        if normalized_manual_theme:
            plan = self._create_manual_plan(normalized_manual_theme)
            self.manual_theme = True
        else:
            plan = self.plan_generator(None, normalized_instruction or None)
            self.manual_theme = False
        self.program_instruction = normalized_instruction
        self.state = self._state_from_plan(plan)

    @property
    def current_segment(self):
        return self.state.segments[self.state.segment_index]

    def build_context(self, speech_type):
        self._renew_plan_if_needed()
        segment = self.current_segment
        points = "\n".join(f"- {point}" for point in segment.talking_points)
        tangents = "\n".join(f"- {idea}" for idea in segment.tangent_ideas)
        covered = "\n".join(
            f"- {point}" for point in self.state.covered_points[-2:]
        ) or "- まだなし"
        mode_instruction = {
            "observation": "現在扱っている人物・作品・出来事への具体的な反応を一段だけ進める",
            "character_thought": "現在の実在ネタをキャラクターらしい判断で少し横へ広げる",
            "trivia": "現在の実在ネタへ直接つながる短い背景情報だけを足す",
            "news": "取得した実在の記事を今回の中心題材にして、同じ記事を数発話かけて掘る",
        }.get(speech_type, "現在の区間を自然に前へ進める")
        if self.state.segment_utterance_count == 0:
            transition = (
                f"前の区間『{self.state.previous_segment_title}』から接点を一言残して"
                "新しい話へ移る"
                if self.state.previous_segment_title
                else "配信企画と最初の話題が分かる具体的な入口から始める"
            )
        elif self.state.utterance_count % 5 == 0:
            transition = "途中参加者向けに、現在の対象を一言示してから続ける"
        else:
            transition = "冒頭に具体的な対象を置き、説明の繰り返しを避ける"

        return (
            "[配信構成表]\n"
            f"配信企画: {self.state.main_theme}\n"
            f"現在の区間: {self.state.segment_index + 1}/{len(self.state.segments)} "
            f"{segment.title}\n"
            f"この区間の材料:\n{points}\n"
            f"自然な脱線候補:\n{tangents}\n"
            f"現在の一時的な脱線: {self.state.tangent_topic or 'なし'}\n"
            f"直前の発言: {self.state.last_speech or 'なし'}\n"
            f"最近すでに話した内容:\n{covered}\n"
            f"今回の展開: {mode_instruction}\n"
            f"話題移動: {transition}\n"
            "材料を順番に読み上げず、未使用の材料か脱線候補を一つ選ぶ。"
            "同じ単語、例、結論を言い換えない。脱線は1〜2発話でよく、"
            "面白ければ次の材料へ接続し、行き止まりなら現在の区間へ戻る。"
        )

    def record_opening(self, text):
        compact_text = " ".join(str(text).split())[:80]
        if compact_text:
            self.state.last_speech = compact_text

    def record_autonomous_speech(self, text, speech_type):
        compact_text = " ".join(str(text).split())[:80]
        if compact_text:
            self.state.covered_points.append(compact_text)
            self.state.covered_points = self.state.covered_points[-10:]
            self.state.current_focus = compact_text
            self.state.last_speech = compact_text
        self.state.tangent_topic = (
            compact_text if speech_type in {"news", "trivia"} else ""
        )
        self.state.utterance_count += 1
        self.state.segment_utterance_count += 1
        if self.state.segment_utterance_count >= self.current_segment.target_utterances:
            self._advance_segment()

    def record_comment_exchange(self, comment, response_text):
        normalized_comment = " ".join(str(comment).split())[:100]
        normalized_response = " ".join(str(response_text).split())[:80]
        self.state.tangent_topic = (
            f"コメントからの枝分かれ: {normalized_comment}"
            if normalized_comment
            else "コメントからの枝分かれ"
        )
        if normalized_response:
            self.state.last_speech = normalized_response
            self.state.covered_points.append(normalized_response)
            self.state.covered_points = self.state.covered_points[-10:]

    def replace_program(self, instruction):
        normalized_instruction = str(instruction or "").strip()
        if not normalized_instruction:
            raise ValueError("配信企画の指示が空です。")
        if len(normalized_instruction) > 200:
            raise ValueError("配信企画の指示は200文字以内にしてください。")
        plan = self.plan_generator(self.state, normalized_instruction)
        self.program_instruction = normalized_instruction
        self.manual_theme = False
        self.state = self._state_from_plan(plan)
        return self.describe()

    def describe(self):
        segment_titles = " → ".join(segment.title for segment in self.state.segments)
        return (
            f"配信企画：{self.state.main_theme}\n"
            f"開始挨拶：{self.state.opening_greeting}\n"
            f"話題構成：{segment_titles}\n"
            f"現在の話題：{self.current_segment.title}"
        )

    def status(self):
        return {
            "stream_theme": self.state.main_theme,
            "stream_segment": self.current_segment.title,
            "stream_segment_index": self.state.segment_index + 1,
            "stream_segment_count": len(self.state.segments),
        }

    def _advance_segment(self):
        previous_title = self.current_segment.title
        if self.state.segment_index + 1 >= len(self.state.segments):
            if self.manual_theme:
                self.state.segment_index = 0
            else:
                self.state.needs_new_plan = True
            self.state.segment_utterance_count = 0
            self.state.previous_segment_title = previous_title
            return
        self.state.segment_index += 1
        self.state.segment_utterance_count = 0
        self.state.previous_segment_title = previous_title
        self.state.current_focus = self.current_segment.talking_points[0]
        self.state.tangent_topic = ""
        print(f"配信の話題を移します：{previous_title} → {self.current_segment.title}")

    def _renew_plan_if_needed(self):
        if not self.state.needs_new_plan:
            return
        previous_theme = self.state.main_theme
        total_utterances = self.state.utterance_count
        plan = self.plan_generator(self.state, self.program_instruction or None)
        self.state = self._state_from_plan(plan)
        self.state.utterance_count = total_utterances
        print(f"配信構成を更新します：{previous_theme} → {self.state.main_theme}")

    @staticmethod
    def _state_from_plan(plan):
        return StreamThemeState(
            main_theme=plan.theme.strip(),
            core_question=plan.core_question.strip(),
            current_focus=plan.opening_angle.strip(),
            opening_greeting=plan.opening_greeting.strip(),
            segments=list(plan.segments),
            closing_direction=plan.closing_direction.strip(),
        )

    @staticmethod
    def _create_manual_plan(theme):
        return StreamThemePlan(
            theme=theme,
            core_question=f"{theme}について身近な例から何を話せるか",
            opening_angle=f"{theme}の分かりやすい入口",
            opening_greeting=(
                f"こんばんは。今日は『{theme}』について、身近なところから話してみる。"
            ),
            segments=[
                StreamSegmentPlan(
                    title="身近な入口",
                    talking_points=[f"{theme}を意識する場面", "最初に思い浮かぶ具体例"],
                    tangent_ideas=["最近の小さな失敗"],
                    target_utterances=4,
                ),
                StreamSegmentPlan(
                    title="体験と違和感",
                    talking_points=["実際に困る場面", "妙だと思う習慣"],
                    tangent_ideas=["逆の立場ならどうするか"],
                    target_utterances=4,
                ),
                StreamSegmentPlan(
                    title="少し横の話",
                    talking_points=["別の日常場面との共通点", "今後試してみたいこと"],
                    tangent_ideas=["趣味との意外な接点"],
                    target_utterances=4,
                ),
            ],
            closing_direction="話した内容を一つだけ振り返って軽く締める",
        )
