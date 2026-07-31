# Obsidian Vault API 実装計画

## 1. 目的

OMV上で稼働するDockerコンテナとして、Obsidian VaultをChatGPTから検索・参照・保存できるREST APIを実装する。

### 必須要件

* Vault内のMarkdownファイルを全文検索できる
* Vault内のMarkdownファイルを読み取れる
* Vaultのディレクトリ構成を取得できる
* Vault全体は読み取り専用とする
* 書き込み先は指定ディレクトリだけに限定する
* ChatGPT Actionsから呼び出せるOpenAPI仕様を提供する
* Bearer Tokenで認証する
* Self-hosted LiveSyncのVaultを直接使用する
* Docker ComposeでOMV上に配置する
* 破壊的操作は実装しない

---

## 2. 対象構成

```text
ChatGPT Custom GPT
        │
        │ HTTPS / GPT Actions
        ▼
Caddy
        │
        ▼
obsidian-api コンテナ
        │
        ├── Vault全体：read-only
        └── ChatGPT Inbox：read-write
                │
                ▼
        Self-hosted LiveSync CLI
                │
                ▼
             CouchDB
```

---

## 3. 権限設計

### 読み取り可能範囲

Vault全体のMarkdownファイル。

```text
/vault-ro/**/*.md
```

### 読み取り対象外

```text
.obsidian/**
.git/**
.trash/**
**/.*
```

添付ファイルは初期バージョンでは対象外とする。

### 書き込み可能範囲

初期版では次の1ディレクトリだけとする。

```text
00_Inbox/ChatGPT/**
```

コンテナ内では以下にマウントする。

```text
/vault-write/inbox
```

### 実装しない操作

* ファイル削除
* 任意パスへの書き込み
* ファイル移動
* ファイル名変更
* Vault全体への更新
* `.obsidian`の読み書き
* シェルコマンド実行
* Git操作
* CouchDBへの直接アクセス

---

## 4. 技術構成

### バックエンド

* Python 3.13
* FastAPI
* Uvicorn
* Pydantic
* PyYAML
* python-frontmatter
* Markdown解析用ライブラリ
* `pathlib`によるパス処理

### 検索

初期版は以下を使用する。

* Pythonによるファイル走査
* 大文字・小文字を区別しない本文検索
* ファイル名検索
* タイトル検索
* YAMLタグ検索

Vault規模が大きくなった場合は、SQLite FTS5へ移行できる構成にする。

### 認証

```http
Authorization: Bearer <API_TOKEN>
```

APIトークンは環境変数またはDocker Secretから取得する。

---

## 5. ディレクトリ構成

```text
obsidian-api/
├── app/
│   ├── __init__.py
│   ├── main.py
│   ├── config.py
│   ├── auth.py
│   ├── models.py
│   ├── exceptions.py
│   │
│   ├── routers/
│   │   ├── health.py
│   │   ├── search.py
│   │   ├── notes.py
│   │   ├── vault.py
│   │   └── inbox.py
│   │
│   └── services/
│       ├── path_security.py
│       ├── markdown_parser.py
│       ├── search_service.py
│       ├── vault_service.py
│       └── inbox_service.py
│
├── tests/
│   ├── fixtures/
│   │   └── vault/
│   ├── test_auth.py
│   ├── test_path_security.py
│   ├── test_search.py
│   ├── test_notes.py
│   ├── test_vault.py
│   └── test_inbox.py
│
├── Dockerfile
├── compose.yaml
├── pyproject.toml
├── .env.example
├── .gitignore
├── README.md
├── AGENTS.md
└── openapi.json
```

---

## 6. API仕様

### 6.1 ヘルスチェック

```http
GET /api/v1/health
```

認証不要。

レスポンス例：

```json
{
  "status": "ok",
  "vault_readable": true,
  "inbox_writable": true
}
```

---

### 6.2 Vault検索

```http
GET /api/v1/search
```

クエリパラメータ：

```text
q
folder
tags
limit
cursor
```

例：

```http
GET /api/v1/search?q=RTX%205070&limit=20
```

レスポンス：

