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

Python 3.12を使用します。Python 3.9はGoogle認証ライブラリのサポート対象外です。

```bash
python3.12 -m venv .venv
.venv/bin/python -m pip install -r requirements.txt
```

1. Python仮想環境を作成する
2. `requirements.txt` のライブラリをインストールする
3. `.env.example` を参考に `.env` を作成する
4. OpenAIまたはGemini API接続を確認する
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

### X投稿案をGeminiで作る

Google AI StudioでGemini APIキーを発行し、`.env`へ次を設定します。

```dotenv
LLM_PROVIDER=openai
X_LLM_PROVIDER=gemini
GEMINI_API_KEY=取得したGemini APIキー
GEMINI_MODEL=gemini-3.5-flash
```

`LLM_PROVIDER`はライブ処理用です。X投稿案の生成は`X_LLM_PROVIDER`だけを
参照するため、ライブをOpenAIのまま運用できます。X投稿案の生成に失敗した場合も、
エラーはX処理内で表示して終了し、ライブ処理へは伝播しません。

話題を自動で選んで投稿案を1件表示します。このモードはXへ送信しません。

```bash
.venv/bin/python main.py --mode x-draft
```

話題を指定する場合は次のように実行します。

```bash
.venv/bin/python main.py --mode x-draft --x-topic "深夜のインターネット"
```

投稿案は130文字以内で、内容に合う場合は絵文字を1個まで使用します。
Gemini APIの無料枠で利用できるモデルと上限は変更される可能性があるため、
Google AI Studioに表示される現在の割り当てを確認してください。

### 確認後にXへ投稿する

X Developer ConsoleでOAuth 1.0の読み取り・書き込み権限を設定し、`.env`へ
次の認証情報を保存します。

```dotenv
X_API_KEY=取得したコンシューマーキー
X_API_KEY_SECRET=取得したコンシューマーキーシークレット
X_ACCESS_TOKEN=取得したアクセストークン
X_ACCESS_TOKEN_SECRET=取得したアクセストークンシークレット
X_POST_HISTORY_DB_PATH=data/x_posts.db
```

次のコマンドは投稿候補を表示するだけで、Xへは送信しません。

```bash
.venv/bin/python main.py --mode x-post
```

投稿を許可する場合は`--confirm`を付けます。候補を確認した後、プロンプトへ
大文字で`POST`と入力した場合だけ送信します。

```bash
.venv/bin/python main.py --mode x-post --confirm
```

話題も指定できます。

```bash
.venv/bin/python main.py --mode x-post --x-topic "深夜のインターネット" --confirm
```

投稿成功時は投稿ID、本文、日時を`data/x_posts.db`へ保存し、同じ本文の
二重投稿を防止します。料金が高くなるURL付き投稿は現在禁止しています。
X投稿処理のエラーはこのモード内で表示して終了し、ライブ処理へ伝播しません。

### YouTube配信IDの自動取得

Google Cloudで行った設定、認証ファイル、再認証、ブランドアカウント切替、
将来の配信開始・終了の自動化については
[YouTube OAuth設定メモ](docs/youtube_oauth_setup.md)にまとめています。

`.env` に `YOUTUBE_API_KEY` を設定します。`YOUTUBE_VIDEO_ID` を空欄にすると、
OAuthで認証した自分のYouTubeアカウントから、現在ライブ中の配信IDを自動取得します。

```dotenv
YOUTUBE_API_KEY=取得したYouTube Data APIキー
YOUTUBE_VIDEO_ID=
YOUTUBE_OAUTH_CLIENT_FILE=.secrets/youtube_oauth_client.json
YOUTUBE_OAUTH_TOKEN_FILE=.secrets/youtube_oauth_token.json
```

Google Cloudからダウンロードしたデスクトップアプリ用OAuthクライアントJSONは、
プロジェクト内の`.secrets/youtube_oauth_client.json`へ配置してください。
`.secrets`はGit管理対象外です。

