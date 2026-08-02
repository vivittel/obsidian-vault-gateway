# Phase 1 — 最小API

設計は `docs/PHASE1_PLAN.md`。範囲は `docs/IMPLEMENTATION_PLAN.md` §16 Phase 1 のみ。

- [x] 0. 本プランを `docs/PHASE1_PLAN.md` として保存
- [x] 1. scaffolding（`pyproject.toml` / `.gitignore` / `.env.example` / `requirements.lock`）
- [x] 2. config + auth + エラーエンベロープ + health
- [x] 3. path_security + markdown_parser + filenames（+ テスト）
- [x] 4. search（+ テスト）
- [x] 5. notes 読み取り（+ テスト）
- [x] 6. inbox 作成（+ テスト）
- [x] 7. ログ・リクエストサイズ制限ミドルウェア
- [x] 8. Dockerfile / compose / Caddy 設定例 / `openapi.json` 出力
- [x] 9. README と OMV 検証 checklist

各項目の完了前に `.venv/bin/pytest -q` と `.venv/bin/ruff check .` を実行した（全件通過）。

## 実装しないもの（Phase 2 以降）

- `GET /api/v1/vault/tree`（§6.4）
- `GET /api/v1/vault/summary`（§6.5）
- `POST /api/v1/inbox/notes/{note_id}/append`（§6.7）
- 検索のページング（`cursor`）
- 削除・移動・リネーム・任意パス書き込み（§3 により恒久的に実装しない）

## 未解決 / ローカルで検証できないこと

- `docker compose config`、コンテナ権限テスト（§15）、LiveSync 同期確認（§15）
  → 開発機に docker が無い。README の OMV checklist に従って実機で実施する。
  代替として: `requirements.lock` からの依存インストール、`pip install --no-deps .`
  によるパッケージビルド、uvicorn 経由での `/api/v1/health` 実応答、
  `compose.yaml` の YAML 妥当性は確認済み。

## Review

### 変更ファイル

- 新規: `app/` 一式（`main.py`, `config.py`, `auth.py`, `models.py`, `exceptions.py`,
  `middleware.py`, `routers/*.py`, `services/*.py`）
- 新規: `tests/` 一式（fixture vault、conftest、各エンドポイント/セキュリティ/ログ/OpenAPI テスト）
- 新規: `Dockerfile`, `.dockerignore`, `compose.yaml`, `pyproject.toml`,
  `requirements.lock`, `.env.example`, `.gitignore`
- 新規: `README.md`, `docs/PHASE1_PLAN.md`, `docs/caddy/obsidian-api.Caddyfile`,
  `scripts/export_openapi.py`, `openapi.json`

### テスト結果

`.venv/bin/pytest -q` → 97 passed
`.venv/bin/ruff check .` → All checks passed

### 実装中に発見し、計画から逸脱した点（`docs/PHASE1_PLAN.md` §5 に記載済み）

1. `os.replace()` ではなく一時ファイル + `os.link()` を使用（上書き禁止と原子性の両立）
2. `python-frontmatter` を不採用。`loads()` が入力全体の CRLF を無条件に LF へ
   正規化してから frontmatter を検出するため、§17「改行コードを尊重する」と矛盾する。
   自前の正規表現 + `yaml.safe_load()` に置き換えた
3. ノート読み取りはパスパラメータでなくクエリパラメータ（`GET /notes?path=...`）
4. `VAULT_INBOX_RELATIVE_PATH` 設定を追加（Inbox の Vault ルート相対パス導出のため）
5. `BaseHTTPMiddleware.dispatch()` から `GatewayError` を raise しても
   `@app.exception_handler(GatewayError)` には届かず、常に汎用 500 になることが
   実装中に判明（Starlette のミドルウェアスタック順序による）。
   `RequestSizeLimitMiddleware` は例外を投げず、`JSONResponse` を直接返すよう修正した

### 未解決事項

- Docker と LiveSync の実機検証は未実施（開発機に docker が無いため）。
  README の OMV checklist に従って実機で確認が必要
- OpenAPI の自動生成に FastAPI 既定の `422`（`HTTPValidationError`）が
  バリデーションエラー時に残っている。実際のランタイムは統一エラー形式で
  `400 VALIDATION_ERROR` を返すため、ドキュメント上の記述と実挙動に軽微な不一致がある。
  §12「OpenAPI仕様整理」は計画上 Phase 3 の作業のため、Phase 1 では修正せず記録のみ残す

---

# Phase 1.5 — MCP MVP

設計は `docs/MCP_IMPLEMENTATION_PLAN.md`、`docs/adr/0001-switch-primary-interface-to-mcp.md`、
`docs/adr/0002-use-mcp-python-sdk-v2.md`。範囲は同計画の Phase 1.5 部分のみ。