```json
{
  "results": [
    {
      "id": "Knowledge/PC/GPU/RTX 5070.md",
      "path": "Knowledge/PC/GPU/RTX 5070.md",
      "title": "RTX 5070",
      "excerpt": "RTX 5070 MSI GAMING TRIO...",
      "tags": ["gpu", "nvidia"],
      "modified_at": "2026-07-29T21:00:00+09:00"
    }
  ],
  "next_cursor": null
}
```

### 検索仕様

以下を検索対象とする。

* ファイル名
* YAML `title`
* YAML `tags`
* Markdown本文
* 見出し

検索結果は最大50件に制限する。

---

### 6.3 ノート読み取り

```http
GET /api/v1/notes/{note_id}
```

`note_id`はVaultルートからの相対パスをURLエンコードしたものとする。

レスポンス：

```json
{
  "id": "Knowledge/PC/GPU/RTX 5070.md",
  "path": "Knowledge/PC/GPU/RTX 5070.md",
  "title": "RTX 5070",
  "frontmatter": {
    "tags": ["gpu", "nvidia"]
  },
  "content": "# RTX 5070\n...",
  "modified_at": "2026-07-29T21:00:00+09:00"
}
```

1ファイルの最大応答サイズを設定する。

巨大ファイルの場合は切り詰め情報を返す。

```json
{
  "truncated": true
}
```

---

### 6.4 ディレクトリ一覧

```http
GET /api/v1/vault/tree
```

パラメータ：

```text
path
depth
include_notes
```

例：

```http
GET /api/v1/vault/tree?depth=3&include_notes=false
```

返却項目：

* ディレクトリ名
* 相対パス
* ノート数
* サブディレクトリ数
* 最終更新日時

---

### 6.5 Vault概要

```http
GET /api/v1/vault/summary
```

返却項目：

```json
{
  "total_notes": 2500,
  "total_folders": 65,
  "total_markdown_bytes": 125000000,
  "notes_without_frontmatter": 800,
  "duplicate_titles": 15,
  "last_scan_at": "2026-07-30T06:00:00+09:00"
}
```

---

### 6.6 Inboxノート作成

```http
POST /api/v1/inbox/notes
```

リクエスト：

```json
{
  "title": "ChatGPTとObsidian Vaultの連携",
  "content": "# ChatGPTとObsidian Vaultの連携\n...",
  "frontmatter": {
    "tags": ["chatgpt", "obsidian"],
    "source": "chatgpt"
  }
}
```

保存先はAPI側で固定する。

```text
/vault-write/inbox/{sanitized-title}.md
```

クライアントから保存パスは受け取らない。

### 同名ファイル

既存ファイルを上書きしない。

```text
ChatGPTとObsidian Vaultの連携.md
ChatGPTとObsidian Vaultの連携-2.md
```

のように連番を付ける。

---

### 6.7 Inboxノートへの追記

```http
POST /api/v1/inbox/notes/{note_id}/append
```

対象はInbox APIで作成されたファイルだけに限定する。

リクエスト：

```json
{
  "content": "\n## 追記\n..."
}
```

---

## 7. パスセキュリティ

以下を必須とする。

* 絶対パスを受け付けない
* `..`を含むパスを拒否
* URLデコード後にも再検証
* バックスラッシュを拒否
* `Path.resolve()`で実体パスを検証
* 許可ルート外のパスを拒否
* シンボリックリンクを拒否
* Markdownファイル以外を拒否
* 隠しファイルを拒否
* null byteを拒否

読み取り時：

```python
resolved_path.is_relative_to(VAULT_READ_ROOT.resolve())
```

書き込み時：

```python
resolved_path.is_relative_to(VAULT_INBOX_ROOT.resolve())
```

を必ず確認する。

---

## 8. ファイル名のサニタイズ

次を除去または置換する。

```text
/
\
:
*
?
"
<
>
|
制御文字
```

追加制約：

* 最大100文字
* 先頭ピリオド禁止
* 空文字禁止
* 拡張子はAPI側で`.md`を付与
* Windows予約名を拒否
* Unicode正規化を実施

---

## 9. Docker構成

### compose.yaml

