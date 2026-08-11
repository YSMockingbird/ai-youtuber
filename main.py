import argparse
import os
import time

from dotenv import load_dotenv

from aivis_speech import AivisSpeechClient, DEFAULT_AIVIS_API_URL
from ai_response import (
    generate_ai_response,
    generate_autonomous_speech,
    generate_news_commentary,
)
from control_server import ALLOWED_EMOTIONS, ExternalControlServer
from news_source import fetch_news_articles, select_news_article
from youtube_chat import fetch_chat_messages, get_live_chat_id, iter_chat_messages


def run_openai_test():
    # YouTubeを使わず、固定コメントでOpenAI接続だけ確認します。
    user_name = "テストユーザー"
    comment = "こんにちは！今日の配信楽しみです。"

    ai_response = generate_ai_response(user_name, comment)

    print(f"{user_name}：{comment}")
    print(f"AI：{ai_response['text']}")
    print(f"emotion：{ai_response['emotion']}")


def create_aivis_client():
    api_url = os.getenv("AIVIS_API_URL", DEFAULT_AIVIS_API_URL).strip()
    raw_timeout = os.getenv("AIVIS_TIMEOUT_SECONDS", "180").strip()
    try:
        timeout_seconds = int(raw_timeout)
    except ValueError as exc:
        raise RuntimeError("AIVIS_TIMEOUT_SECONDSは整数で設定してください。") from exc
    if not 10 <= timeout_seconds <= 600:
        raise RuntimeError("AIVIS_TIMEOUT_SECONDSは10〜600秒で設定してください。")
    return AivisSpeechClient(
        base_url=api_url,
        timeout_seconds=timeout_seconds,
    )


def run_aivis_info():
    # AivisSpeech Engineの接続状態と利用可能なスタイルを表示します。
    client = create_aivis_client()
    version = client.get_version()
    styles = client.get_styles()

    print(f"AivisSpeech Engineへ接続しました。version={version}")
    if not styles:
        print(
            "利用可能な音声スタイルがありません。"
            "AIVMモデルを追加してください。"
        )
        return

    print("利用可能な音声スタイル:")
    for style in styles:
        print(
            f"- ID={style['style_id']} / "
            f"{style['speaker_name']} / {style['style_name']}"
        )


def get_aivis_speaker_id(client):
    raw_speaker_id = os.getenv("AIVIS_SPEAKER_ID", "").strip()
    if not raw_speaker_id:
        styles = client.get_styles()
        available = ", ".join(
            f"{style['style_id']}:{style['speaker_name']}/{style['style_name']}"
            for style in styles
        )
        raise RuntimeError(
            "AIVIS_SPEAKER_IDが未設定です。"
            ".envへ使用するスタイルIDを設定してください。"
            f"利用可能={available or 'なし'}"
        )

    try:
        speaker_id = int(raw_speaker_id)
    except ValueError as exc:
        raise RuntimeError("AIVIS_SPEAKER_IDは整数で設定してください。") from exc

    available_style_ids = {
        style["style_id"] for style in client.get_styles()
    }
    if speaker_id not in available_style_ids:
        raise RuntimeError(
            "AIVIS_SPEAKER_IDに対応するスタイルが見つかりません。"
            f"speaker_id={speaker_id}"
        )
    return speaker_id


def get_control_server_port():
    raw_port = os.getenv("CONTROL_SERVER_PORT", "8765").strip()
    try:
        port = int(raw_port)
    except ValueError as exc:
        raise RuntimeError("CONTROL_SERVER_PORTは整数で設定してください。") from exc
    if not 1024 <= port <= 65535:
        raise RuntimeError("CONTROL_SERVER_PORTは1024〜65535で設定してください。")
    return port


def parse_interactive_speech_command(command):
    # 通常入力はneutral、/emotion指定時は指定された感情で発話します。
    if not command.startswith("/emotion "):
        return command, "neutral"

    parts = command.split(maxsplit=2)
    if len(parts) < 3:
        raise ValueError("形式: /emotion surprised 発話する文章")
    emotion = parts[1].strip()
    text = parts[2].strip()
    if emotion not in ALLOWED_EMOTIONS:
        allowed = ", ".join(sorted(ALLOWED_EMOTIONS))
        raise ValueError(f"未対応のemotionです。利用可能={allowed}")
    return text, emotion


