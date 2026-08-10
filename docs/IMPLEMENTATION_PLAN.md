# Obsidian Vault Gateway 実装計画

> Status: Active  
> Revision: 2026-08-08  
> Repository: `vivittel/obsidian-vault-gateway`  
> Primary interface: MCP  
> Secondary interface: REST API  
> Architecture decision: [`docs/adr/0001-switch-primary-interface-to-mcp.md`](adr/0001-switch-primary-interface-to-mcp.md)

## 1. 目的

OMV上で稼働するDockerコンテナとして、Obsidian VaultをChatGPTデスクトップアプリおよびCodexから安全に検索・参照・保存できるゲートウェイを実装する。

当初はChatGPT Actions向けの公開REST APIを主経路として計画したが、実際の必須要件は次のとおりである。

- ChatGPTデスクトップアプリから利用できること
- Codex CLIおよびCodex IDE拡張からも利用できること
- Gatewayを公開インターネットへ露出しないこと
- Vault全体は読み取り専用とすること
- 書き込み先は`00_Inbox/ChatGPT`だけに限定すること

この要件に合わせ、MCPを主インターフェースとする。既存のREST APIは削除せず、ヘルスチェック、curlによる診断、回帰試験、非MCPクライアント向けの互換インターフェースとして維持する。

## 2. 現在の状態

Phase 1・Phase 1.5・Phase 2は完了している（詳細な内訳は§18）。Phase 3は
一部が前倒しで実装済みで、残りは未着手。Phase 4は未着手。

### Phase 1: 完了

以下は実装・テスト・OMV実機確認済みである。

- FastAPIアプリケーション
- Bearer Token認証
- Vault全文検索
- ノート読み取り
- `00_Inbox/ChatGPT`への新規ノート作成
- Vault全体のread-onlyマウント
- Inboxのみread-writeマウント
- パストラバーサル対策
- シンボリックリンク拒否
- 隠しファイル拒否
- 原子的かつ非上書きのノート作成
- DockerイメージのGHCR公開
- OMV Composeへの配置
- Caddy経由のHTTPS
- Self-hosted LiveSync CLIとの同期
- PC側Obsidianへの反映

詳細は[`docs/PHASE1_PLAN.md`](PHASE1_PLAN.md)を参照する。

### Phase 1.5・Phase 2: 完了

MCPトランスポート（`/mcp`、Streamable HTTP）、`get_vault_tree`/
`get_vault_summary`/`append_inbox_note`、カーソルページング、構造化チャット
エクスポート（issue #12）、検証済み関連ノートwikilink（issue #13）を含む。
詳細は[`docs/MCP_IMPLEMENTATION_PLAN.md`](MCP_IMPLEMENTATION_PLAN.md)・
[`docs/PHASE2_PLAN.md`](PHASE2_PLAN.md)、および§18を参照する。

### Phase 3: 一部実装済み

同時実行制限（`app/runtime.py`）、MCPツール利用ログ、および
`find_duplicate_candidates`（issue #14、`docs/adr/0007-*.md`）は実装済み。
レート制限、メトリクス、監視、401急増検知、SDK更新手順、MCP互換性試験は
未着手。詳細は§18を参照する。

## 3. 対象構成

```text
ChatGPTデスクトップアプリ
Codex CLI
Codex IDE拡張
        │
        │ MCP / Streamable HTTP / Bearer Token
        │ 同一CodexホストのMCP設定を共有
        ▼
https://obsidian-api.example.com/mcp
        │
        │ LANまたはTailscale内のみ
        ▼
Caddy
        │
        ▼
obsidian-api コンテナ
        ├── MCP adapter
        ├── REST adapter
        └── 共通application/service layer
                │
                ├── Vault全体: read-only
                └── ChatGPT Inbox: read-write
                        │
                        ▼
                Self-hosted LiveSync CLI
                        │
                        ▼
                     CouchDB
                        │
                        ▼
                 PC / iPhone Obsidian
```

### 対象外

ChatGPT WebはローカルCodexホストの`~/.codex/config.toml`を読み取らないため、この計画の直接対象外とする。

公開Web版ChatGPTから利用する場合は、将来別途以下を検討する。

- ChatGPT Plugin
- リモートMCP
- Secure MCP Tunnel
- 公開可能範囲を分離した専用Gateway

現時点では公開インターネットへの露出を行わない。

## 4. 必須要件

### 機能要件

- Vault内のMarkdownファイルを全文検索できる
- Vault内のMarkdownファイルを読み取れる
- Vaultのディレクトリ構成を段階的に取得できる
- Vault概要を取得できる
- Inboxへ新規ノートを作成できる
- Inbox内のGateway作成ノートへ追記できる
- ChatGPTデスクトップアプリからMCPツールとして利用できる
- Codex CLIおよびIDE拡張から同じMCPツールを利用できる
- REST APIによる診断を継続できる
- Self-hosted LiveSyncのVaultを直接使用する

### セキュリティ要件

- Vault全体はread-only
- 書き込みは`00_Inbox/ChatGPT`のみ
- 削除、移動、名前変更、任意パス書き込みは禁止
- `.obsidian`、`.git`、`.trash`、隠しファイルを対象外とする
- 絶対パスを応答へ含めない
- Bearer Tokenをログへ出さない
- ノート本文をログへ出さない
- 検索語をログへ出さない
- rootユーザーで実行しない
- Linux capabilitiesを付与しない
- コンテナルートFSをread-onlyとする
- Gatewayを公開インターネットへ露出しない

