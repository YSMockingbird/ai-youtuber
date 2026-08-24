from dataclasses import dataclass, field
from typing import Literal, Optional

from pydantic import BaseModel, ConfigDict, Field

from llm.client import get_shared_llm_client
from time_context import get_current_datetime_context


DELIVERY_STYLES = (
    (
        "具体的な場面",
        "中心材料が表れる具体的な場面を一つだけ話す。一般論や定義から始めない",
    ),
    (
        "率直な好み",
        "中心材料について、自分なら何を好きか、苦手に感じるか、どちらを選ぶかの"
        "どれか一つを率直に話す。特徴を列挙しない",
    ),
    (
        "小さな行動",
        "中心材料に関して実際にする、または避ける小さな行動を一つ話す。"
        "確認できない過去の出来事を事実として作らない",
    ),
    (
        "短い立場",
        "中心材料への立場を一つに絞り、理由を短く添える。無理にオチや教訓を付けない",
    ),
)


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
    news_policy: Literal["off", "related", "general"]
    news_query: Optional[str] = Field(min_length=2, max_length=50)


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
        "性格と価値観、活動目的、目標、今後やりたいことを初見向けの順番にしてください。\n"
        "自己紹介の場合、talking_pointsを『性格』『趣味』のような抽象語だけにせず、"
        "Pythonを本体にして動くこと、未完成だが野心は強い個性、現時点では固定の"
        "好きなものがなく配信を通じて見つけたいこと、笑いや対話を広げて世界平和へ"
        "近づく活動目的、登録者とオリジナルモデルの目標、今後やりたい配信のうち"
        "複数を具体的な材料として分けてください。一度にプロフィール一覧を読ませず、"
        "別のtalking_pointsまたは区間として少しずつ話せる構成にしてください。\n"
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
        "世界平和や登録者目標などの設定は、指定や話の流れに関係する時だけ使い、"
        "毎回の企画へ無理に入れないでください。\n"
        "news_policyは、指定内容にニュースが不要ならoff、直接関係するニュースだけを"
        "使えるならrelated、AIにおまかせで幅広い時事を扱う場合だけgeneralにしてください。"
        "relatedの場合だけ、Google News検索用の短いnews_queryを設定してください。"
        "自己紹介、趣味、特定テーマの雑談へ無関係なニュースを混ぜないでください。\n"
        f"{get_current_datetime_context()}\n\n"
        f"前回までの状態:\n{previous_context}"
    )
    client = get_shared_llm_client()
    return client.generate_structured(
        instructions=(
            "あなたはVTuber・アニメ・ゲーム好き向けの長時間雑談を構成する"
            "日本語の番組作家です。難しそうな題名を避け、実在ネタ中心の"
            "配信構成表だけを返してください。"
        ),
        input_text=input_text,
        response_model=StreamThemePlan,
        # 長めの構成表でもJSONが途中で切れないよう、十分な出力上限を確保します。
        max_output_tokens=2200,
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
    returning_from_comment: bool = False
    utterance_count: int = 0
    needs_new_plan: bool = False
    news_policy: str = "general"
    news_query: str = ""


class StreamThemeManager:
    def __init__(
        self,
        config,
        manual_theme=None,
        program_instruction=None,
        prepared_plan=None,
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

        if prepared_plan is not None:
            if normalized_manual_theme:
                raise ValueError("生成済み配信構成とstream_topicは同時に指定できません。")
            plan = prepared_plan
            self.manual_theme = False
        elif normalized_manual_theme:
            plan = self._create_manual_plan(normalized_manual_theme)
            self.manual_theme = True
        else:
            plan = self.plan_generator(None, normalized_instruction or None)
            self.manual_theme = False
        if (
            normalized_instruction
            and normalized_instruction != "AIにおまかせ"
            and plan.news_policy == "general"
        ):
            # 指定配信で一般ニュースへ逸れる判断は採用しません。
            plan.news_policy = "off"
            plan.news_query = None
        if plan.news_policy == "related" and not plan.news_query:
            plan.news_policy = "off"
        self.program_instruction = normalized_instruction
        self.state = self._state_from_plan(plan)

    @property
    def news_policy(self):
        return self.state.news_policy

    @property
    def news_query(self):
        return self.state.news_query

    @property
    def current_segment(self):
        return self.state.segments[self.state.segment_index]

    def build_context(self, speech_type):
        self._renew_plan_if_needed()
        segment = self.current_segment
        # LLMへ全材料を一度に渡さず、Python側で今回扱う一件を決めます。
        # 材料数より発話数が多い区間では脱線候補も使い、同じ説明の循環を避けます。
        materials = list(segment.talking_points) + list(segment.tangent_ideas)
        material_index = self.state.segment_utterance_count % len(materials)
        current_material = materials[material_index]
        style_index = (
            self.state.utterance_count + self.state.segment_index
        ) % len(DELIVERY_STYLES)
        style_name, style_instruction = DELIVERY_STYLES[style_index]
        is_last_utterance = (
            self.state.segment_utterance_count + 1 >= segment.target_utterances
        )
        if is_last_utterance:
            style_name = "次の区間へのつなぎ"
            style_instruction = (
                "中心材料への考えを一つだけ話し、要約や箇条書きをせず、次の話へ"
                "移れる余白を残す。次の区間の内容を先に説明しない"
            )
        recent_covered = self.state.covered_points
        if recent_covered and recent_covered[-1] == self.state.last_speech:
            # 直前発話は専用欄へ出すため、同じ文章を二重に渡しません。
            recent_covered = recent_covered[:-1]
        reused_materials = "\n".join(
            f"- {point[:56]}" for point in recent_covered[-4:]
        ) or "- まだなし"
        displayed_tangent = self.state.tangent_topic or "なし"
        displayed_last_speech = self.state.last_speech or "なし"
        if self.state.returning_from_comment:
            # 返信済みコメントの強い単語を再入力せず、本編の材料だけへ注意を戻します。
            displayed_tangent = "視聴者コメントへの返信は完了"
            displayed_last_speech = "視聴者コメントへの返信（完了）"
        mode_instruction = {
            "observation": "現在扱っている人物・作品・出来事への具体的な反応を一段だけ進める",
            "character_thought": "現在の実在ネタをキャラクターらしい判断で少し横へ広げる",
            "trivia": "現在の実在ネタへ直接つながる短い背景情報だけを足す",
            "news": "取得した実在の記事を今回の中心題材にして、同じ記事を数発話かけて掘る",
        }.get(speech_type, "現在の区間を自然に前へ進める")
        if self.state.returning_from_comment:
            transition = (
                "視聴者コメントへの返答は直前の発言で完了した。コメントの言葉や"
                "褒め言葉を解説せず、今回から現在の区間へ戻る。必要なら接点は冒頭の"
                "一言だけにして、この区間の未使用材料を中心に話す"
            )
        elif self.state.segment_utterance_count == 0:
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
            f"今回必ず扱う中心材料: {current_material}\n"
            f"今回の話し方: {style_name} - {style_instruction}\n"
            f"現在の一時的な脱線: {displayed_tangent}\n"
            f"直前の発言: {displayed_last_speech}\n"
            f"再利用しない直近の具体例・論点:\n{reused_materials}\n"
            f"話題種類による補助指示: {mode_instruction}\n"
            f"話題移動: {transition}\n"
            "直前発話との接続より、今回の中心材料を優先する。中心材料の文言を"
            "見出しのように読み上げたり、定義・解説したりせず、一人称の自然な"
            "発話に変える。再利用しない欄にある具体例、比喩、論点、結論を再登場させず、"
            "同じ単語、例、結論を言い換えない。"
        )

    def record_opening(self, text):
        compact_text = " ".join(str(text).split())[:80]
        if compact_text:
            self.state.last_speech = compact_text

    def record_autonomous_speech(self, text, speech_type):
        returning_from_comment = self.state.returning_from_comment
        compact_text = " ".join(str(text).split())[:80]
        if compact_text:
            self.state.covered_points.append(compact_text)
            self.state.covered_points = self.state.covered_points[-10:]
            self.state.current_focus = compact_text
            self.state.last_speech = compact_text
        if returning_from_comment:
            self.state.tangent_topic = ""
            self.state.returning_from_comment = False
        else:
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
        self.state.returning_from_comment = True
        if normalized_response:
            self.state.last_speech = normalized_response

    def replace_program(self, instruction):
        normalized_instruction = str(instruction or "").strip()
        if not normalized_instruction:
            raise ValueError("配信企画の指示が空です。")
        if len(normalized_instruction) > 200:
            raise ValueError("配信企画の指示は200文字以内にしてください。")
        plan = self.plan_generator(self.state, normalized_instruction)
        if (
            normalized_instruction != "AIにおまかせ"
            and plan.news_policy == "general"
        ):
            plan.news_policy = "off"
            plan.news_query = None
        if plan.news_policy == "related" and not plan.news_query:
            plan.news_policy = "off"
        self.program_instruction = normalized_instruction
        self.manual_theme = False
        self.state = self._state_from_plan(plan)
        return self.describe()

    def describe(self):
        segment_titles = " → ".join(segment.title for segment in self.state.segments)
        news_description = {
            "off": "使用しない",
            "related": f"関連ニュースのみ（検索: {self.state.news_query}）",
            "general": "幅広いニュースを使用",
        }[self.state.news_policy]
        return (
            f"配信企画：{self.state.main_theme}\n"
            f"開始挨拶：{self.state.opening_greeting}\n"
            f"話題構成：{segment_titles}\n"
            f"現在の話題：{self.current_segment.title}\n"
            f"ニュース方針：{news_description}"
        )

    def status(self):
        return {
            "stream_theme": self.state.main_theme,
            "stream_segment": self.current_segment.title,
            "stream_segment_index": self.state.segment_index + 1,
            "stream_segment_count": len(self.state.segments),
            "news_policy": self.state.news_policy,
            "news_query": self.state.news_query,
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
        if (
            self.program_instruction
            and self.program_instruction != "AIにおまかせ"
            and plan.news_policy == "general"
        ):
            plan.news_policy = "off"
            plan.news_query = None
        if plan.news_policy == "related" and not plan.news_query:
            plan.news_policy = "off"
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
            news_policy=plan.news_policy,
            news_query=str(plan.news_query or "").strip(),
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
                    tangent_ideas=["関連する実在の出来事"],
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
                    tangent_ideas=["実在する人物や作品との接点"],
                    target_utterances=4,
                ),
            ],
            closing_direction="話した内容を一つだけ振り返って軽く締める",
            news_policy="off",
            news_query=None,
        )