```yaml
services:
  obsidian-api:
    build:
      context: .
    container_name: obsidian-api
    restart: unless-stopped

    environment:
      TZ: Asia/Tokyo
      API_TOKEN: ${API_TOKEN}
      VAULT_READ_ROOT: /vault-ro
      VAULT_INBOX_ROOT: /vault-write/inbox
      MAX_SEARCH_RESULTS: 50
      MAX_NOTE_SIZE_BYTES: 1048576

    volumes:
      - /srv/dev-disk-by-uuid-75100e60-4e37-476c-990c-3f763ca7e141/compose/data/obsidian-vault:/vault-ro:ro
      - /srv/dev-disk-by-uuid-75100e60-4e37-476c-990c-3f763ca7e141/compose/data/obsidian-vault/00_Inbox/ChatGPT:/vault-write/inbox:rw

    networks:
      - caddy

    security_opt:
      - no-new-privileges:true

    cap_drop:
      - ALL

    read_only: true

    tmpfs:
      - /tmp:size=64m

networks:
  caddy:
    external: true
```

ホスト側のVaultパスは、実際の構成を確認して必要に応じて修正する。

---

## 10. Dockerfile要件

* Python slimイメージを使用
* バージョンを固定
* rootユーザーで実行しない
* 依存関係のバージョンを固定
* ビルドキャッシュを考慮
* ヘルスチェックを定義
* コンテナ内にVaultデータをコピーしない

---

## 11. Caddy連携

専用ホスト名を使用する。

```text
obsidian-api.example.com
```

Caddyからコンテナのポートへリバースプロキシする。

要件：

* HTTPS必須
* HTTPからHTTPSへリダイレクト
* API以外のパスは拒否
* リクエストサイズ制限
* アクセスログ
* CouchDB用ホスト名とは分離

例：

```caddyfile
obsidian-api.example.com {
    reverse_proxy obsidian-api:8000
}
```

Bearer Token認証はアプリケーション側で行う。

---

## 12. OpenAPI / ChatGPT Actions対応

FastAPIが生成するOpenAPI仕様をベースに、ChatGPT Actions向けに以下を調整する。

* `operationId`を明示
* 各Actionの用途を明確に記述
* パラメータ説明を付ける
* レスポンススキーマを固定
* 必須フィールドを明示
* エラー形式を統一
* APIキー認証スキーマを定義

想定する`operationId`：

```text
searchNotes
readNote
getVaultTree
getVaultSummary
createInboxNote
appendInboxNote
```

---

## 13. エラー仕様

共通形式：

```json
{
  "error": {
    "code": "NOTE_NOT_FOUND",
    "message": "The requested note was not found."
  }
}
```

主なエラーコード：

```text
UNAUTHORIZED
INVALID_PATH
PATH_OUTSIDE_VAULT
NOTE_NOT_FOUND
INVALID_FILE_TYPE
FILE_TOO_LARGE
INVALID_TITLE
NOTE_ALREADY_EXISTS
RATE_LIMITED
INTERNAL_ERROR
```

内部の絶対パスやスタックトレースは返さない。

---

## 14. ログ

記録する項目：

* リクエスト日時
* HTTPメソッド
* エンドポイント
* ステータスコード
* 処理時間
* 読み取ったノートの相対パス
* 作成したInboxノートの相対パス

記録しない項目：

* Bearer Token
* ノート本文
* frontmatterの全文
* クエリに含まれる機密情報

---

## 15. テスト計画

### 単体テスト

* 正常なノート検索
* 日本語検索
* 大文字・小文字を無視した検索
* タグ検索
* frontmatter解析
* WikiLinkを含むノートの読み取り
* 同名ノート作成時の連番
* 日本語ファイル名
* 無効なBearer Token
* Token未指定

### セキュリティテスト

以下をすべて拒否すること。

```text
../secret.md
../../.obsidian/config
%2e%2e%2fsecret.md
..\secret.md
/vault/secret.md
symlink-to-outside
test.txt
.hidden.md
```

### Docker権限テスト

コンテナ内から以下を確認する。

* `/vault-ro`には書き込めない
* `/vault-write/inbox`には書き込める
* Inbox以外には書き込めない
* root権限を持たない
* Linux Capabilityが付与されていない

### LiveSync確認

APIでInboxノートを作成し、以下を確認する。

1. サーバー上のVaultに生成される
2. livesync-cliが変更を検出する
3. CouchDBへ同期される
4. PCのObsidianへ反映される
5. iPhoneのObsidianへ反映される

---

## 16. 実装フェーズ

### Phase 1：最小API