## 5. 権限設計

### 読み取り可能範囲

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

初期段階では添付ファイルを対象外とする。

### 書き込み可能範囲

```text
00_Inbox/ChatGPT/**
```

コンテナ内:

```text
/vault-write/inbox
```

### 実装しない操作

- ファイル削除
- 任意パスへの書き込み
- ファイル移動
- ファイル名変更
- Vault全体への更新
- `.obsidian`の読み書き
- シェルコマンド実行
- Git操作
- CouchDBへの直接アクセス

## 6. 技術構成

### ランタイム

- Python: `pyproject.toml`の`requires-python`は`>=3.12`。CI (`.github/workflows/publish.yml`)は3.12と3.13の両方でテストする。DockerイメージのベースはPython 3.13固定（`python:3.13-slim-bookworm`）（U9: 当初案の「Python 3.13」単独指定から訂正）
- FastAPI
- Uvicorn
- Pydantic
- pydantic-settings
- PyYAML
- MCP Python SDK
- `pathlib`および必要な`os`レベルAPI

### MCP

- Transport: Streamable HTTP
- Endpoint: `/mcp`
- Authentication: Bearer Token
- Mode: stateless HTTP
- Response: JSON response
- Primary clients:
  - ChatGPT desktop app
  - Codex CLI
  - Codex IDE extension

MCP Python SDKは`mcp==2.0.0`に厳密固定する。実装開始時点でPyPI上の安定版はv2系（v1系はメンテナンスのみの旧系列）であったため、本節が当初想定していたv1系固定ではなくv2系を採用した。詳細は`docs/adr/0002-use-mcp-python-sdk-v2.md`を参照する。

### REST

> 本節は歴史的記録。`docs/adr/0010-reduce-rest-surface-to-health-only.md` により
> REST は `GET /api/v1/health` のみに縮小され、以下のエンドポイント一覧は
> 現状を反映していない。

既存エンドポイントを維持する。

```text
GET  /api/v1/health
GET  /api/v1/search
GET  /api/v1/notes
POST /api/v1/inbox/notes
```

Phase 2で追加（実装済み）:

```text
GET  /api/v1/vault/tree
GET  /api/v1/vault/summary
POST /api/v1/inbox/notes/append
```

Phase 3で追加（実装済み、issue #14）:

```text
GET  /api/v1/inbox/duplicate-candidates
```

`POST /api/v1/inbox/notes/append`は対象ノートの`path`をURLではなくJSON body
で受け取る。`GET /api/v1/notes`が`path`をquery paramにしている理由
（`%2F`の扱いがプロキシによって異なるため。§6.3、`docs/PHASE1_PLAN.md` 4.5節）
と同じ判断による。`GET /api/v1/inbox/duplicate-candidates`の`keywords`は
`/search`の`tags`と同じカンマ区切りのquery paramであり、MCP側（JSON配列）との
入力形式の非対称は`docs/adr/0007-*.md`で意図的な決定として記録されている。

### 検索

初期版はPythonによる線形走査を使用する。

- ファイル名
- YAML `title`
- YAML `tags`
- Markdown本文
- 見出し
- NFKC正規化
- casefold
- 関連度順
- 更新日時順

Vault規模または応答時間が要件を超えた場合はSQLite FTS5へ移行する。

## 7. レイヤー構成

RESTとMCPで実処理を重複させない。

```text
Transport adapters
├── REST routers
└── MCP tools
        │
        ▼
Application layer
├── health
├── search_notes
├── read_note
├── get_vault_tree
├── get_vault_summary
├── find_duplicate_candidates
├── create_inbox_note
├── create_chat_export_note
└── append_inbox_note
        │
        ▼
Services
├── path_security
├── markdown_parser
├── search_service
├── note_service
├── inbox_service
├── vault_service
├── cursor_service
├── filenames
├── chat_export
├── related_notes
└── duplicate_notes
        │
        ▼
Filesystem
```

### 原則

- MCPからREST APIを内部HTTP呼び出ししない
- RESTからMCPを呼び出さない
- 両transportは同じapplication/service関数を呼ぶ
- ファイルアクセス規則はservice層に一元化する
- transport固有の認証、エラー変換、ログだけをadapterに置く

## 8. 目標ディレクトリ構成