def start_external_control_server():
    # 外部制御を使う各モードで共通のサーバーを起動します。
    client = create_aivis_client()
    version = client.get_version()
    speaker_id = get_aivis_speaker_id(client)
    port = get_control_server_port()
    try:
        server = ExternalControlServer(
            aivis_client=client,
            speaker_id=speaker_id,
            port=port,
        )
        server.start()
    except OSError as exc:
        raise RuntimeError(
            f"外部制御サーバーを起動できません。port={port} detail={exc}"
        ) from exc

    return server, version, speaker_id, port


def print_external_control_server_info(version, speaker_id, port):
    print(
        "外部制御サーバーを起動しました。"
        f"AivisSpeech={version} speaker_id={speaker_id}"
    )
    print(f"SSE URL: http://127.0.0.1:{port}/events")
    print(f"確認URL: http://127.0.0.1:{port}/health")


def run_external_control_server():
    # AivisSpeechで生成した音声とSSE命令をAITuber OnAirへ配信します。
    server, version, speaker_id, port = start_external_control_server()

    print_external_control_server_info(version, speaker_id, port)
    print("発話する文章を入力してください。")
    print("感情指定: /emotion surprised びっくりした！")
    print("表情解除: /reset")
    print("終了: /quit")

    try:
        while True:
            try:
                command = input("発話> ").strip()
            except EOFError:
                break
            if not command:
                continue
            if command == "/quit":
                break
            if command == "/reset":
                _, delivered_count = server.runtime.reset()
                print(
                    "リセット命令を送信しました。"
                    f"接続中クライアント数={delivered_count}"
                )
                continue

            try:
                text, emotion = parse_interactive_speech_command(command)
                _, delivered_count = server.runtime.speak(text, emotion)
                print(
                    "音声と発話命令を送信しました。"
                    f"emotion={emotion} 接続中クライアント数={delivered_count}"
                )
            except (RuntimeError, ValueError) as exc:
                print(f"発話エラー: {exc}")
    except KeyboardInterrupt:
        print("\n終了操作を受け付けました。")
    finally:
        server.stop()
        print("外部制御サーバーを停止しました。")


def generate_and_deliver_ai_response(runtime, user_name, comment):
    # OpenAIの返答結果をそのままAivisSpeechとAITuber OnAirへ渡します。
    ai_response = generate_ai_response(user_name, comment)
    _, delivered_count = runtime.speak(
        ai_response["text"],
        ai_response["emotion"],
    )
    return ai_response, delivered_count


def get_autonomous_speech_interval_seconds():
    raw_interval = os.getenv(
        "AUTONOMOUS_SPEECH_INTERVAL_SECONDS",
        "600",
    ).strip()
    try:
        interval_seconds = int(raw_interval)
    except ValueError as exc:
        raise RuntimeError(
            "AUTONOMOUS_SPEECH_INTERVAL_SECONDSは整数で設定してください。"
        ) from exc
    if not 60 <= interval_seconds <= 3600:
        raise RuntimeError(
            "AUTONOMOUS_SPEECH_INTERVAL_SECONDSは60〜3600秒で設定してください。"
        )
    return interval_seconds


def get_unused_news_article(used_links=None):
    articles = fetch_news_articles()
    article = select_news_article(articles, used_links)
    if article is None:
        raise RuntimeError(
            "未使用かつ雑談に適したニュース記事が見つかりませんでした。"
        )
    return article


def print_news_commentary(article, ai_response, delivered_count=None):
    print(
        "ニュース："
        f"{article['title']} / {article['source_name']} / "
        f"{article['published_at'] or '公開日時不明'}"
    )
    print(f"参照URL：{article['link']}")
    print(f"AI：{ai_response['text']}")
    print(f"emotion：{ai_response['emotion']}")
    if delivered_count is not None:
        print(f"接続中クライアント数：{delivered_count}")


def generate_and_deliver_news_commentary(runtime, article):
    # ニュース雑談を生成し、AivisSpeechとAITuber OnAirへ渡します。
    ai_response = generate_news_commentary(article)
    _, delivered_count = runtime.speak(
        ai_response["text"],
        ai_response["emotion"],
    )
    return ai_response, delivered_count


def run_news_test():
    # YouTubeや音声合成を使わず、ニュース取得と雑談生成を確認します。
    article = get_unused_news_article()
    ai_response = generate_news_commentary(article)
    print_news_commentary(article, ai_response)