* FastAPIプロジェクト作成
* 設定読み込み
* Bearer認証
* ヘルスチェック
* ノート検索
* ノート読み取り
* Inboxノート作成
* Dockerfile
* Compose
* 基本テスト

### Phase 1 完了条件

* ChatGPT Actionsを使わず、curlで全APIを確認できる
* Vault全体を検索できる
* Inboxへノートを作成できる
* Inbox以外へ書き込めない
* LiveSyncで同期される

---

### Phase 2：Vault構造参照

* ディレクトリツリー
* Vault概要
* frontmatter集計
* タグ集計
* ページング
* 大規模Vault向けの処理改善

### Phase 2 完了条件

* ChatGPTがVault全体の構成を段階的に把握できる
* 大きなレスポンスを一括送信しない
* 数千ノートでもタイムアウトしない

---

### Phase 3：ChatGPT Actions

* OpenAPI仕様整理
* `operationId`設定
* ChatGPT専用の説明文作成
* Custom GPTへAction登録
* Bearer Token設定
* ChatGPTから検索テスト
* ChatGPTからInbox保存テスト

### Phase 3 完了条件

ChatGPTで以下が成功する。

```text
「RTX 5070に関する既存ノートを検索して」
```

```text
「この会話をObsidianのInboxへ保存して」
```

---

### Phase 4：Vault監査

* 孤立ノート検出
* リンク切れ検出
* 重複タイトル検出
* frontmatter欠落検出
* 古いInboxノート検出
* Vault監査レポート生成

このフェーズは初期運用後に実装する。

---

## 17. 非機能要件

* API起動時間：10秒以内
* 検索応答：通常3秒以内
* APIのメモリ使用量：初期状態で256MB以内を目標
* 検索結果：最大50件
* ノート読み取り：標準最大1MB
* リクエスト本文：標準最大2MB
* タイムゾーン：Asia/Tokyo
* 文字コード：UTF-8
* 改行コード：既存ノートを尊重する
* Vaultを変更する処理は原子的に実行する

ノート作成時は一時ファイルへ書き込み後、`os.replace()`で配置する。

---

## 18. Codexへの作業ルール

Codexは以下を遵守すること。

### 必須

* 各Phaseを小さなコミットに分ける
* 実装前にテストを書くか、実装と同時に追加する
* 各変更後にテストを実行する
* `README.md`を常に更新する
* セキュリティ制約を緩めない
* 絶対パスをレスポンスへ含めない
* 依存関係を固定する
* `docker compose config`を確認する
* Compose起動前にdry-run相当の確認を行う

### 禁止

```text
git reset --hard
git clean -fd
force push
Vault内の既存ファイル変更
Vault内の既存ファイル削除
CouchDBへのアクセス
LiveSync設定変更
.obsidianの変更
```

---

## 19. 最初にCodexへ依頼する内容

```text
この計画に従って、まずPhase 1だけを実装してください。

作業前に以下を行ってください。

1. リポジトリ構成案を提示する
2. 採用する依存関係と理由を提示する
3. セキュリティ上のリスクを整理する
4. 実装タスクを細分化する
5. その後に実装を開始する

要件:
- Python 3.13
- FastAPI
- Docker Compose
- Bearer Token認証
- Vault全体はread-only
- 00_Inbox/ChatGPTだけread-write
- 削除・移動・任意書き込みAPIは禁止
- パストラバーサルとシンボリックリンクを拒否
- pytestによるテストを付ける
- READMEと.env.exampleを作成する
- OpenAPI operationIdを明示する
- rootlessコンテナとして実行する

Vaultの既存ファイルには一切変更を加えないでください。
Phase 1完了後、変更ファイル一覧、テスト結果、未解決事項を報告してください。
```

---

## 20. 最終成果物

Phase 1で以下を揃える。

```text
Dockerfile
compose.yaml
.env.example
FastAPIアプリケーション
pytestテスト
README.md
AGENTS.md
OpenAPI仕様
curlによる動作確認例
Caddy設定例
```

最終的に以下の操作ができる状態を目標とする。

```text
ChatGPT
  ├── Vault全体を検索
  ├── 対象ノートを読み取り
  ├── Vaultの構成を確認
  └── 00_Inbox/ChatGPTへMarkdownを保存
```