```text
app/
├── __init__.py
├── main.py
├── config.py
├── auth.py
├── application.py
├── models.py
├── exceptions.py
├── middleware.py
├── mcp_auth.py
├── mcp_server.py
├── openapi_responses.py
├── logging_config.py
├── runtime.py
│
├── routers/
│   ├── health.py
│   ├── search.py
│   ├── notes.py
│   ├── vault.py
│   └── inbox.py
│
└── services/
    ├── path_security.py
    ├── markdown_parser.py
    ├── search_service.py
    ├── note_service.py
    ├── vault_service.py
    ├── cursor_service.py
    ├── inbox_service.py
    ├── filenames.py
    ├── chat_export.py
    ├── related_notes.py
    └── duplicate_notes.py

tests/
├── fixtures/vault/
├── conftest.py
├── test_auth.py
├── test_path_security.py
├── test_search.py
├── test_search_service.py
├── test_notes.py
├── test_inbox.py
├── test_vault.py
├── test_vault_scan_concurrency.py
├── test_cursor_service.py
├── test_filenames.py
├── test_markdown_parser.py
├── test_application.py
├── test_middleware.py
├── test_logging.py
├── test_log_format.py
├── test_health.py
├── test_error_envelope.py
├── test_chat_export.py
├── test_related_notes.py
├── test_duplicate_notes.py
├── test_mcp_auth.py
├── test_mcp_sdk.py
├── test_mcp_lifespan.py
├── test_mcp_tools.py
├── test_mcp_protocol.py
├── test_openapi.py
└── test_rest_regression.py

scripts/
└── export_openapi.py

docs/
├── IMPLEMENTATION_PLAN.md
├── MCP_IMPLEMENTATION_PLAN.md
├── PHASE1_PLAN.md
├── PHASE2_PLAN.md
├── adr/
│   ├── 0001-switch-primary-interface-to-mcp.md
│   ├── 0002-use-mcp-python-sdk-v2.md
│   ├── 0003-allow-os-replace-for-inbox-append.md
│   ├── 0004-allow-disabling-bearer-authentication.md
│   ├── 0005-single-structured-entry-point-for-chat-exports.md
│   ├── 0006-verified-related-note-wikilinks.md
│   ├── 0007-scoped-duplicate-note-detection.md
│   └── 0008-normalize-bare-mcp-path.md
└── caddy/
    └── obsidian-api.Caddyfile
```

## 9. MCPツール計画

### Phase 1.5

```text
get_health
search_notes
read_note
create_inbox_note
```

### Phase 2

```text
get_vault_tree
get_vault_summary
append_inbox_note
```

### Phase 3

```text
find_duplicate_candidates
```

`find_duplicate_candidates`（issue #14、`docs/adr/0007-*.md`）は
`00_Inbox/ChatGPT`直下限定・read-onlyの重複検出ツールであり、Phase 4の
`find_duplicate_titles`（Vault全体の監査ツール）とは別物である。前者は
構造化エクスポート前の判断支援、後者はVault監査であり、互いに依存しない。

### Phase 4

```text
audit_vault
find_broken_links
find_orphan_notes
find_duplicate_titles
find_stale_inbox_notes
```

### ツール分類

| Tool | 種別 | 書き込み | 承認方針 |
|---|---|---:|---|
| `get_health` | read | No | auto |
| `search_notes` | read | No | auto |
| `read_note` | read | No | auto |
| `get_vault_tree` | read | No | auto |
| `get_vault_summary` | read | No | auto |
| `find_duplicate_candidates` | read | No | auto |
| `create_inbox_note` | write | Yes | prompt/writes |
| `append_inbox_note` | write | Yes | prompt/writes |
| `audit_vault` | read | No | auto |

MCPツールのread/writeメタデータを正確に設定し、Codex側で`default_tools_approval_mode = "writes"`を使用できるようにする。

`create_inbox_note`はPhase 2が完了した後（issue #12、
`docs/adr/0005-single-structured-entry-point-for-chat-exports.md`）に
構造化入力（`title` + `export`）専用へ拡張された。
REST `POST /api/v1/inbox/notes`は既存の`content`/`frontmatter`を後方互換として
維持したうえで同じ`export`フィールドを追加しており、MCPとRESTでツール数・承認方針の
表は変わらない。

## 10. 認証

### 共通トークン検証

現在のFastAPI dependency内にある検証ロジックを純粋関数へ抽出する。

```python
def verify_token(provided: str, expected: str) -> bool:
    ...
```

利用箇所:

- RESTのFastAPI dependency
- MCP endpointのASGI middleware

要件:

- `secrets.compare_digest()`を使用
- 16文字未満のトークンを設定段階で拒否
- 推奨値は`openssl rand -hex 32`
- トークンをログへ出さない
- 認証エラーは固定メッセージとする
- RESTとMCPで同一の`API_TOKEN`を使用する

> `AUTH_ENABLED`環境変数により、RESTとMCPの両方で認証を無効化できる
> （既定は`true`＝有効）。無効化は明示的なopt-inであり、外部に同等の
> access-control boundaryが既に存在する場合のみを想定する。詳細は
> `docs/adr/0004-allow-disabling-bearer-authentication.md`。

## 11. パスセキュリティ

以下を必須とする。

- 空文字を拒否
- null byteを拒否
- 絶対パスを拒否
- Windowsドライブレターを拒否
- `..`を拒否
- `.`パス要素を拒否
- バックスラッシュを拒否
- 二重URLデコード攻撃を拒否
- 隠しファイル・隠しディレクトリを拒否
- シンボリックリンクを拒否
- `.md`以外を拒否
- 許可ルート外を拒否
- 通常ファイル以外を拒否
- 応答はVault相対パスのみ

読み取り:

```python
resolved_path.is_relative_to(VAULT_READ_ROOT.resolve())
```

書き込み:

```python
resolved_path.is_relative_to(VAULT_INBOX_ROOT.resolve())
```

MCP tool wrapperで独自の簡易パス検証を追加せず、既存の共通serviceを必ず通す。

## 12. ノート作成

### 保存先

```text
/vault-write/inbox/{sanitized-title}.md
```

クライアントから保存パスを受け取らない。

### 同名ファイル

既存ファイルを上書きしない。

```text
Title.md
Title-2.md
Title-3.md
```

### 原子性

- Inbox内に隠し一時ファイルを作成
- `fsync`
- `os.link()`で候補名へ原子的に配置
- 既存ファイルがあれば次の連番へ進む
- 一時ファイルを必ず削除
- ディレクトリを`fsync`