- [x] S0. MCP SDK 依存追加（`mcp==2.0.0` 固定、`requirements.lock` 再生成）
- [x] S1. Bearer token 検証を純粋関数へ抽出（`verify_bearer_token`）
- [x] S2. ノート読み取りを `app/services/note_service.py` へ抽出
- [x] S3. transport 非依存の `GatewayApplication` を `app/application.py` へ抽出
- [x] S4. REST middleware を Pure ASGI 化し `/api/v1` へスコープ限定
- [x] S5. MCP server・4 ツール定義（`app/mcp_server.py`、未マウント）
- [x] S6. `/mcp` Streamable HTTP エンドポイントのマウント（`app/mcp_auth.py`、combined lifespan）
- [x] S7. ドキュメント更新（ADR-0002、README、Caddyfile、compose.yaml、計画書の訂正）

各スライスの完了前に `.venv/bin/pytest -q` と `.venv/bin/ruff check .` を実行した（全件通過）。

## 実装しないもの（Phase 2 以降、`MCP_IMPLEMENTATION_PLAN.md` §27）

- directory tree / vault summary
- append
- pagination cursor
- SQLite FTS5
- resources / prompts / MCP UI
- OAuth / Secure MCP Tunnel / 公開 plugin
- ChatGPT Web 対応
- delete / move / rename / 任意 write / attachment read

## 未解決 / 実機でのみ確認できること

- Docker・LiveSync の実機検証は未実施（開発機に docker が無いため。`docker compose config`
  含む）→ README「OMV verification checklist」に従って実機で実施する
- ChatGPT デスクトップ・Codex CLI・IDE 拡張との実接続確認は未実施 → README
  「ChatGPT desktop app」「Codex CLI / Codex IDE extension」節の手順で確認する
- `MCP_IMPLEMENTATION_PLAN.md` §5 の「Codex クライアントとの接続確認」も同様に未実施

## Review

### 変更ファイル

- 新規: `app/application.py`, `app/services/note_service.py`, `app/mcp_server.py`,
  `app/mcp_auth.py`
- 新規: `tests/test_mcp_sdk.py`, `tests/test_application.py`, `tests/test_middleware.py`,
  `tests/test_mcp_tools.py`, `tests/test_mcp_protocol.py`, `tests/test_mcp_auth.py`,
  `tests/test_mcp_lifespan.py`, `tests/test_rest_regression.py`
- 新規: `docs/adr/0002-use-mcp-python-sdk-v2.md`
- 変更: `app/main.py`（REST/MCP を対等な Mount で合成する Starlette トップレベル app へ）,
  `app/auth.py`, `app/config.py`, `app/middleware.py`,
  `app/routers/{health,search,notes,inbox}.py`, `app/models.py`
- 変更: `pyproject.toml`, `requirements.lock`, `scripts/export_openapi.py`, `openapi.json`
- 変更: `tests/conftest.py`
- 変更: `README.md`, `AGENTS.md`, `.env.example`, `compose.yaml`,
  `docs/caddy/obsidian-api.Caddyfile`, `docs/IMPLEMENTATION_PLAN.md`,
  `docs/MCP_IMPLEMENTATION_PLAN.md`

### テスト結果

`.venv/bin/pytest -q` → 230 passed
`.venv/bin/ruff check .` → All checks passed

### 実装中に発見し、計画から逸脱した点

1. MCP SDK は v1 系ではなく v2 系（`mcp==2.0.0`）を採用（ADR-0002、A1）
2. `note_service.py` を切り出す構成に変更（当初の MCP §7 概念コードは
   GatewayApplication へ直書きする例だった）（A2）
3. REST middleware を Pure ASGI 化し `/api/v1` へスコープ限定（A3）
4. MCP アクセスログには `note_path` を出さない（U1、A4）。REST のアクセスログは
   引き続き相対パスを記録するため、transport 間でログ項目が完全には一致しない
5. `PROXY_NETWORK` 環境変数の追加により `compose.yaml` へ変更が入った
   （A11、「Compose 変更は原則不要」との記載から逸脱。`ports:` は追加していない）
6. `MCP_ALLOWED_HOSTS` 必須設定を追加（DNS rebinding protection の allowlist。
   計画書に記載の無い実装必須事項 — 未設定だと Caddy 経由の全リクエストが拒否される）
7. `docs/caddy/obsidian-api.Caddyfile` へ `/mcp` と `/mcp/*` を追加（U4、A6）
8. Starlette の `Mount` は末尾スラッシュ無しの基点パスへ一切マッチしないため、
   `/mcp` 単体を `/mcp/` へリダイレクトする明示的な `Route` を追加した
   （計画・ADR のいずれにも記載が無かった実装時の発見）
