# AI YouTuber

YouTube Liveのコメントを取得し、設定されたLLMで返答を生成します。AivisSpeechで音声を生成し、SSE経由でAITuber OnAirのVRMを外部制御できます。

長期的な目標とプロジェクトの判断基準は [VISION.md](VISION.md) に記載しています。

## Phase 1の範囲

- YouTube Liveコメントの取得
- 返答対象コメントの選択
- LLMによる返答生成
- 短期会話、配信メモ、SQLite長期記憶
- 無言時間を抑える自発発話
- ターミナルへの表示

## 現在未対応のもの

- Live2D
- OBS連携
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

OBSで字幕を独立したブラウザソースとして表示する場合は、次のURLを追加します。

```text
http://127.0.0.1:8765/overlay
```

ブラウザソースの幅と高さは配信キャンバスと同じ値にします。AITuber OnAirの
Visual設定では「ソロ配信で内蔵字幕を表示」をオフにしてください。

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
  -d '{"text":"こんにちは！","emotion":"happy","motion":"greeting"}'
```

音声なしでボディモーションだけを確認する場合は、次を使用します。

```bash
curl -X POST http://127.0.0.1:8765/api/motion \
  -H 'Content-Type: application/json' \
  -d '{"motion":"peace_sign"}'
```

利用可能なボディモーションは `show_body`、`greeting`、`peace_sign`、
`shoot`、`spin`、`model_pose`、`squat` です。

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

ニュースRSSの取得と、ガン奈の雑談生成だけを確認します。

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
```

自発発話、コンテキスト予算、記憶件数は`config/llm_config.json`で管理します。
既定では、前の音声の終了見込みから3秒間コメントも発話もない場合に
自発雑談を生成します。ニュース、雑学、身近な観察、キャラクターらしい考察を
設定された重みに従ってランダムに選び、直前と同じ種類は避けます。
コメントがない間は視聴者へ呼びかけず、独り言を基本とします。

ライブコメントへの返答とニュース自発発話を、音声とVRM制御までまとめて
実行する場合は次のモードを使用します。

```bash
.venv/bin/python main.py --mode ai-youtuber-live --max-loops 1000
```

コメントがある場合はコメントへの返答を優先します。ニュースのタイトル、
配信元、公開日時、参照URLは確認できるようターミナルへ表示します。

## LLMコンテキストと記憶

固定人格は`config/character_prompt.txt`、LLM関連設定は
`config/llm_config.json`で管理します。現在のプロバイダーはOpenAIです。
`.env`の`LLM_PROVIDER=openai`で明示できます。GroqとGeminiはプロバイダー決定後に
対応クライアントを追加します。

LLMへ渡す情報は、関連する視聴者記憶、上限付き配信メモ、直近会話、今回の入力に
分けます。System Promptと今回の入力は削らず、設定された総トークン予算を超える
場合は意味のあるエラーを出します。プロバイダー未確定のため、事前計算には日本語を
多めに見積もる近似値を使います。

長期記憶は既定で`data/memory.db`へ保存します。表示名ではなくYouTube Channel IDで
視聴者を識別し、重要度が設定値以上で、機密情報を含まない記憶候補だけを保存します。
DBファイルはGit管理対象外です。

## YouTubeを使わない模擬ライブ

実際のYouTube Liveを始める前に、開始挨拶、ダミーコメントへの返答、
ニュース雑談、終了挨拶を順番に実行できます。AivisSpeechとAITuber OnAirを
起動してから実行してください。

```bash
.venv/bin/python main.py --mode mock-live
```

各発話の待ち時間は `.env` またはコマンド引数で変更できます。

```dotenv
MOCK_LIVE_DELAY_SECONDS=15
```

動作経路だけを短時間で確認する場合は、次のように指定します。

```bash
.venv/bin/python main.py --mode mock-live --mock-delay-seconds 1
```

短い待ち時間では音声が重なる可能性があります。音声と表情の見た目を確認する
場合は、既定の15秒を使用してください。

途中で終了する場合は `Ctrl+C` を押します。OpenAI APIは開始挨拶、
ダミーコメント3件、ニュース雑談、終了挨拶の合計6回呼び出されます。