`os.replace()`は既存ファイルを上書きするため、新規作成では使用しない。

> Phase 2の`append_inbox_note`（既存ノートへの追記）は例外として`os.replace()`
> を使用する — 対象は既存であることが検証済みのノートに限られ、新規作成の
> 上書き禁止は緩めない。詳細は `docs/adr/0003-allow-os-replace-for-inbox-append.md`。

### 構造化エクスポート

`create_inbox_note`の`export`入力（issue #12）は`app/services/chat_export.py`が
決定的に整形する。詳細な決定はすべて
`docs/adr/0005-single-structured-entry-point-for-chat-exports.md`に記録されており、
本節はその契約のうち後から参照する頻度が高い部分（見出し順・プレースホルダ・
frontmatterキー順）だけを転記する。

**共通セクション順序（全モード固定）**

| # | 見出し | 空時のプレースホルダ |
|---|---|---|
| 1 | `## 要約` | （必須・非空） |
| 2 | `## 決定事項` | `なし` |
| 3 | *(モード固有ブロック)* | モードごとに異なる |
| 3.5 | `## コード` | `export.code_blocks`が空なら**セクション自体を省略**（プレースホルダなし） |
| 4 | `## 未解決の論点` | `なし` |
| 5 | `## 次のアクション` | `なし` |
| 6 | `## 関連ノート` | `なし`（検証済みリンクが0件のとき） |
| 7 | `## 出典` | `なし` |

モード固有ブロックの見出しはモードごとに固定（例: `summary`→`## 概要`/`## 要点`、
`issue`→`## 症状`/`## 環境`/`## 調査`/`## 原因`/`## 回避策`）。空時は`未記録`が
既定だが、`issue`モードの`## 原因`のみ`未解決`（原因が未確定であることの明示）。
全モードの見出し一覧はADR-0005参照。`## コード`は他の見出しと異なり、モード固有
セクション群の直後に**任意で追加される supplementary section**であり、ADR-0005
決定4（選択modeの見出しは常に出力する）が対象とする固定見出し集合そのものは
変更しない（詳細はADR-0009）。

**`procedure.steps`のrich block化（ADR-0009）**: `steps`の各要素は
`{"blocks": [...]}`形式で、`blocks`は`{"type": "text", ...}`と
`{"type": "code", "language": ..., "label": ..., "content": ...}`を任意の
順序・回数で混在できる。既存の`steps: ["文字列", ...]`は1つのtext blockを
持つstepの後方互換shorthandとして受理される（`app.models._coerce_step`）。
code contentは`one_line`/`_escape_block_start`を通さない
verbatim/structure-preserving契約（byte-level losslessではない — CRLF/CR統一・
制御文字除去・末尾LFの0/1差の吸収のみ行う）。動的fence長・step番号由来の
continuation indent・Obsidian固有inline構文（`#`タグ/`^`ブロックID等）を
escapeするcaption描画の詳細はADR-0009参照。手順に直接属さないコードは
全モード共通の任意フィールド`export.code_blocks`に置ける。

**本文フィールドのrich block化（ADR-0011）**: `decisions`/`design`/
`topics[].points`等の既存`list[Line]`フィールド20個を`list[BodyItem]`へ
一般化。`BodyItem`はbare stringまたは`{"type": "bullet"|"code"|"table"|"quote", ...}`。
`code`はADR-0009の`CodeBlock`を再利用（新規モデルではない）——`procedure.steps`・
`code_blocks`と同じ型が本文フィールドでも使えるようになった。
`bullet`は`depth`（ネスト深さ、直前bulletの深さ+1を超えるjumpは拒否——clampはしない）
と`checked`（GFM task list）を持つ。`table`はheaders/rows/alignmentsの構造化入力
（生Markdown文字列は受理しない——tableはfenceと異なり自己閉鎖しないため、列数不一致は
明示的にエラーとし、静かな劣化を防ぐ）。`quote`は`callout`（パターン検証のみ、
語彙固定なし）と`title`（`callout`必須）、各`lines`に`_escape_block_start`を適用。
code/table/quoteはbulletリストを終了させ、セクション直下の兄弟blockとしてその場に
レンダリングされる（`## 表`/`## 引用`という別セクションへは集約しない——ADR-0009が
コードの集約を避けた理由と同じ。本文フィールド内のcodeも同様に`## コード`へは
移動しない）。前後は空行で区切る（区切らないと直前のbulletリストのlazy continuation
に吸収され消失する）。`ProcedureStep.blocks`も`table`/`quote`を受理するが`bullet`は
受理しない。`_MAX_TOTAL_CODE_CHARS`は`_MAX_TOTAL_BLOCK_CHARS`へ改名し、table/quoteの
文字列とcode blockの`label`も合算対象に加える（値は100,000のまま）。詳細はADR-0011参照。

**frontmatterキー順**: `title` → `created` → `updated` → `source`（常に`chatgpt`）
→ `export_mode` → `project`（任意、省略可）→ `conversation_type`（任意、省略可）
→ `tags`。

**検証の分担**: 型・上限はpydantic（`app/models.py`の`ChatExport`）、モードと
フィールドの組み合わせ検証は`app/services/chat_export.py`が正規化後のデータに
対して行う。MCP SDKの引数スキーマ検証はツール本体より先に走るため、後者の検証を
pydanticの`model_validator`に置くと`_McpCall`のサニタイズ処理を経由しない
生の`ToolError`になってしまう（ADR-0005決定6）。