配信をYouTube Studioで「ライブ」にした後、次のコマンドで`liveChatId`を確認します。
初回だけブラウザが開くので、配信に使用するYouTubeアカウントでアクセスを許可します。
認証結果は`.secrets/youtube_oauth_token.json`へ保存され、通常は2回目以降の
ブラウザ認証は不要です。

```bash
.venv/bin/python main.py --mode youtube-chat-id
```

現在ライブ中の配信がない場合や、同じアカウントで複数の配信がライブ中の場合は、
誤った配信を選ばずエラーを表示します。一時的に手動指定したい場合は、従来どおり
`.env`の`YOUTUBE_VIDEO_ID`へ動画IDを設定すると、その値を優先します。

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

接続確認とスタイルID一覧だけを表示する場合は、先にAivisSpeechを起動します。

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

`ai-youtuber-live`では、AivisSpeech、AITuber OnAir、OBSが停止していれば自動起動し、
利用可能になるまで待ってから配信処理を始めます。AITuber OnAirはOBSの
ブラウザソース用サーバーだけを起動し、SafariやChromeなどの通常ブラウザは開きません。

```dotenv
AUTO_START_AIVIS=true
AIVIS_APP_PATH=/Applications/AivisSpeech.app
AUTO_START_AITUBER=true
AITUBER_DIRECTORY=~/my-aituber
AITUBER_ONAIR_URL=http://localhost:5173/?mode=broadcast
AUTO_START_OBS=true
OBS_APP_PATH=/Applications/OBS.app
LOCAL_SERVICE_START_TIMEOUT_SECONDS=120
```

すでに起動しているサービスはそのまま再利用します。Pythonが自動起動した
AITuber OnAirだけはライブ終了時に停止します。AivisSpeechとOBSは終了しません。
自動起動を無効にする場合は、対応する`AUTO_START_...`を`false`にします。

外部制御サーバーを起動します。

```bash
.venv/bin/python main.py --mode external-control-server
```

AITuber OnAirの設定で外部制御モードを有効にし、SSE接続先を次に設定します。

```text
http://127.0.0.1:8765/events
```

サーバーを起動したターミナルへ文章を入力すると、AivisSpeechで音声を生成し、AITuber OnAirへ字幕・感情・音声を送信します。

OBSで字幕を独立したブラウザソースとして表示する場合は、ブラウザソースの
「ローカルファイル」を有効にして、次のファイルを一度だけ設定します。

```text
subtitle_overlay.html
```

このファイルはPythonが停止している間もOBS上に残り、Pythonサーバーが起動すると
3秒以内を目安に字幕へ自動接続します。通常の配信ごとの再読み込みは不要です。
ブラウザで直接確認する場合は従来どおり`http://127.0.0.1:8765/overlay`を使えます。

ブラウザソースの幅と高さは配信キャンバスと同じ値にします。AITuber OnAirの
Visual設定では「ソロ配信で内蔵字幕を表示」をオフにしてください。

長い字幕は句読点を優先して約28文字ごとに分割され、順番に表示された後で
自動的に消えます。文字数、表示時間、最後に消えるまでの時間は
`subtitle_overlay.html` 冒頭の定数で調整できます。

### OBSへ自作チャット欄を表示する

YouTubeからPythonが取得したコメントを、OBS用のチャット欄にも表示できます。
OBSの「ソース」から「ブラウザ」を追加し、「ローカルファイル」を有効にして
次のファイルを一度だけ設定します。

```text
chat_overlay.html
```

Pythonサーバーの再起動後も3秒ごとに自動再接続します。ブラウザで直接確認する場合は
`http://127.0.0.1:8765/chat-overlay`を使えます。

最初は幅`520`、高さ`900`を目安にし、OBS上で配置と大きさを調整してください。
背景は透明で、コメント部分だけ半透明の黒いカードとして表示されます。
新着コメントは下へ追加され、画面には最大12件、Python側には再接続用として
直近20件を保持します。