9. REST と MCP を同一 FastAPI インスタンスへ `mount()` せず、対等な `Mount` を持つ
   別の Starlette インスタンスで合成する構成にした。REST の例外ハンドラが MCP の
   レスポンスを構造的に書き換えられないようにするための変更（MCP_IMPLEMENTATION_PLAN
   §15 の懸念に対する対応）
10. `mcp.session_manager.run()` はインスタンスにつき一度しか呼べないため、
    テストの大半は session-scoped な `mcp_client` fixture を共有し、独立した
    lifespan が必要な一部テスト（`test_mcp_lifespan.py`、高レベル `Client` テスト、
    GET/SSE テスト）は独立した throwaway `MCPServer` を都度構築する
    （`app/mcp_server.py` の `build_mcp_transport()` を共用）
11. modern（2026-07-28）プロトコルは `MCP-Protocol-Version` / `Mcp-Method` /
    `Mcp-Name` ヘッダーと `params._meta` エンベロープを要求する。いずれも計画書に
    記載が無く、インストール済み SDK のソースを直接読んで発見した

### 未解決事項

- Docker と LiveSync の実機検証は未実施（開発機に docker が無いため）
- ChatGPT デスクトップ・Codex CLI・IDE 拡張との実接続確認は未実施
- §8 の未確定事項のうち、実装外の運用判断（実際の Caddy 設定投入、OMV での
  `MCP_ALLOWED_HOSTS` / `PROXY_NETWORK` の実値設定）は実機作業側の対応が必要

---

# Phase 2 — Vault構造参照・Inbox追記

設計は `docs/PHASE2_PLAN.md`、`docs/adr/0003-allow-os-replace-for-inbox-append.md`。
範囲は `docs/IMPLEMENTATION_PLAN.md` Phase 2 のみ。実装順はPHASE2_PLAN.md記載の
順（Tree→Summary→Cursor→Append）ではなく、カーソル基盤への依存関係から
Cursor→Tree→Summary→Appendの順に変更した（承認済み）。

- [x] P2.1. カーソル基盤（`cursor_service.py`、`INVALID_CURSOR`、検索ページング）
- [x] P2.2. Vault Tree（ページング込み。`normalise_relative_dir`の絶対パス
  受理バグ修正を先に実施）
- [x] P2.3. Vault Summary（タグ・フォルダ集計、`skipped_count`）
- [x] P2.4. Inbox Append（`resolve_inbox_append_path`、Inbox単位ロック、
  `os.replace()`許可のADR-0003、`resolve_inbox_write_path`の削除）
- [x] P2.5. Documentation and Deployment（本セクション）

各スライスの完了前に `.venv/bin/pytest -q`、`.venv/bin/ruff check .`、
`.venv/bin/python scripts/export_openapi.py --check` を実行した（全件通過）。

## 実装しないもの（Phase 2でも対象外）

- 大規模Vault向け改善（SQLite FTS5等） — `docs/IMPLEMENTATION_PLAN.md` §18
- 削除・移動・リネーム・任意パス書き込み（§3 により恒久的に実装しない）
- レート制限・同時実行制限・メトリクス — Phase 3
- Vault監査（孤立ノート・リンク切れ） — Phase 4

## 未解決 / 実機でのみ確認できること

- OMV・LiveSync・PC/iPhone Obsidianでの実機検証は未実施（開発機に docker が
  無いため）→ README「OMV verification checklist」に従って実機で実施する
- 特に、`append_inbox_note`が使う`os.replace()`による所有UID/GID変化が
  LiveSync・両Obsidianの読み書きを妨げないかの確認（ADR-0003）が未実施。
  この確認が完了するまで `docs/IMPLEMENTATION_PLAN.md` のPhase 2は
  `Implemented — Deployment verification pending` のままとし、`Completed`
  へは変更しない

## Review

### 変更ファイル

- 新規: `app/services/cursor_service.py`, `app/services/vault_service.py`,
  `app/routers/vault.py`
- 新規: `docs/adr/0003-allow-os-replace-for-inbox-append.md`
- 新規: `tests/test_cursor_service.py`, `tests/test_vault.py`
- 変更: `app/application.py`（`get_vault_tree`/`get_vault_summary`/
  `append_inbox_note`追加、cursor共通ヘルパー`_cursor_offset`/`_next_cursor`）