**検証済み関連ノートwikilink（issue #13）**: `ChatExport.related_notes`は
クライアントが`search_notes`の結果から選んだvault相対`.md`パスの候補
（既定・任意入力、上限10件）。`app/services/chat_export.py`は純粋関数のまま
（Vaultへアクセスしない）で、`export.related_notes`を直接レンダリングしない。
新規`app/services/related_notes.py`の`resolve_related_notes`が
`app/application.py`の`create_chat_export_note`から呼ばれ、候補ごとに
`path_security.resolve_read_path`で再検証してから、検証済みの生存パスだけを
`render_chat_export`の`verified_related_notes`引数へ渡す。無効・不存在・重複・
上限超過の個別候補は黙って除外され（exportは失敗しない）、11件以上の指定は
pydanticスキーマで拒否される（他の全リストフィールドと同じ規約）。
正規リンク形式は`[[Vault/相対パス]]`（`.md`を除いたフルパス、aliasなし）。
詳細な決定はすべて`docs/adr/0006-verified-related-note-wikilinks.md`に記録。
レスポンスの`related_notes_linked`/`related_notes_skipped`が実際にリンクされた
件数を示す。

### 重複ノート検出（issue #14）

構造化エクスポート前の重複検出は、`create_inbox_note`への組み込みではなく
独立したread-onlyのMCPツール`find_duplicate_candidates`
（REST: `GET /api/v1/inbox/duplicate-candidates`）として実装した。これにより
「Gatewayは類似度から書き込み承認を推論しない」という制約が構造的に成立する
——`create_inbox_note`/`append_inbox_note`はこのツールの出力を一切参照しない。
新規`app/services/duplicate_notes.py`が`00_Inbox/ChatGPT`直下のみを走査し、
`markdown_parser.read_frontmatter_text`（本文非読込）でfrontmatterの
`title`/`project`/`tags`だけを取得する。

初期スコープの照合信号は`exact_title`/`normalized_title`/`project`/`keywords`
の4種（完全内容フィンガープリントは今回のスコープ外、issue #14参照）。
信頼度（`high`/`medium`/`low`）から`recommendation`
（`create`/`confirm`/`choose`）への決定的なマッピング、`project`未設定同士を
一致としない規則、`limit`適用前の全候補から`recommendation`を決定する規則など、
詳細な決定はすべて`docs/adr/0007-scoped-duplicate-note-detection.md`に記録。

新規/追記/中止の選択が必要な場面（`recommendation`が`confirm`/`choose`）では、
ユーザーが明示的に選ぶまで`create_inbox_note`・`append_inbox_note`のどちらも
呼ばないことをクライアント側のワークフロー契約として`SERVER_INSTRUCTIONS`と
各ツールのdescriptionに文書化した（Gateway自体は書き込みをゲートしない）。

Phase 4計画の`find_duplicate_titles`（Vault全体の監査ツール）とは別物であり、
互いに依存しない。

## 13. エラー仕様

### REST

```json
{
  "error": {
    "code": "NOTE_NOT_FOUND",
    "message": "The requested note was not found."
  }
}
```

### MCP

- Gatewayの既存エラーコードを維持する
- MCP tool errorとして返す
- スタックトレースをクライアントへ返さない
- 絶対パスを返さない
- 内部例外メッセージを返さない
- 入力エラーと内部エラーを区別する

主なエラーコード:

```text
UNAUTHORIZED
VALIDATION_ERROR
INVALID_PATH
PATH_OUTSIDE_VAULT
NOTE_NOT_FOUND
INVALID_FILE_TYPE
FILE_TOO_LARGE
INVALID_TITLE
NOTE_ALREADY_EXISTS
INVALID_CURSOR
NOTE_MODIFIED
RATE_LIMITED
INTERNAL_ERROR
```

`INVALID_CURSOR`（400）はPhase 2で追加。検索・Vault Treeのページングカーソル
が改ざん・破損・別条件への流用のいずれかに該当する場合に返す。`NOTE_MODIFIED`
（409）もPhase 2で追加。`append_inbox_note`が対象ノートの検証時点から変更を
検出した場合に返す（`docs/PHASE2_PLAN.md` §6）。

## 14. ログ

記録する項目:

- 日時
- transport: `rest`または`mcp`
- operation/tool名
- ステータス
- 処理時間
- 読み取ったノートの相対パス（REST）
- 作成したノートの相対パス（REST）
- 検索語の長さ
- 結果件数

**MCPでの逸脱（U1）**: MCPアクセスログは`transport=mcp` / `tool` / `status` /
`duration_ms` / `query_length`（`search_notes`のみ） / `result_count`のみを
記録し、読み取り・作成したノートの相対パス（`note_path`）は記録しない。REST側は
上記のとおり相対パスを記録するため、transport間でログ項目が完全には一致しない。
両者を統一するのではなく、MCP側をより保守的（記録項目を少なく）にする方向で
意図的に逸脱している。

記録しない項目:

- Bearer Token
- Authorizationヘッダー
- ノート本文
- frontmatter全文
- 検索語
- MCP request body
- MCP response body
- ホスト側絶対パス