AIが返答対象に選んだコメントは、水色の枠とバッジで状態を表示します。

- LLMが返答を生成している間：`考え中`
- AivisSpeechの音声を再生している間：`返信中`
- 音声の再生時間が終了した後：`返信済み`

考え中または返信中のコメントは、発話が終わるまで表示欄から押し出されません。
返答生成や音声合成に失敗した場合は強調表示を解除します。

AIが返答対象に選ばなかったコメントも含め、新規コメントをすべて表示します。
ただし、通常のテキストコメントだけが対象です。スーパーチャット、メンバーバッジ、
チャンネルアイコンの専用表示は現在未対応です。

チャット欄は`ai-youtuber-live`モードでコメントを取得している間に更新されます。
OBSブラウザソースを再読み込みした場合も、同じPythonプロセスが動いていれば
直近のコメントを復元します。表示デザインは`chat_overlay.html`で調整できます。

話題カードは字幕と別のOBSブラウザソースとして追加してください。

```text
http://127.0.0.1:8765/topic-overlay
```

幅`720`、高さ`900`を目安に追加し、OBS上で画面右上などへ自由に配置できます。
高解像度で描画する場合は幅`1440`、高さ`1800`にし、OBS上で50%へ縮小します。
ソースが横長のままでもカード本文は最大幅`520px`で折り返すため、横いっぱいには
広がりません。
ローカルファイルを使う場合は`topic_overlay.html`を指定します。ニュース中は見出し、
短い事実要約、媒体名、情報の確度を表示し、ニュース以外では配信企画と現在の区間を
表示します。ニュース要約は発話と同じLLM応答で生成するため、追加のAPI呼び出しは
発生しません。生成できない場合はRSSの概要または見出しを表示します。

`ai-youtuber-live`では字幕、話題カード、コメントの接続を確認してから開始挨拶を流します。
既定では最大120秒待機し、接続できなければ未接続の表示を出して安全に停止します。
待機時間は`.env`の`OBS_OVERLAY_WAIT_SECONDS`で変更できます。

OBS接続後、YouTubeがまだライブでなければ終了せず、既定で10秒ごとにライブを
再検索します。YouTubeがライブになり、ライブチャットを取得できてから開始挨拶を
流します。再検索間隔は`YOUTUBE_LIVE_WAIT_INTERVAL_SECONDS`、待機上限は
`YOUTUBE_LIVE_WAIT_TIMEOUT_SECONDS`で変更できます。待機上限の`0`は無制限です。

### 配信管理画面

`ai-youtuber-live`モードの実行中は、配信者専用の管理画面を次のURLで開けます。

```text
http://127.0.0.1:8765/admin
```

この画面はOBSへ追加せず、配信者が操作する通常のブラウザで開いてください。
サーバーは`127.0.0.1`で待ち受けるため、同じMacからだけアクセスできます。

### 管理画面をMacログイン中に常駐させる

初回だけ次のスクリプトを実行すると、macOSの`launchd`へユーザー用サービスを
登録します。Macへログインすると管理画面が自動起動し、AivisSpeech、AITuber OnAir、
OBSはまだ起動しません。

```bash
./scripts/install_admin_launch_agent.sh
```

以後はターミナルから`ai-youtuber-live`を起動せず、次の管理画面を開きます。

```text
http://127.0.0.1:8765/admin
```

「この予定で配信開始」または「内容を設定して開始」を押すと、必要なアプリを自動起動して
YouTubeライブの開始へ進みます。待機中は「開始待機を中止」で元の管理画面だけの状態へ戻せます。
YouTubeライブ開始後は、従来どおり「終了挨拶して配信終了」を使用してください。
配信終了後も管理画面だけは稼働し続けます。

