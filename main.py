import argparse

from dotenv import load_dotenv

from ai_response import generate_ai_response
from youtube_chat import fetch_chat_messages, get_live_chat_id, iter_chat_messages


def run_openai_test():
    # YouTubeを使わず、固定コメントでOpenAI接続だけ確認します。
    user_name = "テストユーザー"
    comment = "こんにちは！今日の配信楽しみです。"

    ai_answer = generate_ai_response(user_name, comment)

    print(f"{user_name}：{comment}")
    print(f"AI：{ai_answer}")


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

    ai_answer = generate_ai_response(target_message["user_name"], target_message["comment"])

    print(f"{target_message['user_name']}：{target_message['comment']}")
    print(f"AI：{ai_answer}")


def run_ai_youtuber_loop(max_loops):
    # YouTube Liveコメントを継続取得し、未処理コメントにAIが返答します。
    live_chat_id = get_live_chat_id()
    processed_message_ids = set()

    print("AI YouTuberループを開始します。")
    print(f"最大取得回数：{max_loops}")

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
            continue

        ai_answer = generate_ai_response(target_message["user_name"], target_message["comment"])

        print(f"{target_message['user_name']}：{target_message['comment']}")
        print(f"AI：{ai_answer}")


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
    except RuntimeError as exc:
        print(f"エラー: {exc}")
        return


if __name__ == "__main__":
    main()