**出力形式（実装時に確定）**: 上記は記録項目の列挙であり、出力形式ではない。
実際のレンダリングは桁揃え平文（`$1` 日時 / `$2` level / `$3` transportまたは
発生元 / `$4` method / `$5` route・tool / `$6` status / `$7` duration、以降に
任意項目の`key=value`）で、stdoutへ1レコード1行。設計判断とフィールド一覧は
`app/logging_config.py`のモジュールdocstringとREADMEの「Logging」節、
レンダリング後の1行に対する検証は`tests/test_log_format.py`にある。

`LOG_LEVEL`は`app/logging_config.py`が`obsidian_gateway`系ロガーへ適用する。
`/api/v1/health`のアクセスログのみDEBUG（Docker HEALTHCHECKが30秒ごとに叩く
ため、INFOではアクセスログのほぼ全量がこの1ルートになる）。

## 15. Docker構成

### 原則

- GHCRイメージをOMVでpullする
- ホストへ8000番を公開しない
- Caddyと同一のDockerネットワークへ接続
- Vault全体をread-onlyマウント
- Inboxだけread-writeマウント
- `read_only: true`
- `cap_drop: ALL`
- `no-new-privileges:true`
- 非root UID/GIDで実行
- `/tmp`はtmpfs
- Vaultデータをイメージへコピーしない

### ネットワーク

`compose.yaml`はローカルalias名を`proxy`に固定し、実際の外部Dockerネットワーク名を
`PROXY_NETWORK`環境変数（既定値`caddy`）で指定する（A5: 本節の当初案「`br0`」と
実際の`compose.yaml`の記述「`caddy`」が一致していなかったため、どちらか一方に
決め打ちせず設定可能にした）。

```yaml
networks:
  proxy:
    external: true
    name: ${PROXY_NETWORK:-caddy}
```

### 公開範囲

```text
OMV host port 8000: 非公開
Caddy HTTPS 443: LAN / private DNS経由
MCP endpoint: /mcp
REST endpoint: /api/v1/*
```

## 16. Caddy

専用ホスト名:

```text
obsidian-api.example.com
```

要件:

- HTTPS
- Private DNSまたはTailscaleでのみ到達
- `/mcp`をobsidian-apiへ転送
- `/api/v1/*`を診断用に維持
- リクエストサイズ制限
- アクセスログ
- Bearer Tokenをログへ出さない
- CouchDB用ホスト名と分離

例:

```caddyfile
@obsidian_api host obsidian-api.example.com
handle @obsidian_api {
    reverse_proxy http://obsidian-api:8000
}
```

## 17. テスト計画

### 既存REST回帰

> 本節は歴史的記録。`docs/adr/0010-reduce-rest-surface-to-health-only.md` により
> 該当機能の回帰テストは `GatewayApplication` 直呼びまたは MCP 経由のテストへ
> 移行済み。REST 自体は `GET /api/v1/health` のみ。

- health
- 認証なし401
- 無効トークン401
- 日本語検索
- NFKC検索
- タグ検索
- folder検索
- ノート読み取り
- 大容量ノート切り詰め
- Inbox作成
- 同名連番
- frontmatter
- パストラバーサル拒否
- symlink拒否
- Inbox外書き込み拒否

### MCP単体

- tool一覧
- toolスキーマ
- server instructions
- read/write annotations
- `search_notes`
- `read_note`
- `create_inbox_note`
- tool error変換
- 構造化レスポンス

### 構造化チャットエクスポート（`tests/test_chat_export.py`、issue #12）

- 全7モードの見出し・順序
- 空セクションのプレースホルダ（`なし`/`未記録`/`未解決`）
- frontmatterキー順・省略規則・タグ正規化
- モード外フィールド・背骨フィールド欠落の拒否とエラーメッセージ
- 正規化後データに対する検証（正規化前は非空だが正規化後に空になる入力）
- タイトル・本文への構造注入（偽見出し）耐性
- 決定性（同一`now`での再実行がバイト一致すること）

**`procedure.steps`のrich block化とtop-level `code_blocks`（issue #12
follow-up、ADR-0009）**: 依存として`markdown-it-py`をdev extrasに追加し、
文字列containsだけでなく描画結果を実際にparseして構造を検証する。

- schema: text/code blockのフィールド検証、`language`パターン、
  未知フィールド拒否、`blocks`/`CodeBlock.content`の空拒否
  （`TextBlock.content`はlegacy shorthand互換のため空を許容）、
  1 blockあたり・1 stepあたり・export全体のコード文字数上限
- canonicalization境界: CRLF/CR統一、制御文字除去（tab/newlineは保持）、
  末尾LFは最大1個のみ吸収（2個以上の末尾空行は保持）— 契約は
  `fence_token.content == canonicalise_code(input) + "\n"`
- 動的fence長（content内の最長backtick runより1長い）
- captionのinline escape（CommonMark/GFM + Obsidian固有構文`#`/`^`/`==`/
  `$`/`%%`）— Obsidian層は`markdown-it-py`では検出できないため文字集合を
  直接pin
- レンダラ構造（`markdown-it-py`のtoken解析）: step番号が10以上でも
  `ordered_list_open`が1個のまま崩れないこと、fenceが対象step内に収まる
  こと、code後の続きのtextが同じlist itemに残ること
- 後方互換: `steps: ["文字列", ...]`が既存と同じ出力になること、
  コードを含まないexportのgolden outputが不変であること
- code-firstなstep（先頭がcode block）の拒否
- `## コード`が空なら省略されること、全モードで利用できること、procedure
  step内のコードが`## コード`へ集約されないこと

