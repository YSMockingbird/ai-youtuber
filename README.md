# AI YouTuber

YouTube Liveのコメントを取得し、OpenAI APIで返答を生成します。AivisSpeechで音声を生成し、SSE経由でAITuber OnAirのVRMを外部制御できます。

長期的な目標とプロジェクトの判断基準は [VISION.md](VISION.md) に記載しています。

## Phase 1の範囲

- YouTube Liveコメントの取得
- 返答対象コメントの選択
- OpenAI APIによる返答生成
- ターミナルへの表示

## 現在未対応のもの

- Live2D
- OBS連携
- 長期記憶
- データベース
- Webアプリ化
- Docker
- クラウド実行

## セットアップ予定

今後のステップで以下を設定します。

1. Python仮想環境を作成する
2. `requirements.txt` のライブラリをインストールする
3. `.env.example` を参考に `.env` を作成する
4. OpenAI API接続を確認する
5. YouTube Liveコメント取得を確認する

## 環境変数

`.env.example` を参考に、実行時は `.env` にAPIキーなどを設定します。

`.env` は `.gitignore` に含めているため、Gitにはコミットしません。

## 動作確認

OpenAI API接続だけ確認します。

```bash
.venv/bin/python main.py
```

または明示的に指定します。

```bash
.venv/bin/python main.py --mode openai-test
```

`.env` に `YOUTUBE_API_KEY` と `YOUTUBE_VIDEO_ID` を設定した後、YouTube Liveの `liveChatId` を確認します。

```bash
.venv/bin/python main.py --mode youtube-chat-id
```

YouTube Liveコメントを1回だけ取得します。

```bash
.venv/bin/python main.py --mode youtube-messages
```

YouTube Liveコメントを指定回数だけ継続取得します。

```bash
.venv/bin/python main.py --mode youtube-loop --max-loops 3
```

コメントを1回取得し、先頭の1件にAIが返答します。

```bash
.venv/bin/python main.py --mode ai-youtuber-once
```

YouTube Liveコメントを継続取得し、新規コメントにAIが返答します。

```bash
.venv/bin/python main.py --mode ai-youtuber-loop --max-loops 3
```

## AivisSpeechとAITuber OnAirの接続

先にAivisSpeechを起動します。接続確認とスタイルID一覧を表示します。

```bash
.venv/bin/python main.py --mode aivis-info
```

表示されたIDを `.env` の `AIVIS_SPEAKER_ID` に設定します。

```dotenv
AIVIS_API_URL=http://127.0.0.1:10101
AIVIS_SPEAKER_ID=888753760
AIVIS_TIMEOUT_SECONDS=180
CONTROL_SERVER_PORT=8765
```

外部制御サーバーを起動します。

```bash
.venv/bin/python main.py --mode external-control-server
```

AITuber OnAirの設定で外部制御モードを有効にし、SSE接続先を次に設定します。

```text
http://127.0.0.1:8765/events
```

サーバーを起動したターミナルへ文章を入力すると、AivisSpeechで音声を生成し、AITuber OnAirへ字幕・感情・音声を送信します。

```text
発話> こんにちは、今日は何をしようかな。
発話> /emotion surprised えっ、それ本当なの？
発話> /reset
発話> /quit
```

HTTP APIから送信する場合は次を使用します。

```bash
curl -X POST http://127.0.0.1:8765/api/speak \
  -H 'Content-Type: application/json' \
  -d '{"text":"こんにちは！","emotion":"happy"}'
```

## OpenAI返答からAITuber OnAirまでの統合確認

AivisSpeechとAITuber OnAirを起動した状態で、次のモードを実行します。

```bash
.venv/bin/python main.py --mode interactive-ai
```

ターミナルへ視聴者コメントを入力すると、OpenAIがキャラクターとして
`text` と `emotion` を生成します。その結果をAivisSpeechで音声に変換し、
AITuber OnAirへ字幕・表情・音声として送信します。

```text
視聴者コメント> 今日の調子はどう？
AI：今日はわりと元気だよ。
emotion：happy
```

終了する場合は `/quit`、表情を解除する場合は `/reset` を入力します。

## ニュースを使った自発的な雑談

ニュースRSSの取得と、りんの雑談生成だけを確認します。

```bash
.venv/bin/python main.py --mode news-test
```

AivisSpeechとAITuber OnAirまで含めて確認する場合は、両方を起動してから
次のコマンドを実行します。

```bash
.venv/bin/python main.py --mode news-voice
```

既定ではデジタル庁の公開RSSを使用します。別の提供元を使う場合は、
そのRSSの利用条件を確認してから `.env` のURLを変更してください。

```dotenv
NEWS_RSS_URL=https://www.digital.go.jp/rss/news.xml
NEWS_TIMEOUT_SECONDS=10
AUTONOMOUS_SPEECH_INTERVAL_SECONDS=600
```

`AUTONOMOUS_SPEECH_INTERVAL_SECONDS` は、コメントも発話もない状態で
ニュース雑談を始めるまでの秒数です。60〜3600秒で設定します。

ライブコメントへの返答とニュース自発発話を、音声とVRM制御までまとめて
実行する場合は次のモードを使用します。

```bash
.venv/bin/python main.py --mode ai-youtuber-live --max-loops 1000
```

コメントがある場合はコメントへの返答を優先します。ニュースのタイトル、
配信元、公開日時、参照URLは確認できるようターミナルへ表示します。