def run_news_voice_test():
    # ニュース雑談を音声、字幕、表情としてAITuber OnAirへ送信します。
    server, version, speaker_id, port = start_external_control_server()
    print_external_control_server_info(version, speaker_id, port)

    try:
        article = get_unused_news_article()
        ai_response, delivered_count = generate_and_deliver_news_commentary(
            server.runtime,
            article,
        )
        print_news_commentary(article, ai_response, delivered_count)
    finally:
        server.stop()
        print("外部制御サーバーを停止しました。")


def get_mock_live_delay_seconds(override=None):
    raw_delay = (
        str(override)
        if override is not None
        else os.getenv("MOCK_LIVE_DELAY_SECONDS", "15").strip()
    )
    try:
        delay_seconds = float(raw_delay)
    except ValueError as exc:
        raise RuntimeError("MOCK_LIVE_DELAY_SECONDSは数値で設定してください。") from exc
    if not 0 <= delay_seconds <= 60:
        raise RuntimeError("MOCK_LIVE_DELAY_SECONDSは0〜60秒で設定してください。")
    return delay_seconds


def deliver_autonomous_speech(runtime, situation, recent_utterances):
    ai_response = generate_autonomous_speech(situation, recent_utterances)
    _, delivered_count = runtime.speak(
        ai_response["text"],
        ai_response["emotion"],
    )
    recent_utterances.append(ai_response["text"])
    return ai_response, delivered_count


def run_mock_live(delay_override=None):
    # YouTubeを使わず、配信開始から終了までの主要な発話経路を再現します。
    delay_seconds = get_mock_live_delay_seconds(delay_override)
    server, version, speaker_id, port = start_external_control_server()
    recent_utterances = []
    mock_comments = [
        ("初見さん", "初見です。今日の配信を楽しみにしていました"),
        ("ゲーム好き", "りんはどんなゲームが好き？"),
        ("仕事帰り", "今日も残業で疲れたよ"),
    ]

    print_external_control_server_info(version, speaker_id, port)
    print("模擬ライブを開始します。終了する場合はCtrl+Cを押してください。")
    print(f"発話間隔：{delay_seconds}秒")

    try:
        opening_response, delivered_count = deliver_autonomous_speech(
            server.runtime,
            "配信を開始した直後。視聴者を短く歓迎する",
            recent_utterances,
        )
        print("--- 配信開始 ---")
        print(f"AI：{opening_response['text']}")
        print(f"emotion：{opening_response['emotion']}")
        print(f"接続中クライアント数：{delivered_count}")

        for user_name, comment in mock_comments:
            time.sleep(delay_seconds)
            ai_response, delivered_count = generate_and_deliver_ai_response(
                server.runtime,
                user_name,
                comment,
            )
            recent_utterances.append(ai_response["text"])
            print("--- ダミーコメント ---")
            print(f"{user_name}：{comment}")
            print(f"AI：{ai_response['text']}")
            print(f"emotion：{ai_response['emotion']}")
            print(f"接続中クライアント数：{delivered_count}")

        time.sleep(delay_seconds)
        article = get_unused_news_article()
        news_response, delivered_count = generate_and_deliver_news_commentary(
            server.runtime,
            article,
        )
        recent_utterances.append(news_response["text"])
        print("--- コメントがない時間のニュース雑談 ---")
        print_news_commentary(article, news_response, delivered_count)

        time.sleep(delay_seconds)
        closing_response, delivered_count = deliver_autonomous_speech(
            server.runtime,
            "模擬配信を終了する直前。視聴者へ感謝して、短く別れを告げる",
            recent_utterances,
        )
        print("--- 配信終了 ---")
        print(f"AI：{closing_response['text']}")
        print(f"emotion：{closing_response['emotion']}")
        print(f"接続中クライアント数：{delivered_count}")
        # 最後の音声をクライアントが取得する前にサーバーが終了するのを防ぎます。
        shutdown_grace_seconds = min(max(delay_seconds, 1), 5)
        time.sleep(shutdown_grace_seconds)
    except KeyboardInterrupt:
        print("\n終了操作を受け付けました。")
    finally:
        server.stop()
        print("外部制御サーバーを停止しました。")