**本文フィールドのrich block化（ADR-0011）**: 既存の`_MD`（table無効の
commonmark preset）に加え、`_MD_TABLE = MarkdownIt("commonmark").enable("table")`
を追加してtable構造を検証する（新規依存なし）。

- schema: `TableBlock`/`QuoteBlock`/`BulletBlock`のフィールド検証、未知フィールド
  拒否、`headers`/`lines`の空リスト拒否、`callout`パターン、`depth`の範囲（0〜3）、
  `TextBlock`が`depth`/`checked`を持たないこと（`extra="forbid"`）、`BodyBlock`が
  ADR-0009の`CodeBlock`を再利用すること（新規`$defs`が増えないこと）
- table: 列数不一致・alignments長不一致・空headerの拒否（クライアント値を
  含まないエラーメッセージ）、0行tableの許容、空セルの許容、`\`→`|`の順で
  escapeすることでセル内`\|`が列を分割しないこと、セル内inline Markdownが
  生きたまま残ること（code captionの`_escape_inline`は使わない）
- quote: `callout`なしの`title`拒否、空白のみの行がすべて正規化後に空になった
  quoteのdrop、`_escape_block_start`が対象とする全hazard class（`#`/`>`/`<`/
  `[`/`-`・`*`・`+`/fence/thematic break/`N.`・`N)`）がquote行でも防御されること、
  header行の`title`はinline Markdownが生きたまま残ること
- bullet: depth 0/1/2のネスト、depthの逆転（jumpの拒否、clampしないこと）、
  先頭depthが0以外の拒否、正規化で空bulletが脱落した後もsource_indexで
  クライアント入力のindexを報告すること、table/quote後にdepthが0から再開する
  ことの成功・失敗両ケース、checked true/falseのtask list checkbox描画
- 構造: bullet/code/table/quoteが兄弟blockとしてネストせず並ぶこと、連続する
  section-level block間にも空行が入ること、rich blockのみのfieldに余計な
  bulletが出ないこと、`topics[].points`でもtableが使えること、本文フィールド内の
  codeが`## コード`へ移動しないこと
- 予算: `_MAX_TOTAL_CODE_CHARS`改名後の`_MAX_TOTAL_BLOCK_CHARS`に本文フィールド内の
  code/table/quoteの文字列とcode blockの`label`が算入されること（境界値テスト）
- 後方互換: 全モードでbare stringのみのexportがバイト一致で不変であること
  （既存golden outputを含む）

### 検証済み関連ノートwikilink（`tests/test_related_notes.py`、issue #13）

- 実在ノートの受理・指定順の保持
- 不存在・重複・曖昧なbasename（フルパス形式により推測が発生しないことの証明）
- 危険文字（`[` `]` `|` `#` `^`）を含む実在ファイル名の除外
- `Foo.md.md`の除外、symlink・非Markdown・hidden要素の除外
- 上限件数での打ち切り（`max_links`境界: 負数・0・生存者ちょうど上限）
- `FileNotFoundError`は不存在として除外、その他の`OSError`は伝播する
  ことの区別
- `linked + skipped == candidates`の不変条件
- `00_Inbox/ChatGPT`内ノートへのリンク許可

### 重複ノート検出（`tests/test_duplicate_notes.py`ほか、issue #14）

- `exact_title`/`normalized_title`の分離と排他性（同一候補が両方の
  signalを持たないこと）
- `project`未設定同士を一致としないこと、`project`一致単独では
  報告しないこと
- keyword単独一致の下限（`project`併用時との閾値の違い）
- 信頼度から`recommendation`への決定的マッピング
  （`low`単独では`create`のまま、`confirm`/`choose`の境界）
- `limit`適用前の全候補から`recommendation`/`candidate_count`を
  決定すること（`truncated`の導出）
- ディレクトリ走査失敗（`InternalError`）と無一致の区別
- 候補パスが`append_inbox_note`のパス構文検証を必ず通過すること
- `00_Inbox/ChatGPT`直下限定の走査（サブディレクトリ・symlink・hidden・
  非Markdown除外、本文を読まないことの証明）
- 壊れたfrontmatter・`title`欠落時のdegradation
- レスポンスに絶対パス・本文・内部score値が含まれないこと
- `create_inbox_note`/`append_inbox_note`がこのツールの出力に
  依存せず成功すること（承認境界）

### MCP transport

- initialize
- tools/list
- tools/call
- Bearerなし拒否
- 無効Bearer拒否
- 正常Bearer許可
- 大きすぎるrequest拒否
- セッション終了後の後始末
- 複数リクエスト
- RESTとの同居

### 実機

1. MCP Inspectorから接続
2. ChatGPTデスクトップアプリへ登録
3. `/mcp`で接続状態確認
4. Vault検索
5. 検索結果からノート読み取り
6. Inboxへノート作成
7. LiveSync CLIが検出
8. PC Obsidianへ同期
9. iPhone Obsidianへ同期
10. REST curlテストが継続成功

## 18. 実装フェーズ

### Phase 1: 共通コア + REST

Status: Completed

- FastAPI
- 設定
- Bearer認証
- health
- search
- read note
- create inbox note
- Docker
- Compose
- Caddy
- GHCR
- LiveSync
- 基本テスト

### Phase 1.5: MCP MVP

Status: Completed