カレンダーで「予定時刻に自動で配信開始する」を有効にした予定は、常駐サービスが
開始10分前にAI配信構成を準備し、AivisSpeech、AITuber OnAir、OBSを起動して接続を
確認します。OBSの配信出力とYouTubeライブは予定時刻まで開始しません。予定時刻になると
準備済みのアプリを再利用して配信を開始します。Macがスリープしていた場合も、予定時刻から15分以内に
復帰すれば遅れて開始します。それより古い予定は誤配信防止のため自動開始しません。

自動配信の設定は`.env`で変更できます。

```dotenv
AUTO_SCHEDULE_ENABLED=true
AUTO_SCHEDULE_CHECK_INTERVAL_SECONDS=15
AUTO_SCHEDULE_PREPARE_MINUTES_BEFORE=10
AUTO_SCHEDULE_LATE_GRACE_MINUTES=15
```

通常待機中は15秒ごとのSQLite確認だけで、LLMやYouTube APIは呼び出しません。
自動配信にはMacへのログインと起動状態が必要です。スリープ中のMac自体を起こす機能はありません。

常駐サービスのログは次の場所へ保存します。

```text
~/Library/Logs/AiYoutuber/admin-service.log
~/Library/Logs/AiYoutuber/admin-service-error.log
```

常駐を解除する場合は次を実行します。保存済みの予定・記憶・ログは削除しません。

```bash
./scripts/uninstall_admin_launch_agent.sh
```

プロジェクトや仮想環境を別の場所へ移動した場合は、`launchd`のplist内にある絶対パスも
更新してから、登録スクリプトをもう一度実行してください。

管理画面から次の操作ができます。

- 月間カレンダーへ配信日時、配信内容の希望、自動開始の有無を登録する
- よく使うタイトル・説明文・公開設定をテンプレートとして保存し、予定へ適用する
- 保存した予定の配信構成を準備し、その予定の内容でOBSとYouTubeを開始する
- AIに配信構成とニュース方針を作らせ、確認してから開始する
- 配信タイトル、説明文、公開設定を手入力または保存済みテンプレートから設定する
- 常設のYouTube配信の内容を更新し、OBSとYouTubeを開始する
- 開始可能な既存配信枠が一件の場合、その枠でOBSとYouTubeを開始する
- AIへの非公開指示を、ガン奈自身の自然な発言に変換して話す
- 入力文章をLLMに渡さず、そのままAivisSpeechで話す
- 直接発話の表情、話速、モーションを選ぶ
- 自発発話だけを一時停止・再開する（YouTubeコメント返信は継続）
- 先読み済みの次の自発発話をキャンセルする
- 配信終了の挨拶を生成して話す
- 現在の状態、配信テーマ、待機中の管理命令数を確認する

カレンダーへの登録・編集・削除だけでは、LLMの呼び出しやYouTube配信枠の作成は
行いません。予定とテンプレートは`data/broadcast_schedule.db`へ保存され、Pythonを
再起動しても残ります。予定を選んで「この予定を準備」を押すとAIが配信構成を作り、
その構成もSQLiteへ保存します。「この予定で配信開始」を押すと、予定に保存した
タイトル・説明文・公開設定と配信構成を使って、現在の配信開始処理を実行します。
配信内容を編集した場合は、古いAI構成を誤って使わないよう準備前へ戻ります。

配信内容の希望を入力するか空欄のまま「AIで配信構成を作成」を押すと、AIが
配信構成とニュース方針を作成します。タイトルと説明文はAI生成せず、手入力または
保存済みテンプレートから設定します。内容を確認して「内容を設定して開始」を押して
ください。確認済みの配信構成を開始後の発話へ再利用するため、同じ構成を作るための
LLM呼び出しは重複しません。