- 変更: `app/exceptions.py`（`INVALID_CURSOR`/`NOTE_MODIFIED`追加）
- 変更: `app/services/path_security.py`（`resolve_read_dir`/`iter_directory`/
  `VaultEntry`/`WalkStats`追加、`resolve_inbox_append_path`追加、
  `resolve_inbox_write_path`削除、`normalise_relative_dir`の絶対パスバグ修正）
- 変更: `app/services/search_service.py`（`offset`/`SearchPage`）
- 変更: `app/services/inbox_service.py`（`append_inbox_note`と関連ヘルパー追加）
- 変更: `app/models.py`（`VaultTreeEntry`/`VaultTreeResponse`/`VaultNameCount`/
  `VaultSummaryResponse`/`InboxNoteAppendRequest`/`AppendedNoteResponse`追加）
- 変更: `app/routers/search.py`（`cursor`パラメータ）、`app/routers/inbox.py`
  （`POST /inbox/notes/append`追加）
- 変更: `app/mcp_server.py`（`get_vault_tree`/`get_vault_summary`/
  `append_inbox_note`ツール追加、`SERVER_INSTRUCTIONS`修正）
- 変更: `app/main.py`（vaultルーター登録）、`app/middleware.py`
  （`appended_note`ログフック）
- 変更: `openapi.json`（再生成）
- 変更: `README.md`, `docs/IMPLEMENTATION_PLAN.md`, `docs/PHASE1_PLAN.md`,
  `docs/PHASE2_PLAN.md`
- 変更: `tests/test_application.py`, `tests/test_inbox.py`,
  `tests/test_logging.py`, `tests/test_mcp_protocol.py`,
  `tests/test_mcp_tools.py`, `tests/test_openapi.py`,
  `tests/test_path_security.py`, `tests/test_search.py`

### テスト結果

`.venv/bin/pytest -q` → 382 passed
`.venv/bin/ruff check .` → All checks passed
`.venv/bin/python scripts/export_openapi.py --check` → up to date

### 実装中に発見し、計画から逸脱した点

1. `docs/PHASE2_PLAN.md`記載のP2.1〜P2.5順序を変更し、Cursor基盤を先頭に
   （ユーザー承認済み。§9参照）
2. `normalise_relative_dir`（既存・Phase 1由来）が先頭`/`を正規化前に
   `strip("/")`していたため、絶対パス`/Knowledge`を相対パス`Knowledge`として
   受理してしまうバグを発見。search の`folder`パラメータにも影響していた
   既存の回帰。P2.2で修正し、`tests/test_path_security.py`・
   `tests/test_search.py`にテストを追加
3. `Path.is_dir(follow_symlinks=False)`はPython 3.13以降の機能で、CIは
   3.12/3.13両対応のため使用できず、symlink済み確認後の無引数`is_dir()`に
   修正（`app/services/path_security.py`の`iter_directory`）
4. カーソルのHMAC鍵は`API_TOKEN`から導出し、fingerprint用・署名用に異なる
   purposeラベルでサブキーへ分離した（新規env varを追加しないため。
   `API_TOKEN`ローテーション時は既存カーソルが無効になる仕様として明文化）
5. `docs/PHASE2_PLAN.md`§6の排他制御を「ファイル単位」から「Inbox単位の
   単一`.append.lock`」に変更。ファイル単位ロックは`os.replace()`を挟むと
   inodeが変わり機能しないため（ADR-0003の代替案検討にも記録）
6. `os.replace()`の無条件禁止（`docs/IMPLEMENTATION_PLAN.md`§12、
   `docs/PHASE1_PLAN.md`§4.3）に対し、追記（既存ファイル更新）のみを対象と
   する例外をADR-0003として記録。新規作成での禁止は変更していない
7. `NOTE_MODIFIED`（409）エラーコードを追加。計画書には無いが、追記の
   検証後変更検出に必須と判断した（§10の「対象が検証時から変更されていない
   ことを確認」の失敗時に返す）
8. `resolve_inbox_write_path`（Phase 1由来、呼び出し元0件の未使用関数）を
   `rg`で確認の上削除し、`resolve_inbox_append_path`に置き換えた
9. `SERVER_INSTRUCTIONS`の「create_inbox_note is the only write tool」という
   記述が`append_inbox_note`追加により誤りになるため修正。512文字制約の
   テストは維持

### 未解決事項

- OMV・LiveSync・PC/iPhone Obsidianでの実機検証は未実施（上記「未解決 /
  実機でのみ確認できること」参照）
- `docs/PHASE1_PLAN.md:98`は削除済みの`resolve_inbox_write_path()`に言及した
  ままだが、この記述はPhase 1時点から実装と一致していなかった（実際は
  `inbox_service.py`が直接パスを組んでいた）既存の不整合であり、Completedな
  historical documentのため今回は変更していない