- MCP SDK導入
- application layer抽出
- token検証共通化
- Streamable HTTP `/mcp`
- 4つのMCP tool
- tool annotations
- server instructions
- MCP認証
- MCPテスト
- MCP Inspector
- ChatGPTデスクトップ接続
- Codex CLI接続
- IDE拡張接続
- OMV実機確認

### Phase 2: Vault構造参照

Status: Completed

- `get_vault_tree`
- `get_vault_summary`
- frontmatter集計
- タグ集計
- ページング
- `append_inbox_note`
- 大規模Vault向け改善（Phase 2の範囲外。詳細は `docs/PHASE2_PLAN.md`）

自動テスト・lint・OpenAPI回帰に加え、OMV・LiveSync・PC/iPhone Obsidianでの
実機検証（README.md「OMV verification checklist」、特に`append_inbox_note`
が使う`os.replace()`の所有者変化の確認を含む）も完了した。`compose.yaml`へ
後から追加した`mem_limit`とDockerログローテーション設定は、最新デプロイ
イメージに対する実機確認がまだ済んでいない（README.md「Known gaps」参照）。

### Phase 3: 運用強化

Status: 一部実装済み（前倒し対応。§2参照）

実装済み:

- 同時実行制限・バックプレッシャー（`app/runtime.py`の`vault_scan_limiter`。
  REST/MCP双方の全Vault走査を同じ上限で共有する）
- Inbox追記のロックタイムアウト（`InboxLockTimeoutError`、503）
- ツール利用ログ（`app/mcp_server.py`の`_log_mcp_call`。呼び出しごとに
  tool/status/duration/result_countを記録）
- `find_duplicate_candidates`（issue #14、`docs/adr/0007-*.md`。§9参照）

未着手:

- レート制限（`ErrorCode.RATE_LIMITED`は定義済みだが、これを送出する
  `GatewayError`サブクラスは存在しない）
- 一般的なリクエストタイムアウト（Inbox追記ロック以外）
- メトリクス
- 監視
- 401急増検知
- SDK更新手順
- MCP互換性試験

### Phase 4: Vault監査

- 孤立ノート
- リンク切れ
- 重複タイトル
- frontmatter欠落
- 古いInboxノート
- 監査レポート

## 19. 非機能要件

- 起動時間: 10秒以内
- 通常検索: 3秒以内を目標
- 初期メモリ: 256MB以内を目標
- 検索結果: 最大50件
- 読み取り: 標準最大1MB
- リクエスト: 標準最大2MB
- MCP request body: SDK既定より小さい値を検討し、必要最小限に固定
- タイムゾーン: Asia/Tokyo
- 文字コード: UTF-8
- 既存ノートの改行を尊重
- 新規ノートはLF
- 書き込みは原子的
- 公開インターネット非依存
- RESTとMCPで同一のセキュリティ規則

## 20. Codexへの作業ルール

### 必須

- 作業ブランチを作る
- 各変更を小さなコミットに分ける
- 実装と同時にテストを追加する
- 各変更後にpytestを実行する
- ruffを実行する
- OpenAPI回帰を確認する
- REST回帰を確認する
- MCP protocolテストを実行する
- READMEを更新する
- `.env.example`を更新する
- 依存関係を厳密に固定する
- `requirements.lock`を更新する
- セキュリティ制約を緩めない
- 絶対パスを応答へ含めない
- Vault fixture以外の実Vaultへテストで触れない

### 禁止

```text
git reset --hard
git clean -fd
force push
Vault既存ファイル変更
Vault既存ファイル削除
CouchDBへのアクセス
LiveSync設定変更
.obsidian変更
公開インターネットへのGateway露出
MCPからRESTへの内部HTTP呼び出し
同じ処理のREST/MCP二重実装
```

## 21. Phase 1.5の完了条件

以下をすべて満たすこと。

- `pytest -q`成功
- `ruff check .`成功
- OpenAPIチェック成功
- REST API回帰成功
- MCP initialize成功
- MCP tools/list成功
- 4ツールのtools/call成功
- Bearerなし接続拒否
- 無効Bearer拒否
- ChatGPTデスクトップアプリで接続成功
- Codex CLIで接続成功
- IDE拡張で接続成功
- ChatGPTからVault検索成功
- ChatGPTからノート読み取り成功
- ChatGPTからInbox保存成功
- LiveSyncでPC Obsidianへ同期
- Inbox以外へ書き込めない
- Gatewayが公開インターネットから到達不能
- ドキュメント更新完了

## 22. 最終成果物

```text
Dockerfile
compose.yaml
.env.example
FastAPI REST application
MCP Streamable HTTP application
共通application/service layer
pytest test suite
README.md
AGENTS.md
OpenAPI specification
MCP接続手順
ChatGPT desktop接続手順
Codex CLI接続手順
Caddy設定例
OMV検証手順
ADR
```

最終的に次の操作を実現する。

```text
ChatGPTデスクトップ / Codex
  ├── Vault全体を検索
  ├── 対象ノートを読み取り
  ├── Vault構造を確認
  ├── Vault概要を確認
  ├── 00_Inbox/ChatGPTへMarkdownを保存
  └── 明示的な承認後にInboxノートへ追記
```

## 23. 参考資料

- [OpenAI: Model Context Protocol](https://learn.chatgpt.com/docs/extend/mcp)
- [MCP Python SDK](https://py.sdk.modelcontextprotocol.io/)
- [MCP Python SDK: Building Servers](https://py.sdk.modelcontextprotocol.io/server/)

