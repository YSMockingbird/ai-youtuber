# YouTube OAuth設定メモ

最終確認日: 2026-08-12

この文書は、YouTube Liveの配信IDを自動取得するために行った設定と、
再設定が必要になった場合の手順を記録するものです。
APIキー、クライアントシークレット、アクセストークンなどの秘密情報は記載しません。

## 現在の構成

```text
YouTube Live
    ↓ OAuthで自分のライブ配信を検索
Python
    ↓ 配信動画IDからliveChatIdを取得
YouTubeコメント監視
```

現在のOAuthは「配信を操作する権限」ではなく、配信情報を読み取るために使っています。

| 項目 | 現在の設定 |
|---|---|
| Google CloudプロジェクトID | `ai-youtube-505107` |
| 有効化したAPI | YouTube Data API v3 |
| OAuthクライアント種類 | デスクトップアプリ（installed application） |
| OAuthスコープ | `https://www.googleapis.com/auth/youtube.readonly` |
| リダイレクト先 | `http://localhost`（実行時に空きポートを自動使用） |
| OAuthクライアントJSON | `.secrets/youtube_oauth_client.json` |
| 保存済みOAuthトークン | `.secrets/youtube_oauth_token.json` |
| 動画IDの指定 | `.env`の`YOUTUBE_VIDEO_ID`が空欄なら自動取得 |
| コメント取得用APIキー | `.env`の`YOUTUBE_API_KEY` |

Google Auth Platformの「公開ステータス」がテスト中か本番環境かは、
ローカルファイルから確認できないため、この文書では未確認です。
Google Cloud Consoleの「Google Auth Platform」で確認してください。

## Google Cloudで行った設定

1. Google Cloudプロジェクト`ai-youtube-505107`を作成した。
2. YouTube Data API v3を有効にした。
3. Google Auth Platformのブランディング、対象、データアクセスを設定した。
4. データアクセスへYouTube Data API v3の`youtube.readonly`を追加した。
5. OAuthクライアントを「デスクトップアプリ」として作成した。
6. ダウンロードしたJSONを次の名前で配置した。

```text
.secrets/youtube_oauth_client.json
```

7. 次のコマンドを実行し、配信に使用するYouTubeチャンネルを選んで許可した。

```bash
.venv/bin/python main.py --mode youtube-chat-id
```

8. 認証結果が次のファイルへ自動保存された。

```text
.secrets/youtube_oauth_token.json
```

## `.env`の設定

```dotenv
YOUTUBE_API_KEY=Google Cloudで発行したAPIキー
YOUTUBE_VIDEO_ID=
YOUTUBE_OAUTH_CLIENT_FILE=.secrets/youtube_oauth_client.json
YOUTUBE_OAUTH_TOKEN_FILE=.secrets/youtube_oauth_token.json
```

`YOUTUBE_VIDEO_ID`が空欄の場合、認証したチャンネルが所有する配信を取得し、
Python側で`status.lifeCycleStatus == "live"`の配信だけを選びます。

`YOUTUBE_VIDEO_ID`へ値を設定した場合は、OAuthによる自動取得より手動IDを優先します。

## 通常の起動

OAuth確認用コマンドは毎回実行する必要はありません。本番配信では次だけを実行します。

```bash
.venv/bin/python main.py \
  --mode ai-youtuber-live \
  --max-loops 1000
```

保存済みトークンのアクセストークンが期限切れになった場合、リフレッシュトークンで
自動更新します。リフレッシュトークン自体が無効な場合だけブラウザ認証が再度開きます。

## YouTubeチャンネルを変更する場合

同じGoogleアカウント内でも、通常チャンネルとブランドアカウントは別のYouTube
チャンネルとして扱われます。別のチャンネルへ切り替える場合は、現在のトークンを
削除せず退避します。

```bash
mv .secrets/youtube_oauth_token.json \
   .secrets/youtube_oauth_token.backup.json
```

その後、確認用コマンドを実行し、目的のYouTubeチャンネルを選び直します。

```bash
.venv/bin/python main.py --mode youtube-chat-id
```

複数チャンネルを頻繁に使い分ける場合は、チャンネルごとにトークンファイルを分けます。

```dotenv
YOUTUBE_OAUTH_TOKEN_FILE=.secrets/youtube_oauth_token_channel_a.json
```

OAuthクライアントJSONとYouTube APIキーは、通常はそのまま利用できます。

## Google Auth Platformが「テスト中」の場合

外部ユーザー型かつ公開ステータスが「テスト中」のOAuthアプリでは、YouTubeスコープを
含むリフレッシュトークンが原則7日で期限切れになります。その場合は異常ではなく、
ブラウザで再認証します。長期無人運転へ進む前に、公開ステータスとGoogleの要件を
改めて確認します。

## 将来、配信開始・終了も自動化する場合

現在の`youtube.readonly`では、YouTube配信の開始・終了や配信枠の作成はできません。
実装時には次のどちらかを選びます。

### OBS開始・停止とYouTube自動スタート／ストップを使う

- PythonからOBS WebSocketで配信を開始・停止する
- YouTube Studioの自動スタート／自動ストップを利用する
- OBS WebSocketの認証設定を最初に一度行う

### YouTube APIから配信状態も操作する

- OAuthスコープを`youtube`または`youtube.force-ssl`へ変更する
- 既存トークンには追加権限がないため、一度だけ再認証する
- 配信枠の作成、ストリームとの関連付け、`live`への遷移、`complete`への遷移を実装する

どちらの場合も、LLMから直接終了APIを呼ばず、Python側で最低配信時間、未処理コメント、
終了挨拶と音声再生完了を確認してから停止します。

## セキュリティ

次のファイルは共有、画面掲載、Gitへのコミットをしません。

```text
.env
.secrets/youtube_oauth_client.json
.secrets/youtube_oauth_token.json
```

`.secrets/`は`.gitignore`へ登録済みです。

## よくあるエラー

### 現在ライブ中の配信がありません

- YouTube Studioで実際に「ライブ」になっているか確認する
- OAuth認証時に正しい通常チャンネル／ブランドアカウントを選んだか確認する
- `.env`に古い`YOUTUBE_VIDEO_ID`が残っていないか確認する

### 保存済みトークンを更新できない

トークンを退避して、確認用コマンドから再認証します。

### Python 3.9のFutureWarning

Google認証ライブラリがPython 3.9のサポート終了を警告しています。
現時点では処理を止めるエラーではありませんが、将来Pythonを更新します。

## 公式資料

- [YouTube Live Streaming APIのOAuth](https://developers.google.com/youtube/v3/live/authentication)
- [liveBroadcasts.list](https://developers.google.com/youtube/v3/live/docs/liveBroadcasts/list)
- [liveBroadcasts.transition](https://developers.google.com/youtube/v3/live/docs/liveBroadcasts/transition)
- [Google OAuth 2.0とトークン有効期限](https://developers.google.com/identity/protocols/oauth2)
