import math
import time
from dataclasses import dataclass

from ai_response import generate_autonomous_speech, generate_news_commentary
from autonomous_topics import AutonomousTopicSelector, TOPIC_INSTRUCTIONS
from news_source import fetch_news_articles, select_news_article
from stream_theme import StreamThemeManager


@dataclass(frozen=True)
class BufferedAutonomousSpeech:
    ai_response: dict
    prepared_speech: object
    topic: str
    article: object = None


class AutonomousSpeechBuffer:
    def __init__(
        self,
        runtime,
        stream_context,
        config,
        publish_callback,
        stream_topic=None,
        theme_manager=None,
        now=None,
    ):
        autonomous_config = config.get("autonomous_speech", {})
        self.silence_seconds = float(
            autonomous_config.get("silence_seconds", 3)
        )
        if not 1 <= self.silence_seconds <= 300:
            raise ValueError("silence_secondsは1〜300秒で設定してください。")

        self.runtime = runtime
        self.stream_context = stream_context
        self.topic_selector = AutonomousTopicSelector(config)
        self.theme_manager = theme_manager or StreamThemeManager(
            config,
            manual_theme=stream_topic,
        )
        self.publish_callback = publish_callback
        self.used_news_links = set()
        self.recent_utterances = []
        self.previous_topic = None
        self.has_received_comment = False
        self.prepared = None
        self.paused = False
        self.retry_at = 0.0
        current_time = time.monotonic() if now is None else float(now)
        self.next_speech_at = current_time + self.silence_seconds

    def tick(self, now=None):
        if self.paused:
            return False
        current_time = time.monotonic() if now is None else float(now)
        self._ensure_prepared(current_time)
        if self.prepared is None or current_time < self.next_speech_at:
            return False

        item = self.prepared
        self.prepared = None
        try:
            ai_response, delivered_count, command = self.publish_callback(
                self.runtime,
                item.ai_response,
                item.prepared_speech,
            )
        except (RuntimeError, ValueError) as exc:
            self._record_error("準備済み音声の配信", exc, current_time)
            return False

        character_event_candidate = ai_response.get(
            "character_event_candidate"
        )
        if character_event_candidate:
            self.stream_context.record_ai_speech(
                ai_response["text"],
                character_event_candidate,
            )
        else:
            self.stream_context.record_ai_speech(ai_response["text"])
        self._remember_utterance(ai_response["text"])
        self.theme_manager.record_autonomous_speech(
            ai_response["text"],
            item.topic,
        )
        self.previous_topic = item.topic
        if item.topic == "news":
            self.used_news_links.add(item.article["link"])
        playback_seconds = command["duration_ms"] / 1000
        published_at = (
            time.monotonic() if now is None else current_time
        )
        self.next_speech_at = (
            published_at + playback_seconds + self.silence_seconds
        )
        self.retry_at = 0.0
        self._print_published(item, ai_response, delivered_count, playback_seconds)

        # 現在の音声を再生している間に、次の文章とWAV音声を準備します。
        self._ensure_prepared(published_at)
        return True

    def cancel_for_comment(self):
        # コメント返信を優先し、まだ再生していない自発発話を破棄します。
        self.prepared = None
        self.next_speech_at = math.inf
        self.retry_at = math.inf

    def pause(self):
        # 管理者操作中は、未再生の自発発話を破棄して新規生成も止めます。
        self.paused = True
        self.prepared = None
        self.next_speech_at = math.inf
        self.retry_at = math.inf

    def resume(self, now=None):
        self.paused = False
        current_time = time.monotonic() if now is None else float(now)
        self.prepared = None
        self.retry_at = 0.0
        self.next_speech_at = current_time + self.silence_seconds

    def cancel_next(self, now=None):
        # 現在再生中の音声は止めず、先読み済みの次の発話だけを破棄します。
        self.prepared = None
        if self.paused:
            return
        current_time = time.monotonic() if now is None else float(now)
        self.next_speech_at = current_time + self.silence_seconds
        self.retry_at = self.next_speech_at

    def schedule_after_external_speech(self, duration_ms, now=None):
        # 管理者発話の音声終了後から通常の無言時間を計測します。
        self.prepared = None
        if self.paused:
            self.next_speech_at = math.inf
            self.retry_at = math.inf
            return
        current_time = time.monotonic() if now is None else float(now)
        self.next_speech_at = (
            current_time + float(duration_ms) / 1000 + self.silence_seconds
        )
        self.retry_at = 0.0

    def resume_after_comment(
        self,
        ai_response,
        duration_ms,
        comment="",
        now=None,
    ):
        self.has_received_comment = True
        self._remember_utterance(ai_response["text"])
        self.theme_manager.record_comment_exchange(
            comment,
            ai_response["text"],
        )
        current_time = time.monotonic() if now is None else float(now)
        self.next_speech_at = (
            current_time + float(duration_ms) / 1000 + self.silence_seconds
        )
        self.retry_at = 0.0
        self.prepared = None
        self._ensure_prepared(current_time)
        return self.tick(now=(None if now is None else current_time))

    def seconds_until_next_speech(self, now=None):
        if math.isinf(self.next_speech_at):
            return 0
        current_time = time.monotonic() if now is None else float(now)
        return max(0, math.ceil(self.next_speech_at - current_time))

    def _ensure_prepared(self, current_time):
        if (
            self.paused
            or self.prepared is not None
            or current_time < self.retry_at
        ):
            return
        try:
            self.prepared = self._prepare_next()
        except (RuntimeError, ValueError) as exc:
            self._record_error("自発発話の先読み", exc, time.monotonic())

    def _prepare_next(self):
        selected_topic = self.topic_selector.select(self.previous_topic)
        theme_context = self.theme_manager.build_context(selected_topic)
        article = None
        if selected_topic == "news":
            try:
                article = self._get_unused_news_article()
            except RuntimeError as exc:
                print(
                    "ニュースを取得できないため雑学へ切り替えます: "
                    f"{exc}"
                )
                selected_topic = "trivia"

        if selected_topic == "news":
            ai_response = generate_news_commentary(
                article,
                context_builder=self.stream_context.context_builder,
                theme_context=theme_context,
                character_memory_repository=(
                    self.stream_context.character_memory_repository
                ),
            )
        else:
            audience_situation = (
                "配信開始後、まだコメントは一件もない。"
                "視聴者がいない前提で独り言を話す"
                if not self.has_received_comment
                else
                "現在はコメントが途切れている。"
                "誰かに回答せず、独り言を続ける"
            )
            ai_response = generate_autonomous_speech(
                audience_situation,
                list(self.recent_utterances),
                context_builder=self.stream_context.context_builder,
                topic_instruction=TOPIC_INSTRUCTIONS[selected_topic],
                theme_context=theme_context,
                character_memory_repository=(
                    self.stream_context.character_memory_repository
                ),
            )

        prepared_speech = self.runtime.prepare_speech(
            ai_response["text"],
            ai_response["emotion"],
            ai_response.get("speech_style", "normal"),
        )
        print(
            "自発発話を先読みしました："
            f"種類={selected_topic} "
            f"テーマ={self.theme_manager.state.main_theme} "
            f"音声時間={prepared_speech.duration_ms / 1000:.1f}秒"
        )
        return BufferedAutonomousSpeech(
            ai_response=ai_response,
            prepared_speech=prepared_speech,
            topic=selected_topic,
            article=article,
        )

    def _get_unused_news_article(self):
        articles = fetch_news_articles()
        article = select_news_article(
            articles,
            self.used_news_links,
            theme_text=(
                f"{self.theme_manager.state.main_theme} "
                f"{self.theme_manager.state.current_focus}"
            ),
        )
        if article is None:
            raise RuntimeError(
                "未使用かつ雑談に適したニュース記事が見つかりませんでした。"
            )
        return article

    def _remember_utterance(self, text):
        self.recent_utterances.append(text)
        self.recent_utterances = self.recent_utterances[-8:]

    def _record_error(self, phase, error, current_time):
        print(f"{phase}エラー: {error}")
        self.prepared = None
        self.retry_at = current_time + 10

    def _print_published(
        self,
        item,
        ai_response,
        delivered_count,
        playback_seconds,
    ):
        if item.topic == "news":
            article = item.article
            print(
                "ニュース："
                f"{article['title']} / {article['source_name']} / "
                f"{article['published_at'] or '公開日時不明'}"
            )
            print(f"参照URL：{article['link']}")
        else:
            print(f"--- 自発雑談 / 種類：{item.topic} ---")
        print(f"AI：{ai_response['text']}")
        print(f"emotion：{ai_response['emotion']}")
        print(f"motion：{ai_response.get('motion')}")
        print(f"接続中クライアント数：{delivered_count}")
        print(f"実音声時間：{playback_seconds:.1f}秒")
