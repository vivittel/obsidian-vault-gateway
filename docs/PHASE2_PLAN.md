# Obsidian Vault Gateway Phase 2 実装計画

> Status: Implemented — Deployment verification pending  
> Prerequisite: Phase 1.5 completed  
> Primary interface: MCP  
> Secondary interface: REST API

## 1. 目的

Vaultを安全に段階参照できる機能と、Inbox内ノートへの追記機能を追加する。

追加対象：

- Vault直下・指定フォルダ直下の一覧取得
- Vault全体の集計概要
- 検索結果のカーソルページング
- `00_Inbox/ChatGPT`内ノートへの追記
- MCPとRESTで同一のapplication/service層を使用

## 2. 追加するMCPツール

| Tool | 種別 | 承認 |
|---|---|---|
| `get_vault_tree` | read | auto |
| `get_vault_summary` | read | auto |
| `append_inbox_note` | write | prompt |

既存4ツールと合わせて、Phase 2完了後は7ツールとなる。

## 3. Vault Tree

### MCP

```text
get_vault_tree(
    folder: string | null = null,
    limit: int = 100,
    cursor: string | null = null
)
```

### REST

```text
GET /api/v1/vault/tree
```

Query parameters:

```text
folder
limit
cursor
```

### 動作

- `folder=null`ではVault直下を取得
- 指定フォルダの直接の子だけを返す
- 下位階層は追加呼び出しで段階取得
- フォルダを先、ノートを後にして安定ソート
- 隠しパス、シンボリックリンク、非Markdownファイルを除外
- 応答はVault相対パスだけを含む

### 応答案

```json
{
  "folder": "",
  "entries": [
    {
      "type": "folder",
      "name": "Knowledge",
      "path": "Knowledge"
    },
    {
      "type": "note",
      "name": "Home.md",
      "path": "Home.md",
      "modified_at": "2026-08-02T12:00:00+09:00"
    }
  ],
  "next_cursor": null
}
```

## 4. Vault Summary

### MCP

```text
get_vault_summary(top_tags_limit: int = 20)
```

### REST

```text
GET /api/v1/vault/summary
```

### 集計対象

- Markdownノート総数
- Markdownノート合計サイズ
- ノートを含むフォルダ数
- トップレベルフォルダ別ノート数
- frontmatterタグ別ノート数
- 最終更新日時

### 制約

- ノート本文、タイトル一覧、絶対パスを含めない
- 隠しパスとシンボリックリンクを除外
- タグ数は上限を設ける
- 同数の場合も安定した順序で返す
- 読み取れないファイルは安全にスキップし、件数だけ記録する

## 5. 検索ページング

既存の `search_notes` に `cursor` を追加する。

```text
search_notes(
    query,
    folder,
    tags,
    limit,
    cursor
)
```

### カーソル仕様

- URL-safeな不透明文字列
- operation、offset、検索条件のfingerprintを保持
- APIトークン由来のHMACで改ざんを検出（fingerprint用・署名用は別のpurposeラベルで
  導出したサブキーを使い、鍵を直接共用しない）
- 別の検索条件へのカーソル流用を拒否
- 不正または破損したカーソルは `INVALID_CURSOR`
- Vaultが途中で変更された場合、重複・欠落が起こり得るbest-effort paginationとする
- カーソル内に検索語そのものを保存しない
- `limit`（ページサイズ）はfingerprintの対象条件に含めない。ページサイズは
  結果集合の同一性を変えないため、ページ間で`limit`を変えてもカーソルは有効
- **`API_TOKEN`をローテーションすると、発行済みのカーソルはすべて無効になる**
  （鍵がAPIトークンから導出されるため）。クライアントは`INVALID_CURSOR`を
  受け取った場合、先頭ページから再取得する

ツリー一覧にも同じカーソル基盤を利用する。

## 6. Inbox追記

### MCP

```text
append_inbox_note(
    path: string,
    content: string
)
```

### REST

```text
POST /api/v1/inbox/notes/append
```

Request:

```json
{
  "path": "00_Inbox/ChatGPT/Example.md",
  "content": "追記するMarkdown"
}
```

### 許可条件

- 対象は `VAULT_INBOX_RELATIVE_PATH` 直下の `.md`のみ
- 通常ファイルであること
- 既存ファイルであること
- シンボリックリンクではないこと
- 隠しファイルではないこと
- Inbox外、サブディレクトリ、絶対パス、`..`を拒否
- 空の追記を拒否
- 追記後のファイルサイズに上限を設ける