def run_interactive_ai():
    # 手入力コメントでOpenAIからVRM発話までの経路を確認します。
    server, version, speaker_id, port = start_external_control_server()
    user_name = "テストユーザー"

    print_external_control_server_info(version, speaker_id, port)
    print("視聴者コメントを入力すると、AIキャラクターが返答します。")
    print("表情解除: /reset")
    print("終了: /quit")

    try:
        while True:
            try:
                comment = input("視聴者コメント> ").strip()
            except EOFError:
                break
            if not comment:
                continue
            if comment == "/quit":
                break
            if comment == "/reset":
                _, delivered_count = server.runtime.reset()
                print(
                    "リセット命令を送信しました。"
                    f"接続中クライアント数={delivered_count}"
                )
                continue

            try:
                ai_response, delivered_count = generate_and_deliver_ai_response(
                    server.runtime,
                    user_name,
                    comment,
                )
                print(f"AI：{ai_response['text']}")
                print(f"emotion：{ai_response['emotion']}")
                print(f"接続中クライアント数：{delivered_count}")
            except (RuntimeError, ValueError) as exc:
                print(f"AI発話エラー: {exc}")
    except KeyboardInterrupt:
        print("\n終了操作を受け付けました。")
    finally:
        server.stop()
        print("外部制御サーバーを停止しました。")


def run_youtube_chat_id_test():
    # 配信動画IDからliveChatIdを取得できるか確認します。
    live_chat_id = get_live_chat_id()

    print("YouTube LiveのliveChatIdを取得しました。")
    print(f"liveChatId：{live_chat_id}")


def run_youtube_messages_test():
    # liveChatIdを取得した後、コメントを1回だけ取得します。
    live_chat_id = get_live_chat_id()
    result = fetch_chat_messages(live_chat_id)

    messages = result["messages"]
    print(f"取得コメント数：{len(messages)}")
    print(f"次回取得用トークン：{result['next_page_token']}")
    print(f"推奨待機時間ミリ秒：{result['polling_interval_millis']}")

    for message in messages:
        print(f"{message['user_name']}：{message['comment']}")


def run_youtube_loop_test(max_loops):
    # YouTube Liveコメントを指定回数だけ継続取得します。
    live_chat_id = get_live_chat_id()
    print("YouTube Liveコメントの取得ループを開始します。")
    print(f"最大取得回数：{max_loops}")

    for index, result in enumerate(iter_chat_messages(live_chat_id, max_loops=max_loops), start=1):
        messages = result["messages"]
        print(f"--- {index}回目 / 取得コメント数：{len(messages)} ---")

        for message in messages:
            print(f"{message['user_name']}：{message['comment']}")


def select_reply_target(messages):
    # Phase 1では、返答しやすい新規コメントの先頭1件に返答します。
    for message in messages:
        if is_reply_candidate(message):
            return message

    return None


def is_reply_candidate(message):
    # 空コメントや短すぎるコメントは返答対象から外します。
    comment = message.get("comment", "").strip()

    if len(comment) < 2:
        return False

    if len(comment) > 120:
        return False

    if comment.startswith("@"):
        return False

    meaningful_chars = [char for char in comment if char.isalnum()]
    if not meaningful_chars:
        return False

    return True


def run_ai_youtuber_once():
    # コメントを1回取得し、最初の1件にAIキャラクターとして返答します。
    live_chat_id = get_live_chat_id()
    result = fetch_chat_messages(live_chat_id)
    target_message = select_reply_target(result["messages"])

    if target_message is None:
        print("返答対象のコメントはありませんでした。")
        return

    ai_response = generate_ai_response(target_message["user_name"], target_message["comment"])

    print(f"{target_message['user_name']}：{target_message['comment']}")
    print(f"AI：{ai_response['text']}")
    print(f"emotion：{ai_response['emotion']}")


