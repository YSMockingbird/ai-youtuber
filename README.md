# AI YouTuber Phase 1

YouTube Liveのコメントを取得し、OpenAI APIでAIキャラクターの返答文を生成して、ターミナルに表示するためのPythonプロジェクトです。

## Phase 1の範囲

- YouTube Liveコメントの取得
- 返答対象コメントの選択
- OpenAI APIによる返答生成
- ターミナルへの表示

## Phase 1で扱わないもの

- 音声合成
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