Phase 1.5以前のファイルには作成元を証明する情報がないため、「Gateway作成ノート」は技術的には「専用の `00_Inbox/ChatGPT` ディレクトリで管理されるノート」と定義する。

### 書き込み方法

1. 対象を安全に解決（`resolve_inbox_append_path`）
2. Inbox単位の排他制御 — `.append.lock`を`O_NOFOLLOW`で開き通常ファイルである
   ことを確認した上で`flock`。対象ファイル単位のロックにはしない（`os.replace()`
   を挟むとinodeが変わり、ファイル単位ロックは機能を失う）
3. 対象を`O_NOFOLLOW`で開き、同じfdから`fstat`確認・内容読み取りまでを行う
   （名前で再度開き直さない。検証後にホスト側がsymlinkへ差し替える競合を
   避けるため）
4. 改行形式を維持して追記内容を連結
5. Inbox内の隠し一時ファイルへ全内容を書き込み、元ファイルのmodeを引き継ぐ
6. `fsync`
7. `os.lstat`で対象がsymlinkでないことを確認し、device/inode/mtime/sizeが
   検証時から変更されていないことを確認
8. `os.replace()`で原子的に置換
9. ディレクトリを`fsync`
10. 一時ファイルとロックを必ず解放

`os.replace()`は新規作成では引き続き禁止する。追記では、部分書き込みを防ぐための
既存ファイル更新としてのみ使用する（詳細: `docs/adr/0003-allow-os-replace-for-
inbox-append.md`）。

**排他制御の限界**: `.append.lock`のflockはこのGatewayプロセス内のリクエスト
間のみ有効。ObsidianやLiveSync CLIなどホスト側の書き込みはこのロックの対象外
で、手順7の再検証で検出するのみ（防止ではない）。手順7と8の間には残余の
TOCTOU窓が残るが、`os.replace()`自体は宛先がsymlinkでもそれを追従せず置換
するだけなので、任意ファイルへの書き込みには至らない。

**所有者への影響**: `os.replace()`は一時ファイルのinodeをそのまま宛先へ入れ替
えるため、追記後のノートの所有UID/GIDは一時ファイルを作成したコンテナプロセス
の所有者になる。modeは明示的にコピーするが、`compose.yaml`の`cap_drop: ALL`
により`CAP_CHOWN`がなく`os.fchown`で元の所有者へ戻すことはできない。実機での
影響確認は§11参照。

### 応答

```json
{
  "id": "00_Inbox/ChatGPT/Example.md",
  "path": "00_Inbox/ChatGPT/Example.md",
  "modified_at": "2026-08-02T12:00:00+09:00",
  "appended_bytes": 128
}
```

ノート本文は応答やログへ含めない。

## 7. レイヤー構成

```text
MCP tools / REST routers
          │
          ▼
GatewayApplication
├── get_vault_tree
├── get_vault_summary
├── search_notes(cursor追加)
└── append_inbox_note
          │
          ▼
Services
├── cursor_service.py
├── vault_service.py
├── search_service.py
├── inbox_service.py
└── path_security.py
```

MCPからRESTを呼ばず、両transportが同じapplication/service関数を使用する。

## 8. 想定変更ファイル

```text
app/application.py
app/models.py
app/mcp_server.py
app/exceptions.py
app/routers/vault.py
app/routers/inbox.py
app/routers/search.py
app/services/cursor_service.py
app/services/vault_service.py
app/services/search_service.py
app/services/inbox_service.py
app/services/path_security.py

tests/test_vault.py
tests/test_search.py
tests/test_inbox.py
tests/test_mcp_tools.py
tests/test_mcp_protocol.py
tests/test_rest_regression.py

openapi.json
README.md
docs/IMPLEMENTATION_PLAN.md
docs/PHASE2_PLAN.md
```

## 9. 実装スライス

Tree・Search双方がカーソルページングに依存するため、共通カーソル基盤を最初に
実装する。Treeを最初からページング前提で設計し、`next_cursor`を一度null固定
で出してから後付けする手戻りを避ける。

### P2.1: Cursor Pagination

- 共通カーソルcodec（fingerprint/署名のサブキー分離、厳密なデコード検証）
- `INVALID_CURSOR`追加
- 検索ページング（`search_service`に`offset`/`SearchPage`を追加）
- 条件不一致・改ざん・境界値カーソル試験

### P2.2: Vault Tree（ページング込み）