「内容を設定して開始」では、常設の「S のライブ配信」のタイトル・説明・公開設定を
更新してからOBSを開始します。開始可能な配信枠がない場合は、画面で設定したタイトル・
説明文・公開設定で新しいYouTube配信枠を自動作成し、既存の再利用ストリームへ接続します。
OBSの映像到着後にYouTubeをライブ状態へ移行するため、YouTube Studioで予約枠を
作成する必要はありません。管理画面では「AIで配信構成を作成」から「内容を設定して開始」
の順で操作します。

管理者命令は自発発話より優先します。直接発話または終了挨拶を送った場合、
現在の音声から管理者指定の発話へ切り替わります。終了挨拶ボタンは挨拶だけを行い、
挨拶後の自発発話を停止します。「終了挨拶して配信終了」はYouTubeとOBSを停止し、
Pythonも終了します。「終了挨拶を生成」だけを押した場合は配信を停止しないため、
間違えて押した場合は「自発発話を再開」で通常状態へ戻せます。

管理画面から送ったAI指示は、配信テーマや会話履歴を利用します。指示文の存在や
「管理者から言われた」といった裏側の事情は、配信上で読み上げないよう指定しています。

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

既定では、GoogleニュースのキーワードRSSから次の話題を横断取得します。

- VTuber、ホロライブ、にじさんじ、ぶいすぽ、AI VTuber
- アニメ、漫画、声優、アニソン
- ゲーム、eスポーツ、ストリーマー
- YouTube、配信文化、ネットミーム、生成AI
- VTuberの炎上、物議、謝罪、卒業、活動休止、噂

24時間以内の記事を強く優先し、既定では7日より古い記事を除外します。
視聴者層との相性、鮮度、現在の配信テーマ、話題性、同じ話題を扱う媒体数を
採点して候補を選びます。死亡、犯罪、住所特定など、短い雑談で安全に扱いにくい
話題は自動的に除外します。

噂や疑惑は取得対象ですが、確定情報としては扱いません。一媒体だけの記事、
複数媒体の報道、未確認の噂を区別してLLMへ渡し、発言にも情報の確度を反映します。

取得先を自分で指定する場合は、利用条件を確認したRSS URLをカンマ区切りで
`NEWS_RSS_URLS`へ設定してください。設定した場合、既定フィードは置き換わります。

```dotenv
NEWS_RSS_URLS=https://example.com/vtuber.xml,https://example.com/anime.xml
NEWS_TIMEOUT_SECONDS=10
NEWS_MAX_AGE_HOURS=168
NEWS_CACHE_SECONDS=600
NEWS_HISTORY_DB_PATH=data/news_history.db
NEWS_HISTORY_DAYS=14
```

`NEWS_CACHE_SECONDS`は取得済みニュースを再利用する秒数です。既定の`600`では、
同じ配信中にRSSへアクセスする回数を10分に1回までに抑えます。更新に失敗した場合は、
取得済みの古いキャッシュがあれば配信を止めずに利用します。

ライブで実際に読み上げを開始したニュースは、`data/news_history.db`へ保存します。
既定では14日間、同じURLに加えて、配信元や表記が少し違う類似見出しも候補から
除外します。保存対象は実際に使った記事だけなので、コメント到着で破棄した先読みは
既読になりません。候補を使い切った場合は、同じニュースを繰り返さず雑学などの
通常雑談へ切り替えます。期間は`NEWS_HISTORY_DAYS`で1〜90日の範囲から変更できます。
この判定はPythonとSQLiteだけで行うため、LLMの呼び出し回数やトークン消費は増えません。

以前の`NEWS_RSS_URL`も独自URLの場合は互換用として利用できます。ただし、旧既定値の
デジタル庁RSSが残っている場合は無視し、オタク層向けの既定フィードへ移行します。