def run_ai_youtuber_loop(max_loops, runtime=None):
    # コメントを優先し、無言時間が続いた場合はニュースから自発発話します。
    live_chat_id = get_live_chat_id()
    processed_message_ids = set()
    used_news_links = set()
    interval_seconds = get_autonomous_speech_interval_seconds()
    last_speech_at = time.monotonic()

    print("AI YouTuberループを開始します。")
    print(f"最大取得回数：{max_loops}")
    print(f"ニュース自発発話間隔：{interval_seconds}秒")

    for index, result in enumerate(iter_chat_messages(live_chat_id, max_loops=max_loops), start=1):
        messages = [
            message
            for message in result["messages"]
            if message["message_id"] not in processed_message_ids
        ]
        target_message = select_reply_target(messages)

        print(f"--- {index}回目 / 新規コメント数：{len(messages)} ---")

        for message in messages:
            processed_message_ids.add(message["message_id"])

        if target_message is None:
            print("返答対象のコメントはありませんでした。")
            if time.monotonic() - last_speech_at < interval_seconds:
                continue

            try:
                article = get_unused_news_article(used_news_links)
                ai_response = generate_news_commentary(article)
                delivered_count = None
                if runtime is not None:
                    _, delivered_count = runtime.speak(
                        ai_response["text"],
                        ai_response["emotion"],
                    )
                used_news_links.add(article["link"])
                print_news_commentary(
                    article,
                    ai_response,
                    delivered_count,
                )
            except (RuntimeError, ValueError) as exc:
                print(f"ニュース自発発話エラー: {exc}")
            finally:
                # 失敗時も次の取得ですぐ再試行せず、設定間隔を空けます。
                last_speech_at = time.monotonic()
            continue

        ai_response = generate_ai_response(target_message["user_name"], target_message["comment"])

        print(f"{target_message['user_name']}：{target_message['comment']}")
        print(f"AI：{ai_response['text']}")
        print(f"emotion：{ai_response['emotion']}")
        if runtime is not None:
            _, delivered_count = runtime.speak(
                ai_response["text"],
                ai_response["emotion"],
            )
            print(f"接続中クライアント数：{delivered_count}")
        last_speech_at = time.monotonic()


def run_ai_youtuber_live(max_loops):
    # YouTube、OpenAI、AivisSpeech、AITuber OnAirをまとめて実行します。
    server, version, speaker_id, port = start_external_control_server()
    print_external_control_server_info(version, speaker_id, port)

    try:
        run_ai_youtuber_loop(max_loops, runtime=server.runtime)
    finally:
        server.stop()
        print("外部制御サーバーを停止しました。")


def parse_args():
    parser = argparse.ArgumentParser(description="AI YouTuber Phase 1")
    parser.add_argument(
        "--mode",
        choices=[
            "openai-test",
            "youtube-chat-id",
            "youtube-messages",
            "youtube-loop",
            "ai-youtuber-once",
            "ai-youtuber-loop",
            "aivis-info",
            "external-control-server",
            "interactive-ai",
            "news-test",
            "news-voice",
            "ai-youtuber-live",
            "mock-live",
        ],
        default="openai-test",
        help="実行する確認処理を選びます。",
    )
    parser.add_argument(
        "--max-loops",
        type=int,
        default=3,
        help="youtube-loopまたはai-youtuber-loopモードでコメント取得を繰り返す回数です。",
    )
    parser.add_argument(
        "--mock-delay-seconds",
        type=float,
        default=None,
        help="mock-liveモードで各発話の間に待つ秒数です。",
    )
    return parser.parse_args()


def main():
    # .envの内容を環境変数として読み込みます。
    load_dotenv()
    args = parse_args()

    try:
        if args.mode == "openai-test":
            run_openai_test()
        elif args.mode == "youtube-chat-id":
            run_youtube_chat_id_test()
        elif args.mode == "youtube-messages":
            run_youtube_messages_test()
        elif args.mode == "youtube-loop":
            run_youtube_loop_test(args.max_loops)
        elif args.mode == "ai-youtuber-once":
            run_ai_youtuber_once()
        elif args.mode == "ai-youtuber-loop":
            run_ai_youtuber_loop(args.max_loops)
        elif args.mode == "aivis-info":
            run_aivis_info()
        elif args.mode == "external-control-server":
            run_external_control_server()
        elif args.mode == "interactive-ai":
            run_interactive_ai()
        elif args.mode == "news-test":
            run_news_test()
        elif args.mode == "news-voice":
            run_news_voice_test()
        elif args.mode == "ai-youtuber-live":
            run_ai_youtuber_live(args.max_loops)
        elif args.mode == "mock-live":
            run_mock_live(args.mock_delay_seconds)
    except (RuntimeError, ValueError) as exc:
        print(f"エラー: {exc}")
        return


if __name__ == "__main__":
    main()