- `normalise_relative_dir`の絶対パス受理バグ修正（先頭`/`を正規化前に拒否）
- モデルとservice実装（最初からP2.1のカーソル基盤で段階取得）
- application層追加
- REST/MCP追加
- パスセキュリティ試験
- 安定ソート試験

### P2.3: Vault Summary

- Vault集計service
- タグ・フォルダ集計、`iter_vault_notes`への`skipped_count`集計追加
- REST/MCP追加
- 大量タグと破損frontmatterの試験

### P2.4: Inbox Append

- 追記専用パス解決（`resolve_inbox_append_path`。Inbox直下1階層のみ、既存確認）
- サイズ制限（追記前・追記後の両方を判定）
- Inbox単位の排他制御（`.append.lock`、`O_NOFOLLOW`）と原子的更新（ADR-0003）
- REST/MCP追加
- 同時追記・障害時復旧・symlink競合の試験

### P2.5: Documentation and Deployment

- README更新
- MCP設定例更新
- OpenAPI再生成
- 実機検証チェックリスト追加（mode・所有者・LiveSync・両Obsidianの確認を含む）
- `IMPLEMENTATION_PLAN.md`のPhase 1.5をCompletedへ更新。Phase 2は
  `Implemented — Deployment verification pending`とし、OMV・LiveSync・PC/iPhone
  Obsidianでの実機確認が完了するまで`Completed`へは変更しない

各スライスを独立した小さなコミット／PRとして実装する。

## 10. 必須テスト

### セキュリティ

- traversal拒否
- 絶対パス拒否
- Windowsパス拒否
- 隠しパス拒否
- symlink拒否
- Inbox外追記拒否
- 非Markdown追記拒否
- サブディレクトリ追記拒否
- 絶対ホストパスをエラーへ含めない
- 本文、検索語、トークンをログへ含めない

### 機能

- Treeの安定ソート
- 空フォルダ
- 日本語パス
- Summary集計
- 重複タグの正規化
- 正常ページング
- カーソル改ざん
- 別検索条件へのカーソル流用
- LF/CRLF追記
- 追記後サイズ上限
- 同時追記で内容を失わない
- 一時ファイルを残さない

### 回帰

```text
pytest -q
ruff check .
python scripts/export_openapi.py --check
```

既存REST、MCP認証、4ツール、`/mcp`と`/mcp/`の動作を維持する。

## 11. OMV実機検証

1. GHCRのPhase 2イメージをpull
2. Compose再作成
3. REST health
4. MCP tools/listで7ツールを確認
5. Vault Treeを段階取得（`limit`を小さくして全件を重複・欠落なく走査）
6. Vault Summaryを取得
7. 検索を複数ページ取得（同様に重複・欠落なく走査）
8. Phase 2検証用ノートを新規作成
9. 同じノートへ追記
10. 追記前後で対象ノートのmode・所有UID・所有GIDを`ls -ln`で比較し、変化の
    有無を記録する（`docs/adr/0003-allow-os-replace-for-inbox-append.md`が
    指摘する`os.replace()`の既知の副作用）
11. PC・iPhone Obsidianへ追記が同期されることを確認
12. 追記後のノートがLiveSyncから正常に読み取り・同期できることを確認
13. 追記後のノートをPC・iPhone Obsidianから編集・保存できることを確認
    （手順10で所有者が変わった場合に書き込み権限を失っていないかの確認）
14. Inbox外への追記が拒否されることを確認
15. 検証用ノートをObsidianから手動削除

Gatewayには削除機能を追加しない。

手順10・12・13が問題なく通過するまで、`docs/IMPLEMENTATION_PLAN.md`のPhase 2
は`Completed`にしない（`Implemented — Deployment verification pending`の
まま）。問題が出た場合は追記方式自体の再設計（例: in-place `pwrite`による
追記）が必要になる。

## 12. 完了条件

- 7つのMCPツールが公開される
- Treeを安全に段階取得できる
- Summaryが本文を露出せず取得できる
- 検索とTreeでカーソルページングできる
- Inbox内ノートへ安全に追記できる
- 書き込み時に明示的な承認が要求される
- Inbox外への書き込みが不可能
- 全自動テスト、lint、OpenAPI回帰が成功

以下が完了するまで、Phase 2は`Implemented — Deployment verification
pending`のままとし、`Completed`へは変更しない:

- OMV、Caddy、ChatGPT、Codex、LiveSyncの実機確認が成功
- 追記による所有者変化がLiveSync・PC/iPhone Obsidianの読み書きを妨げないこと
  を実機で確認済み
- Phase 1およびPhase 1.5の機能・セキュリティ制約に回帰がない