自発発話、コンテキスト予算、記憶件数は`config/llm_config.json`で管理します。
既定では、前の音声の終了見込みから3秒間コメントも発話もない場合に
自発雑談を生成します。ニュース、雑学、身近な観察、キャラクターらしい考察を
設定された重みに従ってランダムに選び、直前と同じ種類は避けます。
コメントがない間は視聴者へ呼びかけず、独り言を基本とします。
配信内容を指定した場合は、配信構成の生成時にニュース方針も同時に決めます。
ニュース不要の企画ではニュースを除外し、関連ニュースを使う企画では生成された
検索テーマでGoogle Newsを絞り込みます。追加のLLM呼び出しは発生しません。
ライブ音声では、現在の音声を再生している間に次の発言とWAV音声を1件だけ
先読みします。WAVの実再生時間を基準に、音声終了後約3秒で準備済み音声を送り、
コメントを取得した場合は未再生の自発発話を破棄してコメント返信を優先します。

ライブ開始時には配信のメインテーマを1つ決め、現在の論点、扱った論点、
コメントからの一時的な脱線をPython側で管理します。自動テーマは20発話ごとに
継続または変更を見直します。テーマを手動指定する場合は次のように起動します。

```bash
.venv/bin/python main.py --mode ai-youtuber-live --max-loops 1000 \
  --stream-topic "AI時代に人間の仕事はどう変わるか"
```

`--stream-topic`を省略すると、配信開始時にLLMがテーマを自動決定します。

ライブコメントへの返答とニュース自発発話を、音声とVRM制御までまとめて
実行する場合は次のモードを使用します。

```bash
.venv/bin/python main.py --mode ai-youtuber-live --max-loops 1000
```

コメントがある場合はコメントへの返答を優先します。ニュースのタイトル、
配信元、公開日時、参照URLは確認できるようターミナルへ表示します。

## LLMコンテキストと記憶

固定人格は`config/character_prompt.txt`、公開できる自己認識・個性・活動目標・
実在対象への立場は`config/character_bible.json`、LLM関連設定は
`config/llm_config.json`で管理します。現在のプロバイダーはOpenAIです。

LLMには各生成時点の年月日、曜日、時刻を短い参照情報として渡します。日時は
関連する場合だけ発言に使い、毎回読み上げないよう指示しています。既定のタイムゾーンは
`Asia/Tokyo`で、必要な場合は`.env`の`AITUBER_TIMEZONE`で変更できます。

`character_bible.json`の`public_identity`には配信で説明できる技術的自己認識、
`personality`には現在の個性と好きなものを見つける方針、`goals`には登録者目標、
`industry_stances`には実在する組織や配信文化への立場を設定します。
各応答には話題に近い設定だけを渡すため、設定を毎回読み上げません。
実際の登録者数は外部から渡された値がない限り発言しません。

配信中に生まれたガン奈自身の共有イベントや、好み・価値観の変化は、重要度が設定値以上の
候補だけを`data/character_memory.db`へ下書き保存します。視聴者ごとの
`memory.db`とは分離されています。承認したものだけが将来の会話で使われます。

## 配信予定データ

今後の管理画面カレンダーで使用する配信予定と、タイトル・説明文の
テンプレートは、既定で`data/broadcast_schedule.db`へ保存します。
保存先は`.env`の`BROADCAST_SCHEDULE_DB_PATH`で変更できます。
予定にはAIへ配信構成を任せるか自分で内容を指定するかを保存し、
タイトルと説明文は登録時の内容を保持します。そのため、後から
テンプレートを編集しても、すでに登録した予定は勝手に変更されません。

ライブまたは外部制御サーバーを起動している間は、ブラウザで次を開くと
下書きの確認、承認、却下をボタン操作できます。配信管理画面の「記憶を確認」からも
移動できます。

```text
http://127.0.0.1:8765/character-memories
```

ターミナルで操作する場合は次のコマンドも使用できます。

```bash
.venv/bin/python main.py --mode character-memory-drafts
.venv/bin/python main.py --mode character-memory-approve --character-memory-id <ID>
.venv/bin/python main.py --mode character-memory-reject --character-memory-id <ID>
```
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
