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

# 運用向けログ形式の整備（Phase 2 範囲内の欠陥修正）

新規フェーズではなく、`docs/IMPLEMENTATION_PLAN.md`§14と
`docs/MCP_IMPLEMENTATION_PLAN.md`§16が要求していた項目が実際には出力されて
いなかったことの修正。コンテナログの実出力が`request` / `mcp_call` /
`Terminating session: None`だけの状態だった。

- [x] 0. 原因特定（`MCPServer`生成の副作用である`logging.basicConfig(
      format="%(message)s")`がrootロガーを掌握していた。アプリ側に logging
      設定は存在しなかった）
- [x] 1. `app/logging_config.py`新規（`PlainLogFormatter` +
      `configure_logging()`）
- [x] 2. `app/mcp_server.py`で`MCPServer`生成の直前に`configure_logging()`
- [x] 3. RESTアクセスログに`transport=rest`と`result_count`を追加（§14）
- [x] 4. `/api/v1/health`のアクセスログをDEBUGへ降格
- [x] 5. MCPアクセスログに`method=tools/call`を追加（§16）
- [x] 6. `compose.yaml`にログローテーション（`10m × 3`）
- [x] 7. `tests/test_log_format.py`新規（レンダリング後の1行を検証）
- [x] 8. README「Logging」節、§14・§16への出力形式注記、`.env.example`

## Review

### 変更ファイル

- 新規: `app/logging_config.py`, `tests/test_log_format.py`
- 変更: `app/mcp_server.py`（`configure_logging()`呼び出し、`method`フィールド）
- 変更: `app/middleware.py`（`transport` / `result_count` / health DEBUG /
  `_route_path()`）
- 変更: `app/routers/search.py`, `app/routers/vault.py`
  （`request.state.result_count`）
- 変更: `app/main.py`（import順の理由コメントのみ）
- 変更: `compose.yaml`, `.env.example`, `README.md`,
  `docs/IMPLEMENTATION_PLAN.md`, `docs/MCP_IMPLEMENTATION_PLAN.md`
- 変更: `tests/test_middleware.py`（health用テストの追加、既存テストの
  対象ルート変更）

### テスト結果

- `.venv/bin/pytest -q` → 407 passed（うち新規24件）
- `.venv/bin/ruff check .` → All checks passed
- `.venv/bin/python scripts/export_openapi.py --check` → up to date
  （`request: Request`追加はスキーマに影響しない）
- `docker compose config`は開発機にdockerが無いため未実行。`compose.yaml`は
  PyYAMLでパースし`logging`ブロックを確認した
- ローカルuvicornで実出力を目視確認（`awk '$3=="mcp" {print $5}'`が通ること、
  `LOG_LEVEL=DEBUG`でhealth行が出ること、`Terminating session`が消えたこと）

### 実装中に発見し、計画から逸脱した点

1. **`configure_logging()`をSettings非依存にした。** 計画では
   `configure_logging(settings)`だったが、`app/mcp_server.py`のimport時に
   `get_settings()`を呼ぶとテストのcollectionが壊れる（テストは環境変数を
   設定する前にこのモジュールをimportする）。加えてSettings検証失敗自体を
   ログに出したいので、`TZ`/`LOG_LEVEL`を環境変数から直接読む形が本来正しい。
   デフォルト値のみ`Settings`と二重管理になっている
2. **`dictConfig`を使わず素の logging API を使った。** `root`を明示指定した
   `dictConfig`はrootのハンドラリストを置換するため、アプリを最初にimport
   したテストのpytest caplogハンドラを消してしまう
3. **`uvicorn.access`を統合対象から外し、明示的に無効化した。** 統合テストで
   検出した回帰: `propagate=True`にすると`--no-access-log`を無効化してしまい、
   生のクエリ文字列（`q=RTX 5070`）がログに出る。§14の「記録しない: 検索語」
   違反。`app/logging_config.py`側でも落とすようにしたので、この不変条件が
   Dockerfileのフラグだけに依存しなくなった
4. **`route`が`/api/v1`プレフィックス無しで記録されていた既存バグを修正。**
   この FastAPI 版は`include_router(prefix=...)`をマッチ時に`_IncludedRouter`
   で適用するため`scope["route"].path`は`/search`のまま。そのため
   health判定（`/api/v1/health`との比較）が実アプリで一致せず、DEBUG降格が
   効いていなかった。`AccessLogMiddleware._route_path()`で解決
5. **タイムスタンプの区切りを空白ではなく`T`にした。** 空白だと日時が2
   フィールドに割れて以降の全カラムがずれ、「1カラム = 1 awkフィールド」
   （平文を選んだ理由そのもの）が崩れる

### 未解決事項

- MCPのエラー行（`mcp tools/call read_note error 0.1ms`）に**どのエラーか**が
  出ない。`GatewayError.log_detail`が無い場合（`NoteNotFoundError`など）は
  `mcp_tool_error`レコードも出ないため、`code`がどこにも残らない。§14/§16は
  エラーコードの記録を要求していないので今回は変更していないが、運用上は
  `mcp_call`のerror側extraに`code`を足す価値がある
- `docs/PHASE1_PLAN.md:178`はuvicornアクセスログの無効化を`--no-access-log`
  のみと説明しており、`app/logging_config.py`側の二重化に言及していない。
  Completedなhistorical documentのため変更していない
- ローテーションの実効確認（`docker inspect obsidian-api --format
  '{{json .HostConfig.LogConfig}}'`）は実機で未実施

# Vaultスキャンの同時実行制限をREST/MCP間で共有（Phase 3 前倒し対応）

新規フェーズではなく、`docs/IMPLEMENTATION_PLAN.md`§18 Phase 3の「同時実行
制限」の一部を前倒しで修正。RESTの`search`/`vault/summary`は`app/main.py`が
`CapacityLimiter(2)`を`rest_app.state`に持たせて全文スキャンを2並列に制限
していたが、MCPの`search_notes`/`get_vault_summary`は`def`のまま
`GatewayApplication`を直接呼んでおり、インストール済みSDK
（`mcp/server/mcpserver/utilities/func_metadata.py`）のディスパッチを確認
した結果、limiter未指定の`anyio.to_thread.run_sync`でデフォルトスレッド
プール（40トークン）に流れ、この制限を完全に回避していることが分かった。
REST/MCP合計のスキャン数は2に制限されていなかった。

- [x] 0. 原因特定（MCPの2ツールが`def`のまま`_application()`を直接呼び、
      SDKがlimiter未指定の`anyio.to_thread.run_sync`でデフォルトプールへ
      流していた）
- [x] 1. `app/runtime.py`新規: `vault_scan_limiter`をプロセス全体で1個だけ
      保持
- [x] 2. `app/main.py`: limiter構築と`rest_app.state`経由の参照を削除
- [x] 3. `app/routers/search.py`, `app/routers/vault.py`:
      `runtime.vault_scan_limiter`を直接参照するよう変更
- [x] 4. `app/mcp_server.py`: `search_notes`/`get_vault_summary`のみ
      `async def`化し、`GatewayApplication`呼び出しだけを同じlimiter経由
      でスレッドへオフロード
- [x] 5. `tests/test_vault_scan_concurrency.py`拡張: REST/MCP混在の相互
      排他テスト2件、`/health`非枯渇テスト1件、形状チェック2件、capacity
      guardテスト1件

## 実装しないもの（対象外、別PRへ）

- MCPのエラーログに`code`が残らない問題（`_McpCall`のerror側extra）
- 413がAccessLogに残らない問題（`RequestSizeLimitMiddleware`と
  `AccessLogMiddleware`の登録順）
- `serverInfo.version`が空文字列のまま
- searchのskipped件数が捨てられている問題
- ドキュメントのPhaseステータス不整合、`AUTH_ENABLED`のADR化

## Review

### 変更ファイル

- 新規: `app/runtime.py`
- 変更: `app/main.py`（limiter構築の削除、未使用になった`anyio` importの
  削除）
- 変更: `app/routers/search.py`, `app/routers/vault.py`
  （`runtime.vault_scan_limiter`参照。`get_vault_summary`から未使用の
  `request: Request`引数を削除）
- 変更: `app/mcp_server.py`（`search_notes`/`get_vault_summary`を
  `async def`化）
- 変更: `tests/test_vault_scan_concurrency.py`（新規6テスト、既存2テストの
  拡張、モジュールdocstring更新）

### テスト結果

- `.venv/bin/ruff check .` → All checks passed
- `.venv/bin/pytest -q` → 497 passed（既存491 + 新規6）
- `.venv/bin/python scripts/export_openapi.py --check` → up to date
  （REST側の変更はドキュメント化されたパラメータに影響しない）
- 修正前の挙動を確認するため`app/mcp_server.py`のみ一時的に
  `git stash`で戻し、新設した3件の振る舞いテストが実際に失敗することを
  確認した
  - `test_mcp_scan_waits_while_rest_holds_both_limiter_tokens`:
    MCP呼び出しが即座に開始してしまう（limiter未共有を検出）
  - `test_rest_scan_waits_while_mcp_holds_both_limiter_tokens`:
    REST summaryが即座に開始してしまう
  - `test_health_stays_responsive_while_mcp_scans_are_blocked`:
    `TimeoutError`で失敗（2.5秒で終了）
- `git stash pop`で修正を復元後、同3件を含む全11件が1.25秒で成功
- コンテナ実機での負荷試験は未実施（開発機にDocker無し）。「MCPスキャンが
  デフォルトプールを消費しうる」ことはインストール済みSDKのソースから確認
  済みだが、実際に40並列に達すること・メモリが40倍になること・512MBの
  `mem_limit`に達することは未検証であり、そのようには主張していない

### 実装中に発見し、計画から逸脱した点

1. **`get_vault_summary`から`request: Request`引数を削除した。**
   limiter参照が`request.app.state.vault_scan_limiter`から
   `runtime.vault_scan_limiter`（モジュール経由）に変わったことで、
   `request`はこの関数内で完全に未使用になった。`result_count`を設定して
   いるのは`get_vault_tree`のみで、`get_vault_summary`は元から設定して
   いない
2. **limiterをモジュール経由（`from app import runtime`;
   `runtime.vault_scan_limiter`）で参照する形にした。**
   `from app.runtime import vault_scan_limiter`だと各呼び出し元が別名で
   オブジェクトを束縛するため、将来の再代入やmonkeypatchでREST/MCPが
   別々のlimiterを参照する状態に分岐できてしまう。単一の参照経路を保証
   するため、全呼び出し元がモジュール属性を参照する形に統一した
3. **`/health`非枯渇テストで、テストダブルの`release.wait(timeout=...)`と
   テスト側の`anyio.fail_after(...)`を同じ秒数にしてはいけないことが判明
   した。** 最初の実装（両方5秒）では、修正前のコードでもダブル自身の
   タイムアウトが偶然テストの合否判定と同時に発生し、`/health`が実際には
   約5秒ブロックされていたにもかかわらずテストが誤ってパスした。さらに、
   `release.set()`を`async with anyio.create_task_group()`ブロックの外
   （`finally`）に置いたところ、`to_thread.run_sync`はデフォルトで
   `cancellable=False`のため子タスクがキャンセルされてもワーカースレッド
   は自然終了までブロックし続け、`__aexit__`がそれを待つ間`release.set()`
   に到達できず、ダブルの30秒セーフティネットに達するまでテスト全体が
   止まる、という制御フロー上のデッドロックも発見した。`release.set()`を
   `async with tg:`ブロックの内側・`fail_after`の`finally`に置き、ダブル
   のタイムアウトはテスト側の観測窓（2秒）よりずっと長い（30秒、実質
   到達しないセーフティネット）に設定して両者を分離した

### 未解決事項

- A2〜A5相当（MCPエラーログの`code`欠落、413のAccessLog欠落、
  `serverInfo.version`空文字列、searchのskipped件数）は範囲外として
  別PRに残す
- ドキュメント側（Phaseステータス矛盾、`AUTH_ENABLED`のADR化）も別PRに
  残す
- 複数uvicornワーカー構成にした場合、このlimiterは「プロセス全体」では
  なく「ワーカーごと」に2並列になる。現行Dockerfileは単一ワーカーのため
  影響しないが、`app/runtime.py`のdocstringに明記した
- 実機でのVaultスキャン負荷試験は未実施

# MCPエラーコードのログ欠落・413のAccessLog欠落・MCPバージョン欠落の修正

前回PR（Vaultスキャンのlimiter共有）に続き、レビューで洗い出した項目の
優先度順（A3・A4・A2）に対応。いずれも1〜5行規模の修正だが、ログの
「LogRecordに属性はあるがformatterが捨てて実ログには出ない」という
このリポジトリで過去に実際起きた回帰パターンがあるため、`caplog`での
属性検証だけでなく`tests/test_log_format.py`での実レンダリング検証も
必須とした。

- [x] 0. 原因特定
  - A3: `_McpCall.__exit__`は`mcp_call`を常に出すが`code`を含めておらず、
    補助的な`mcp_tool_error`は`status_code>=500`または`log_detail`が
    truthyの場合しか出ない。`NoteNotFoundError()`はどちらも満たさないため、
    最も一般的な拒否パターンで`code`がログのどこにも残らない
  - A4: `app/main.py`は`AccessLogMiddleware`→`RequestSizeLimitMiddleware`の
    順で`add_middleware`していたが、Starletteの`add_middleware`は
    `insert(0, ...)`のため後から登録した方が外側になる。実際の呼び出し順は
    `RequestSizeLimitMiddleware → AccessLogMiddleware → router`で、413は
    外側から直接返るため`AccessLogMiddleware`に到達しない
  - A2: `MCPServer(...)`に`version=`を渡していないため、インストール済み
    SDKの既定値`""`が`serverInfo.version`に出る
- [x] 1. A3: `app/mcp_server.py`の`_McpCall.__exit__`で、常に出る
      `mcp_call`のerror extraに`error_code`（`GatewayError`ならその
      `code.value`、それ以外なら`ErrorCode.INTERNAL_ERROR.value`）を追加
- [x] 2. A4: `app/main.py`の`add_middleware`呼び出し順を入れ替え、
      `AccessLogMiddleware`を最外周にした
- [x] 3. A2: `app/config.py`に`PACKAGE_VERSION`（`importlib.metadata.version(
      "obsidian-vault-gateway")`、フォールバックなし）を追加し、
      `app/main.py`のFastAPI版と`app/mcp_server.py`のMCPServer版が
      同じ値を参照するようにした
- [x] 4. テスト追加（後述）

## 実装しないもの（対象外、別PRへ）

- RESTの`AccessLogMiddleware`自体に`GatewayError.code`を記録する変更
  （後述の「未解決事項」参照）
- A5（searchのskipped件数）、D1/D2（ドキュメントのPhaseステータス矛盾・
  `AUTH_ENABLED`のADR化）、B/C系（mypy・coverage・CI強化・検索の
  ページングコスト）

## Review

### 変更ファイル

- 変更: `app/mcp_server.py`（`error_code`の追加、`ErrorCode` importの追加、
  `PACKAGE_VERSION` importの追加、`MCPServer(...)`への`version=`追加）
- 変更: `app/main.py`（`add_middleware`の順序入れ替え、`PACKAGE_VERSION`
  importの追加、`FastAPI(...)`の`version=`を`PACKAGE_VERSION`参照に変更）
- 変更: `app/config.py`（`PACKAGE_VERSION`定数の新規追加）
- 変更: `tests/test_mcp_tools.py`（A3のLogRecordレベルテスト2件追加）
- 変更: `tests/test_log_format.py`（A3・A4の実レンダリングテスト2件追加）
- 変更: `tests/test_logging.py`（A4のLogRecordレベルテスト1件追加）
- 変更: `tests/test_mcp_protocol.py`（A2のテスト、`PACKAGE_VERSION`と
  `rest_app.version`の一致確認を追加）

### テスト結果

- `.venv/bin/ruff check .` → All checks passed
- `.venv/bin/pytest -q` → 502 passed（既存497 + 新規5）
- `.venv/bin/python scripts/export_openapi.py --check` → up to date
  （`PACKAGE_VERSION`は現状`"0.1.0"`に解決され、既存の`version="0.1.0"`
  ハードコードと同値のためdriftなし）
- 3項目それぞれについて、対応する1ファイルの該当箇所だけを一時的に
  修正前の状態に戻し（Edit差し戻し→pytest実行→Edit再適用）、新設テストが
  意図した理由で失敗することを確認した
  - A3: `NoteNotFoundError`テスト2件・renderedテスト1件が
    `code=NOTE_NOT_FOUND`欠落で失敗
  - A4: LogRecordテスト・renderedテストの両方が「該当ロガーの
    レコードが0件」で失敗（413自体は返るが、AccessLogに一切残らない）
  - A2: `serverInfo.version`が空文字列で失敗
  - すべて差し戻しを元に戻した後、全502件が成功することを再確認

### 実装中に発見し、計画から逸脱した点

1. **A3のレンダリングテストは`mcp.call_tool(...)`直接呼び出しではなく、
   `tests/test_log_format.py`の既存パターン（`mcp_client`
   フィクスチャ経由の実HTTP POST）に合わせた。** 計画段階では
   `test_mcp_tools.py`と同じ`mcp.call_tool(...)`直接呼び出しを想定していたが、
   `test_log_format.py`の「driven through the real application」節は
   既にmountされた`/mcp`エンドポイントへの生JSON-RPC POSTで統一されており
   （`test_mcp_call_renders_tool_status_duration_and_result_count`等）、
   そちらに合わせる方が一貫していた。結果として`pytestmark =
   pytest.mark.anyio`と`anyio_backend`フィクスチャをこのファイルに追加する
   計画も不要になった（`mcp_client`は同期`TestClient`のため）
2. **`error_code`をGatewayError判定の`if/else`に分けた。** 計画の
   三項演算子1行では`ruff`の行長制限（100文字）を超えた
   （104文字）ため、素直な`if/else`に分解した

### 未解決事項

- **RESTの`AccessLogMiddleware`にも同種の欠落がある。**
  `status_code`のみを記録し、`GatewayError.code`を記録できない。
  `log_detail=None`の4xxでは別の`gateway_error`ログも出ないため、
  HTTP statusだけでしか分類できない。MCP側のcode追加とは別PRで扱う
- A5〜、B/C系は引き続き未対応

# Phaseステータスの整合と`AUTH_ENABLED`のADR化（D1・D2）

コードは変更せず、ドキュメントのみを修正。README.mdが既に正確に記述している
内容（Phase 2のOMV/LiveSync/PC・iPhone実機検証は完了済み、コンテナメモリ
制限とログローテーションのみ後から追加されて未確認）を、古いまま矛盾していた
`docs/IMPLEMENTATION_PLAN.md`・`docs/PHASE2_PLAN.md`・
`docs/MCP_IMPLEMENTATION_PLAN.md`へ反映した（D1）。また、`AUTH_ENABLED`が
セキュリティ境界を変更する設定でありながらADR化されていなかったため、
ADR-0001〜0003と同じ形式でADR-0004を新規作成した（D2）。

- [x] 0. 現状確認
  - README.mdの記述とdocs/配下のステータス行・本文の矛盾箇所を特定
  - `docs/MCP_IMPLEMENTATION_PLAN.md`は先頭のステータス行だけでなく、
    §5・§26本文にも「未実施」という古い記述が別途残っていることを発見
  - `app/auth.py`のREST側と`app/mcp_auth.py`のMCP側で、`AUTH_ENABLED=false`
    時の実装が微妙に異なることを発見（後述）
- [x] 1. `docs/IMPLEMENTATION_PLAN.md`: Phase 2のステータスを`Completed`へ、
      §10へADR-0004への前方参照を追加
- [x] 2. `docs/PHASE2_PLAN.md`: ステータスを`Completed`へ、§12の完了条件
      （実機検証が通るまでCompletedにしない、という記述）はそのまま残し、
      その直後に「検証結果」節を追加して満たされたことを明記
- [x] 3. `docs/MCP_IMPLEMENTATION_PLAN.md`: ステータスを`Completed`へ。
      §5・§26本文の「Codexクライアント接続確認・IDE拡張接続」の記述を、
      README.md「Client checks」で実際に確認されている範囲
      （ChatGPTデスクトップ・Codex CLI）とされていない範囲（IDE拡張は
      単独では未確認）に分けて修正。§8へADR-0004への前方参照を追加
- [x] 4. `docs/adr/0004-allow-disabling-bearer-authentication.md`新規作成
- [x] 5. `README.md`: 導入部のADR一覧とSecurity invariantsの`AUTH_ENABLED`
      箇条書きの両方にADR-0004への参照を追加

## Review

### 変更ファイル

- 変更: `docs/IMPLEMENTATION_PLAN.md`（Phase 2ステータス、§10前方参照）
- 変更: `docs/PHASE2_PLAN.md`（ステータス、§12検証結果節）
- 変更: `docs/MCP_IMPLEMENTATION_PLAN.md`（ステータス、§5・§26本文、
  §8前方参照）
- 新規: `docs/adr/0004-allow-disabling-bearer-authentication.md`
- 変更: `README.md`（ADR一覧、Security invariantsの参照追加）

### テスト結果

- `git diff --check` → `docs/PHASE2_PLAN.md`と
  `docs/MCP_IMPLEMENTATION_PLAN.md`のステータス行で「trailing whitespace」
  警告が出るが、これは同じブロック内の`Prerequisite:`行など既存行にも
  使われているMarkdownのhard-break規約（行末の半角スペース2つ）であり、
  意図的なもの。修正不要
- `.venv/bin/ruff check .` → All checks passed（コード変更なし）
- `.venv/bin/pytest -q` → 502 passed（コード変更なしのため件数は前回と同じ）
- `.venv/bin/python scripts/export_openapi.py --check` → up to date
- ステータス行のgrep（`^> Status: (Proposed|Implemented — Deployment
  verification pending)`）→ 該当なしを確認
- `AUTH_ENABLED`・`ADR-0004`参照のgrep → 期待した箇所
  （§10・§8・ADR本文、README・両プラン内の前方参照）にすべて出現することを
  確認

### 実装中に発見し、計画から逸脱した点

1. **`docs/MCP_IMPLEMENTATION_PLAN.md`はステータス行だけでは不十分だった。**
   レビューで指摘された通り、先頭を`Completed`にするだけでは§5・§26本文の
   「未実施」という古い記述と矛盾したまま残る。README.md「Client checks」
   （ChatGPTデスクトップ・Codex CLIの接続確認のみを明記、IDE拡張は単独では
   触れていない）と照合し、確認済み・未確認を実際の記述範囲に合わせて
   分けて修正した
2. **ADR-0004でREST/MCPの挙動差を正確に書き分けた。** 当初案は
   「`AUTH_ENABLED=false`ではAuthorizationヘッダーを両transportとも一切
   検査しない」としていたが、`app/auth.py`の`require_token`は
   `credentials: CredentialsDep`をパラメータに取るため、FastAPIが
   `HTTPBearer`のsecurity dependencyを`require_token`本体より先に評価し、
   ヘッダー自体は解析される（比較・検証はしないだけ）。MCP側
   （`McpBearerAuthMiddleware`）は`settings.auth_enabled`を確認してから
   ヘッダーを読むため、本当に一切読まない。ADRの本文とConsequencesの両方に
   この違いを明記した
3. **外部境界の表現を限定した。** 「loopback bindingやTailscaleは境界の例」
   という書き方ではなく、「deploymentのthreat modelに適したexplicitな
   access-control boundary（loopback-only listener、firewall allowlist、
   restrictive reverse-proxy policy、またはTailscaleのACL/Grantsで
   接続元を限定している場合）」という限定表現にした。「同一LAN/tailnetに
   参加しているだけでは不十分」という一文もREADMEの既存表現と揃えた

### 未解決事項

- ~~`app/auth.py`の`require_token`docstringの不正確な記述~~ — 別PR
  （Tier 1+2監査修正PR）で修正済み。実際にはFastAPIの`HTTPBearer`security
  dependencyがヘッダーを解析してから`require_token`本体が呼ばれるため、
  「検査しない」のは比較・検証の部分のみである旨をdocstringに書き直し、
  `docs/adr/0004-*.md`のNegative欄も追随して更新した
- `docs/PHASE2_PLAN.md`§11の実機検証手順（15ステップ）自体はそのまま残した
  （将来の再検証時に参照する手順として有効なため）。手順内の個別ステップに
  チェックマーク等は付けていない
- A5、B/C系は引き続き未対応

### PR #11レビュー対応（追加コミット）

上記PRのレビューで4点の指摘を受け、マージ前に修正した。

1. **ADRのContextで「loopbackならBearer認証は何のセキュリティも追加しない」
   と断定していたのを限定した。** 同一ホスト上の別ユーザーや侵害済み
   プロセスからは依然到達可能であり、proxy・port forwarding・sidecar等で
   外部経路が生まれる可能性もある。「同一ホスト上の全principalを信頼し、
   そのような経路が存在しないthreat modelでは追加のremote-access control
   を提供しない場合がある」という限定表現へ修正した
2. **`MCP_IMPLEMENTATION_PLAN.md`のStatus`Completed`と、§26のIDE拡張
   「単独では未確認」が矛盾していた。** 確認済みにする・完了条件から除外
   する・waiveするの3択のうち、実機確認ができないためwaiveを選択。
   §26直下に「Phase 1.5 was accepted as Completed with the standalone
   IDE-extension re-verification explicitly waived as a completion gate」
   という注記を追加し、§5・§26のIDE拡張の記述もこれに揃えた
3. **ADRのPositive consequencesで「1つのtoggleなら将来driftできない」と
   断定していたのを修正した。** 1つの設定値が防ぐのは「運用者が
   REST/MCPを別々の状態に設定すること」のみであり、RESTとMCPが別コード
   パスである以上、将来の実装バグによる非対称化までは防げない。この区別を
   明記した
4. **`git diff --check`が新規追加した`Status:`行等でtrailing whitespace
   警告を出していたのを解消した。** 該当行のみ、既存の「行末に半角スペース
   2つ」規約から`<br>`タグへ変更（未変更の既存行はそのまま）。
   `git diff --check`が exit 0 になることを確認済み

レビューでは後続PRとして「`AUTH_ENABLED=false`時のREST/MCP parity test」の
追加も提案された。価値はあるが今回はdocs-onlyの範囲を超えるため実装せず、
上記の`app/auth.py`docstring修正と合わせて次のコード変更PRの候補として残す。

再検証: `git diff --check` exit 0、`.venv/bin/ruff check .` All checks
passed、`.venv/bin/pytest -q` 502 passed、
`.venv/bin/python scripts/export_openapi.py --check` up to date。

# ChatGPTエクスポートの構造化入力対応（issue #12）

`create_inbox_note`を「タイトル + 自由記述Markdown + 任意frontmatter」から
「タイトル + 構造化サマリー」へ拡張し、Gateway側の決定的フォーマッタが見出し順・
空セクションの表現・frontmatterキー順を固定する。専用の`export_chat_note`ツールは
追加しない（書き込みツールが2つになるとツール選択が曖昧になるため）。設計判断は
`docs/adr/0005-single-structured-entry-point-for-chat-exports.md`に記録した。

- [x] 0. 現状確認（既存の`_render_note`が唯一の整形箇所であること、Markdownレンダラが
      リポジトリに無いこと、MCPの引数スキーマ検証がツール本体より先に走ることを確認）
- [x] 1. S1: `app/models.py`に`ChatExport`家族（`ExportMode`/`TimelineEntry`/
      `TopicSection`/`TermDefinition`）を追加。`app/services/chat_export.py`を新規
      作成（純粋フォーマッタ）。`tests/test_chat_export.py`を新規作成
- [x] 2. S2: `app/application.py`に`create_chat_export_note`を追加（`app/`内で
      唯一`datetime.now()`を呼ぶ箇所）。`CreatedNoteResponse.title`のdescriptionを
      「ファイル名ステム」へ修正。`tests/test_application.py`に追加
- [x] 3. S3: MCPツール`create_inbox_note`を`title` + `export`のみに変更し、
      `content`/`frontmatter`パラメータを削除。`tests/test_mcp_tools.py`・
      `tests/test_mcp_protocol.py`を更新
- [x] 4. S4: REST `InboxNoteCreateRequest`に`export`フィールドと排他制約
      （`content`/`export`のどちらか一方必須、`export`+`frontmatter`拒否）を追加。
      `app/routers/inbox.py`で分岐。`tests/test_inbox.py`・`tests/test_openapi.py`に
      追加。`openapi.json`再生成
- [x] 5. S5: ADR-0005新規作成。`README.md`・`docs/IMPLEMENTATION_PLAN.md`§9・§12・§17
      を更新。本セクション追加

各スライス完了前に`.venv/bin/pytest -q`・`.venv/bin/ruff check .`・
`.venv/bin/python scripts/export_openapi.py --check`を実行した（全件通過）。

## 実装しないもの（対象外、別PRへ）

- **`related_notes`入力とwikilink生成 → issue #13**（`Depends on: #12`）。今回は
  `## 関連ノート`の見出し・位置・空状態（常に`なし`）だけを確定させた
- **エクスポート前の重複ノート検出 → issue #14**
- **会話→知識への昇格ワークフロー → issue #15**
- 作成時のバイト上限（`MAX_REQUEST_BYTES`のみが境界のまま）
- `append_inbox_note`での`updated`更新（append自体がframtmatterを解析・再直列化しない）
- クライアント`frontmatter`とフォーマッタ所有キーのマージ（`export`+`frontmatter`は
  無条件拒否）
- `FrontmatterValue`の拡張（スカラーとスカラーの平坦リストのまま）
- 英語見出しモード、8番目のモード、モード別既定タグ・タグ語彙
- MCP `clientInfo`を`source`へ通すこと（transport非依存の不変条件を壊すため）

## 未解決 / 実機でのみ確認できること

- ChatGPTデスクトップ・Codex CLIが素の「保存して」で実際に`export.mode: summary`を
  選ぶかは実クライアントでのみ確認できる
- `ChatExport`が約30フィールド+3 `$defs`に膨らんだことで、モデルがモード外フィールドを
  誤って埋める頻度が増えないかは実クライアントセッションでの観察が必要
- Obsidianの Properties UI 上で`project`/`conversation_type`省略時の表示（キー自体が
  出ないこと）は実機Obsidianでの目視確認が必要
- Docker・LiveSync・PC/iPhone Obsidianでの実機検証は本PRでは未実施
  （開発機にdockerが無いため）→ README「OMV verification checklist」の新設curl
  （MCP `create_inbox_note`の構造化summaryエクスポート呼び出し）に従い実機で確認する

## Review

### 変更ファイル

- 新規: `app/services/chat_export.py`, `tests/test_chat_export.py`,
  `docs/adr/0005-single-structured-entry-point-for-chat-exports.md`
- 変更: `app/models.py`（`ChatExport`家族の追加、`InboxNoteCreateRequest`の
  `export`フィールドと排他制約`model_validator`、`CreatedNoteResponse.title`の
  description修正。`ChatExport`をファイル先頭寄りへ移動して前方参照エラーを回避）
- 変更: `app/application.py`（`create_chat_export_note`追加）
- 変更: `app/mcp_server.py`（`create_inbox_note`のシグネチャと`description=`を
  構造化専用に変更。`FrontmatterValue` importを削除、`ChatExport` importを追加）
- 変更: `app/routers/inbox.py`（`body.export`の有無で`create_chat_export_note`/
  `create_inbox_note`を分岐）
- 変更: `openapi.json`（再生成。`CreatedNoteResponse.title`のdescription変更と
  `InboxNoteCreateRequest`のフィールド追加を反映）
- 変更: `tests/test_application.py`, `tests/test_inbox.py`,
  `tests/test_mcp_protocol.py`, `tests/test_mcp_tools.py`, `tests/test_openapi.py`
- 変更: `README.md`（イントロのADR一覧、Tools節の見出しマッピング表、REST節の
  raw/structured両リクエスト例、Testing節、OMV checklistへのMCP構造化呼び出し追加）
- 変更: `docs/IMPLEMENTATION_PLAN.md`（§9ツール分類表の下に注記、§12へ
  「構造化エクスポート」小節を追加、§17テスト構成へ`test_chat_export.py`を追加）

### テスト結果

- `.venv/bin/ruff check .` → All checks passed
- `.venv/bin/pytest -q` → 628 passed（直前のPR時点の502件 + 本PRで126件追加）
- `.venv/bin/python scripts/export_openapi.py --check` → up to date

### 実装中に発見し、計画から逸脱した点

1. **MCP SDKの動的引数モデル（`ArgModelBase`）は`extra="forbid"`を設定していない。**
   計画では「トップレベルに`content`/`frontmatter`を送るとToolErrorになる」と想定
   していたが、実際は pydanticの既定`extra="ignore"`により静かに無視されるだけで
   あることをインストール済みSDK（`mcp/server/mcpserver/utilities/func_metadata.py`）
   のソースと実行確認で発見した。`extra="forbid"`が効くのは`ChatExport`自身のような
   個別パラメータ型（明示的に`ConfigDict(extra="forbid")`を持つ）に限られる。
   `tests/test_mcp_tools.py`のテストをこの実挙動（無視されるが、その内容は書き込みへ
   一切反映されない）を検証する形に修正した
2. **タイトル301文字以上の挙動が変わった。** 旧シグネチャでは`title: str`が
   bareパラメータのため上限が無く、`sanitise_title`が100文字へ切り詰めていた。
   REST parityのため`title`に`Annotated[str, Field(min_length=1, max_length=300)]`
   を追加した結果、301文字以上は`ToolError`（スキーマ拒否）になる。RESTは元から
   この挙動なので新たな非対称性ではない
3. **`app/models.py`内で`ChatExport`を`InboxNoteCreateRequest`より前方へ移動する
   必要があった。** `from __future__ import annotations`下でも、pydanticはクラス
   定義時にモジュール名前空間で型を解決するため、`InboxNoteCreateRequest.export:
   ChatExport | None`が`ChatExport`定義より前にあるとエラーになる
4. **`NoteResponse.content`は`_render_note`の既存挙動により先頭に空行を1つ持つ。**
   `_render_note`がfrontmatterとbodyの間に空行を1つ挿入し、`markdown_parser`の
   `_split_frontmatter`は閉じdelimiter直後から`body`を切り出すため、その空行は
   bodyの一部として残る。chat_export由来ではなく既存の挙動であり、
   `tests/test_application.py`のタイトル注入テストのアサーションをこれに合わせて
   修正した
5. **`CreatedNoteResponse.title`のdescription修正を、計画のS2ではなくS1の
   モデル追加と同時に`app/models.py`へ適用した。** 同一ファイルの変更なので
   まとめる方が自然だったため。`openapi.json`もその時点で1回再生成し、S4で
   再度再生成した

### 未解決事項

- 上記「未解決 / 実機でのみ確認できること」を参照
- レビューで指摘された「フィールド一覧の列挙順を`frozenset`ではなく`tuple`にする」
  「`verification`のようなモード共有フィールドのdescriptionが全所有モードを含む
  ことをテストする」等の計画修正はすべて実装へ反映済み（`_ALL_MODE_FIELDS_IN_ORDER`、
  `_FIELD_OWNER_MODES`の導出、対応するテスト）

### PR #16レビュー対応（追加コミット）

上記PRへのコードレビューで**Request changes**（マージブロッカー2件・要修正1件）を
受け、マージ前に修正した。3件とも実機検証（`markdown-it-py`によるレンダリング確認、
インストール済みSDKのソース読解、実際のPython実行での再現）で技術的に正しいと確認
した上で対応した。

1. **箇条書き・番号付き・timeline/definitions結合文字列でMarkdown構造注入を防げて
   いなかった。** `_escape_paragraph`（`tldr`の段落レンダリングのみに適用）を
   `_escape_block_start`へ改名し、`app/services/chat_export.py`の全レンダリング
   経路（`decisions`等の箇条書き、`steps`の番号付き、`timeline`/`definitions`の
   結合後文字列、`topics.points`）へ適用した。`decisions=["# 偽の見出し"]`は
   `markdown-it-py`で実際に`<li><h1>偽の見出し</h1></li>`（リスト内に本物のH1）
   を生成することを確認して発見した。ハザード集合に先頭`<`（HTMLブロック全7種が
   必ず`<`で始まる）と先頭`[`（リンク参照定義がリスト項目の表示テキストを消す・
   タスクリストチェックボックスになる）を追加した。数字+区切り文字
   （`"1. nested"`）は句読点側へ`\`を挿入する専用処理（`_ORDERED_MARKER_RE`）を
   追加した——数字の前へ`\`を置くとCommonMarkのエスケープ対象外（数字はASCII
   句読点でない）のため`\`がそのまま表示に残ってしまうことを実機確認した
2. **MCPトップレベルの未知引数（`content`/`frontmatter`）が静かに破棄されていた。**
   `mcp==2.0.0`の動的引数モデル（`ArgModelBase`）に`extra="forbid"`を設定する
   公開手段が無いことを確認した上（`**kwargs`追加・`model_config`後書き換え・
   ラッパーモデル化の3案を検討し、いずれも不採用と判断）、SDKが提供する
   `mcp.server.context.ServerMiddleware`拡張点を使い、`_StrictCreateInboxNoteArgumentsMiddleware`
   を`app/mcp_server.py`に追加してフェイルクローズ化した。この保護は実際の
   JSON-RPCディスパッチ（マウント済み`/mcp`）を通る経路にのみ働き、
   `tests/test_mcp_tools.py`の直接呼び出し便利メソッド（`mcp.call_tool(...)`）は
   `ServerRunner`のミドルウェア連鎖を経由しないため対象外——同モジュールの
   docstringをこの非対称性を明記する形に修正した。ミドルウェアで拒否すると
   `_McpCall`が実行されず監査ログから書き込み試行が欠落する問題も指摘され、
   `_log_mcp_call`共通ヘルパーを抽出して`_McpCall.__exit__`とミドルウェアの
   両方から使う形にした
3. **タグ正規化テストが公開契約（`ChatExport`→pydantic検証）を経由していなかった。**
   `tags: list[Label]`（`Label`は`min_length=1`）のため`ChatExport(tags=[""])`は
   pydanticレベルで拒否され、空要素を落とす`_normalise_tags`の実装へ到達しない
   矛盾があった。`Label`から`min_length`を除いた`Tag`型を新設し`tags`をこれへ
   変更。`_normalise_tags`の直接テストを`ChatExport(...)`→`render_chat_export(...)`
   経由の公開契約テストへ書き換えた。`ChatExport`はREST
   `InboxNoteCreateRequest.export`から参照されるため、この型変更は
   `openapi.json`（`ChatExport.tags.items.minLength`が消える）にも反映され、
   再生成した

いずれの修正でも、`docs/adr/0005-*.md`の該当記述（`_escape_paragraph`のみに
触れていたリスク節、「silently ignored」としていたNegative節）と
`tests/test_mcp_protocol.py`の既存コメント（`test_extra_unexpected_argument_is_ignored_not_rejected`）
を実装後の挙動に合わせて更新した。

実装中に副次的に発見した点: モダンプロトコル（2026-07-28）とレガシープロトコル
（2025-06-18）では、JSON-RPCレベルのエラーがHTTPステータスへマッピングされる際の
挙動が異なる（レガシーは常にHTTP 200 + bodyの`error`フィールド、モダンはHTTP 400）
ことを新規テスト作成時に実機確認した。計画時点では未把握だった既存SDK挙動であり、
今回追加した`_StrictCreateInboxNoteArgumentsMiddleware`固有の挙動ではない。

再検証: `.venv/bin/ruff check .` → All checks passed、`.venv/bin/pytest -q` →
642 passed（Fix適用前628件+新規14件）、
`.venv/bin/python scripts/export_openapi.py --check` → up to date。

# 検証済み関連ノートwikilink（issue #13）

`ChatExport`に`related_notes`入力を追加し、Gateway側で全件再検証してから
`## 関連ノート`をwikilinkとして決定的にレンダリングする。検証（Vaultアクセス）と
整形（純粋関数）を分離し、`app/services/chat_export.py`のfilesystem非依存という
ADR-0005決定8の制約を保ったまま実装する。設計判断は
`docs/adr/0006-verified-related-note-wikilinks.md`に記録した。

- [x] 1. S1: `app/models.py`に`MAX_RELATED_NOTES`（公開定数、10）・`NotePath`
      （`min_length`なし）を追加。`ChatExport.related_notes`を`next_actions`と
      `sources`の間に宣言。`ChatExport`docstringを差し替え。`CreatedNoteResponse`に
      `related_notes_linked`/`related_notes_skipped`（defaultなし・required）を追加
- [x] 2. S2: `app/services/chat_export.py`に`is_renderable_wikilink_target`・
      `format_wikilink`を追加（危険文字`[` `]` `|` `#` `^`・改行・制御文字・
      `.md.md`を拒否）。`render_chat_export`に`verified_related_notes`引数を追加
      （既定値`()`）。`_render_related_notes_section`が検証済みリストのみを
      レンダリングし、`export.related_notes`は直接読まない
- [x] 3. S3: 新規`app/services/related_notes.py`。`resolve_related_notes`が
      `path_security.resolve_read_path`で候補を再検証。上限判定はループ先頭で
      `>=`（`max_links=0`が機能するように）。重複排除はvault相対パス文字列のみ
      （inodeでは畳まない）。`FileNotFoundError`は不存在として除外、その他の
      `OSError`は伝播させる
- [x] 4. S4: `app/application.py`の`create_chat_export_note`が
      `resolve_related_notes`→`render_chat_export(verified_related_notes=...)`→
      `create_inbox_note`→`model_copy`でカウントを付与する順に配線。
      `create_inbox_note`のレスポンス構築に明示的な`0`/`0`を追加。limiterや
      `to_thread`は使わない（SDKの`func_metadata.py`実測により、対象ツールは
      既にイベントループ外で実行されることを確認済み）
- [x] 5. S5: `app/mcp_server.py`の`create_inbox_note`の`description=`に
      関連ノートの段落を追加。`SERVER_INSTRUCTIONS`・ミドルウェアは変更不要
- [x] 6. S6: 新規`tests/test_related_notes.py`（サービス単体、共有fixture
      vaultへファイルを追加しない）。`tests/test_chat_export.py`・
      `test_mcp_tools.py`・`test_mcp_protocol.py`・`test_rest_regression.py`・
      `test_application.py`・`test_inbox.py`を更新
- [x] 7. S7: `openapi.json`再生成
- [x] 8. S8: ADR-0006新規作成。ADR-0005へ前方参照を追加。`README.md`・
      `docs/IMPLEMENTATION_PLAN.md`§12・§17・本セクションを更新

各スライス完了後に`.venv/bin/pytest -q`・`.venv/bin/ruff check .`・
`.venv/bin/python scripts/export_openapi.py --check`を実行した（全件通過）。

## 実装しないもの（対象外、別issueへ）

- 短縮形`[[Note]]`（basename一意時）— 書き込み経路での全Vault走査が必要、かつ
  時間をまたいだ決定性が壊れる（ADR-0006 Alternative 3）
- alias（`|Title`）— リンク識別子は検証済みvault相対パスだけで完結させる
  （ADR-0006 決定1・Alternative 1・2）
- 落としたパスそのものをレスポンスに載せること（今回は件数のみ、ADR-0006
  Alternative 6）
- 重複ノート検出 → issue #14 / 昇格ワークフロー → issue #15
- `append_inbox_note`での関連ノート追記
- 非Markdownファイルへのリンク（issueのopen questionだが将来検討）

## 未解決 / 実機でのみ確認できること

- OMV / LiveSync / PC Obsidianでの実機検証は本PRでは未実施（開発機にdockerが
  無いため）→ README「OMV verification checklist」の新設curl（`related_notes`
  付きの`create_inbox_note`呼び出し）に従い、実在ノートを指すリンクが
  Obsidian上で解決すること・バックリンクに現れることを実機で確認する
- LiveSyncが実際にVaultへ書き込んでいる最中の`resolve_read_path`の
  TOCTOU（`resolve()`成功後の`stat()`失敗）は、テストではmockで再現したのみで
  実際のLiveSync書き込みタイミングとの競合は未確認

## Review

### 変更ファイル

- 新規: `app/services/related_notes.py`, `tests/test_related_notes.py`,
  `docs/adr/0006-verified-related-note-wikilinks.md`
- 変更: `app/models.py`（`MAX_RELATED_NOTES`・`NotePath`・
  `ChatExport.related_notes`・`ChatExport`docstring・`CreatedNoteResponse`の
  2フィールド追加）
- 変更: `app/services/chat_export.py`（`_WIKILINK_HAZARD_RE`・
  `is_renderable_wikilink_target`・`format_wikilink`を追加、
  `_render_related_notes_section`/`_build_content`/`render_chat_export`の
  シグネチャ変更、モジュールdocstring更新）
- 変更: `app/application.py`（`create_chat_export_note`の配線、
  `create_inbox_note`のレスポンス構築）
- 変更: `app/mcp_server.py`（`create_inbox_note`の`description=`）
- 変更: `openapi.json`（再生成。`ChatExport.related_notes`・
  `CreatedNoteResponse`の2フィールド追加を反映）
- 変更: `docs/adr/0005-*.md`（ADR-0006への前方参照を1行追加）
- 変更: `tests/test_chat_export.py`, `tests/test_mcp_tools.py`,
  `tests/test_mcp_protocol.py`, `tests/test_rest_regression.py`,
  `tests/test_application.py`, `tests/test_inbox.py`
- 変更: `README.md`（Tools節の表・責務分担段落、Testing節、OMV checklist）
- 変更: `docs/IMPLEMENTATION_PLAN.md`（§12「検証済み関連ノートwikilink」小節、
  §17テスト構成）

### テスト結果

- `.venv/bin/ruff check .` → All checks passed
- `.venv/bin/pytest -q` → 708 passed（前PR時点642件 + 本PRで66件追加）
- `.venv/bin/python scripts/export_openapi.py --check` → up to date

### 計画段階のレビューで修正した点（コードを書く前に発見）

計画レビュー（3ラウンド）で発見・修正した設計上の誤りをここに記録する。実装は
最終版の計画に沿って行ったため、実装中の逸脱ではない。

1. **`related_notes`を必須入力にしていた。** 当初案`list[NotePath]`のまま
   `default_factory=list`を付けないと、検索0件・関連ノートなしのexportが
   常に失敗する。`default_factory=list`を追加した
2. **`CreatedNoteResponse`のカウンタに`= 0`を付けると「defaultありのrequired
   field」という用語矛盾になる。** JSON Schema上、default付きフィールドは
   requiredにならない。`Field(ge=0)`（defaultなし）に変更し、raw content経路で
   明示的に`0`/`0`を渡す形にした
3. **`except (GatewayError, OSError)`は広すぎた。** `resolve_read_path`の
   末尾`stat()`はtry/except外にあり、LiveSyncとのTOCTOUで`FileNotFoundError`が
   伝播し得ることは正しい着眼点だったが、`OSError`を丸ごと捕まえると
   `PermissionError`等のI/O障害まで「候補が無効だった」扱いで握り込んでしまう。
   `except (GatewayError, FileNotFoundError)`に絞った
4. **重複排除に`(st_dev, st_ino)`を使う案を一度採用しかけて撤回した。**
   このGatewayではノートの同一性が一貫してvault相対パスであり
   （`SearchResultItem.id`/`path`、`ResolvedNote.relative`）、
   `resolve_read_path`はhardlinkを禁止していない。inodeで畳むと、クライアントが
   `search_notes`の結果から正当に選んだリンクの片方を勝手に消すことになる
5. **上限判定をループ末尾（`links.append`の後に`len(links) == max_links`）に
   置く案は`max_links=0`で機能しない。** 1件目追加後に`1 == 0`が偽になり、
   上限0が効かず全候補が入ってしまう矛盾に気づき、判定をループ先頭・`>=`へ
   修正した
6. **alias却下の理由づけを一度誤った。** 「aliasにはfrontmatter読み取りが
   必須」と書いたが、basenameからもalias文字列は作れるため事実として誤り。
   「リンク識別子は検証済みvault相対パスだけで完結させる。aliasは解決に不要な
   表示情報で、frontmatter由来なら内容読み取りが増え、basename由来なら別の
   表示名生成規則が増える」という正確な言い方へ修正した（ADR-0006決定1・
   Alternative 1・2）

### 未解決事項

- 上記「未解決 / 実機でのみ確認できること」を参照

### PR #17レビュー対応（追加コミット）

PR #17へのコードレビューで指摘（マージブロッカー1件・ドキュメント矛盾1件）を
受け、マージ前に修正した。P1は実際に`ChatExport(...)`を実行して再現し、
技術的に正しいと確認した上で対応した。

1. **[P1] `NotePath`の`max_length=1024`が、1件の長すぎる候補でexport全体を
   拒否していた。** `resolve_related_notes`（S3）は1100文字の候補をサービス
   単体では正しく除外できていたが、その手前の`ChatExport`モデル自体が
   pydanticスキーマで1024文字超を拒否するため、実際のMCP/REST経路では
   `resolve_related_notes`まで到達する前にexport全体が失敗していた
   （`test_related_notes.py`のサービス単体テストは`resolve_related_notes`を
   直接呼ぶため、この矛盾を検出できていなかった）。issue #13の「個別の無効
   候補はexportを阻害しない」契約に反する。`NotePath`を`str`（長さ制約なし）
   へ変更し、長さの妥当性判定は既存の`path_security`の`MAX_PATH_LENGTH`
   チェック（`resolve_read_path`経由、`resolve_related_notes`が
   `GatewayError`として捕捉）に一元化した。リスト全体の件数上限
   （`max_length=MAX_RELATED_NOTES`）だけがスキーマ強制のままであることは
   変えていない。`app/application.py`・`app/mcp_server.py`（MCP経由）・
   REST経由の3層それぞれで「1025文字の候補+有効な候補」を渡し、有効な方だけ
   リンクされexportが成功することを確認するテストを追加した
   （`tests/test_application.py`・`tests/test_mcp_tools.py`・
   `tests/test_inbox.py`）
2. **[P2] `related_notes_skipped`のdescriptionとREADMEが「上限超過は
   silently omitted」と実装と矛盾する説明をしていた。** 実際は
   `ChatExport.related_notes`自体のリスト件数上限（10件）はpydanticスキーマ
   レベルでexport全体を拒否する（P1と同じ理由で「個別候補の除外」とは別の
   失敗モード）。`CreatedNoteResponse.related_notes_skipped`のdescriptionから
   「or over the maximum link count」を削除し、上限超過は別の失敗モードで
   ある旨を明記。`README.md`の関連ノート段落も同様に「個別候補の除外」と
   「上限超過の拒否」を明確に分けて説明する形へ修正した

`openapi.json`を再生成した（`ChatExport.related_notes.items`から
`maxLength`が消える）。`tests/test_mcp_tools.py`の
`test_create_inbox_note_export_schema_related_notes_is_bounded`をこの
スキーマ変更に合わせて修正した。

再検証: `.venv/bin/ruff check .` → All checks passed、`.venv/bin/pytest -q` →
711 passed（PR #17時点708件 + 本修正で3件追加）、
`.venv/bin/python scripts/export_openapi.py --check` → up to date。

# 重複ノート検出（issue #14、PR #18）

このセクションは、他の全ての監査項目を修正する本PRの一環として遡及的に追加した
記録である。issue #14 / PR #18（commit `447be25`）はこのセッションの開始前に
既にマージ済みで、実装過程そのものはこのセッションでは観測していない。他の
PRセクションのような「計画レビューで発見した誤り」の記録は残っていないため、
ここには最終的な変更内容と検証結果のみを記録する。

## 変更内容

`find_duplicate_candidates`という新規read-only MCPツール（およびREST版
`GET /api/v1/inbox/duplicate-candidates`）を追加。`00_Inbox/ChatGPT`の直下
だけをスキャンし、frontmatterのみを読み、exact/normalized title・project・
keywordの各信号で候補をスコアリングする。Gatewayは`create_inbox_note`/
`append_inbox_note`をこの結果でゲートしない — 類似度はadvisoryであり、
new/append/cancelの判断フローはクライアント側のワークフロー契約として
文書化されている。詳細な設計判断は`docs/adr/0007-*.md`を参照。

主な変更ファイル: `app/services/duplicate_notes.py`（新規）、
`app/application.py`・`app/mcp_server.py`・`app/routers/inbox.py`・
`app/models.py`（配線）、`app/services/{chat_export,markdown_parser,
path_security}.py`（補助関数の追加・共有）。テスト:
`tests/test_duplicate_notes.py`（新規）ほか`test_application.py`・
`test_inbox.py`・`test_mcp_protocol.py`・`test_mcp_tools.py`・
`test_path_security.py`・`test_openapi.py`に追加。

マージ後のレビューで1点修正: `create_inbox_note`自身のツール説明文に
「`find_duplicate_candidates`が失敗した場合でも進めてよい」という既存の
`SERVER_INSTRUCTIONS`/ADR-0007の記載を反映していなかったため、
説明文自体にも明記した。

## 実装しないもの（対象外）

- 完全一致コンテンツのフィンガープリンティング — ADR-0007決定15。issue #14の
  初期スコープでもoptionalとして明記されており、次イテレーションへ延期
- `score`のレスポンス露出 — ADR-0007決定10。内部の重み付け調整をAPI契約化
  してしまうため

## 検証結果（このPRで再確認）

`.venv/bin/pytest -q` → 771 passed（マージ済みコミット時点の値）。
`.venv/bin/ruff check .` → All checks passed。
`.venv/bin/python scripts/export_openapi.py --check` → up to date。

なお、このセッション自身の監査で、`find_duplicate_candidates`の候補並び順が
`score`単独に依存し「most confident first」という自身の契約に反する場合が
あることが判明した（`limit`が小さいとconfidence `medium`の候補が
confidence `low`の候補より先に切り捨てられ得る）。この修正は本PR自身の
別セクションで扱う（confidenceを第一ソートキーへ変更、テスト追加）。

# procedure.stepsのverbatim/structure-preserving対応（issue #12 follow-up、ADR-0009）

## 背景

`create_inbox_note`の構造化export（issue #12、ADR-0005）は「1フィールド=
1 Markdown行」を前提としており、`one_line`の改行空白化・`_escape_block_start`
のfenceエスケープにより、`procedure.steps`にコマンドや設定ファイルを
そのまま保存できなかった。コードを独立した`## コード`セクションへ集約する
案は、手順書の「説明→コード→説明→コード」という順序そのものを失うため
不採用とした。

## 変更内容

`app.models.ProcedureStep`（`blocks: list[TextBlock | CodeBlock]`、
discriminated union）を新設し、`ChatExport.steps`を`list[StepInput]`に変更。
`_coerce_step`（`BeforeValidator`）により既存の`steps: ["文字列", ...]`は
1つのtext blockを持つstepとして後方互換に解釈される。全モード共通の任意
フィールド`ChatExport.code_blocks`（手順に属さない完成版コード用）も追加。

`app/services/chat_export.py`に`_canonicalise_code`（CRLF/CR統一・制御文字
除去・末尾LF最大1個吸収のみ、それ以外は変更しない verbatim/structure-
preserving契約）、`_fence_for`（動的fence長）、`_escape_inline`
（caption専用、CommonMark/GFM + Obsidian固有構文`#`/`^`/`==`/`$`/`%%`の
escape）、`_normalise_steps`（先頭text block必須の検証を含む）、
`_render_step`/`_render_fenced_code`（step番号由来のcontinuation indent）、
`_render_supplementary_sections`（`## コード`、空なら省略）を追加。
`_MODE_SECTIONS`/`_HEADINGS`（modeごとの固定見出し集合を扱う既存コード）は
一切変更していない — `## コード`はADR-0005決定4の例外ではなく、新しい
optional supplementary sectionという別カテゴリとして実装した。

コード全体の合計文字数上限（`_MAX_TOTAL_CODE_CHARS = 100_000`、正規化後
データに対して検証）を追加。単一fieldの`Field(max_length=...)`では複数
フィールドをまたぐ合計を検証できないため。

主な変更ファイル: `app/models.py`（新モデル3つ、`_MAX_CODE_CHARS`等の定数、
`ChatExport.steps`/`.code_blocks`）、`app/services/chat_export.py`
（canonicalization・fence・caption escape・rich step/code renderer）、
`pyproject.toml`（dev extrasに`markdown-it-py==3.0.0`を追加）。
テスト: `tests/test_chat_export.py`（schema・preservation・fence・label・
markdown-it-pyによるレンダラ構造検証・regression）、`tests/test_mcp_tools.py`・
`tests/test_inbox.py`（MCP/REST経路、legacy互換、code-first step拒否）。
`app/application.py`・`app/mcp_server.py`・`app/services/inbox_service.py`・
`app/routers/inbox.py`は変更不要（既存の`_CREATE_INBOX_NOTE_ALLOWED_ARGUMENTS`
が`{"title", "export"}`のままで足りる）。

詳細な設計判断（canonicalization境界、動的fence、Obsidian固有inline
semanticsのescape、supplementary sectionという新カテゴリの位置づけ、
サイズ上限の根拠、後方互換ポリシー）は`docs/adr/0009-*.md`を参照。

## 実装しないもの（対象外、別issueへ）

- `technical`/`issue`/`reference`等、他モードの本文フィールドへの
  rich block化の拡張 — 今回は`procedure.steps`のみ。既存schemaとの
  整合調査が別途必要
- `code_blocks`とstepに属するコードの取り違えを防ぐschemaレベルの
  ガード — 役割分担は現時点ではfield descriptionによる規約のみ
  （ADR-0009決定10）

## 検証結果

`.venv/bin/pytest -q` → 928 passed。
`.venv/bin/ruff check .` → All checks passed。
`.venv/bin/python scripts/export_openapi.py --check` → up to date
（`openapi.json`を再生成済み）。
`docker compose config` → この開発環境にdockerが無いため未実行
（`compose.yaml`自体は本変更で触っていない）。

手動確認: 同一`now`での2回レンダリングがbyte一致すること、
`markdown-it-py`によるtoken解析でstep 10以降も`ordered_list_open`が
1個のまま崩れないこと、`tempfile.TemporaryDirectory`上のテスト用Inbox
（実Vault・本番`obsidian-api.tokonemore.com`は使用せず）に書き込んだ
ノートを`read_note`で読み戻し、`markdown-it-py`のfence token contentが
`canonicalise_code(入力) + "\n"`と一致することを確認した。

## REST API を `/api/v1/health` のみに縮小（docs/adr/0010-*.md）

REST は 8 エンドポイントから `GET /api/v1/health`（認証なし）1 本のみに
縮小した。MCP が機能面の唯一のインターフェースになる（ADR-0001 の延長）。

### 実装

4 段階のコミットに分けた（`git log --oneline` で確認可能）。

1. **テスト移行**（production code 無変更）: `tests/test_{search,vault,
   notes,inbox}.py` を `GatewayApplication` 直呼びへ書き換え、
   `tests/conftest.py` に共有 `application` fixture を追加（既存 2 箇所の
   重複定義を集約）。この時点で `pytest -q` が無変更のまま緑であることを
   確認し、移行が同じ application/service 層の振る舞いを検証していることの
   根拠とした。
2. **REST 実装の削除**: `app/routers/{search,notes,vault,inbox}.py` を削除。
   `app/main.py`（router 登録・`RequestSizeLimitMiddleware` 登録・
   description・auth-disabled warning）、`app/auth.py`（`require_token`/
   `bearer_scheme`/`CredentialsDep` 削除、`verify_bearer_token` は
   `app/mcp_auth.py` 用に残す）、`app/models.py`（`InboxNoteCreateRequest`/
   `InboxNoteAppendRequest`/`_MAX_CONTENT_CHARS` 削除）、`app/middleware.py`
   （`RequestSizeLimitMiddleware` 削除、`AccessLogMiddleware` から
   `note_path`/`result_count`/`query_length` の scope-state 受け渡しを削除）
   を変更。`GatewayApplication`（`app/application.py`）はロジック無変更
   （docstring のみ）。この段階で依存していた `tests/test_{auth,
   error_envelope,middleware,logging,log_format,vault_scan_concurrency,
   rest_regression}.py` も同時に削除・retarget し、`pytest -q` を緑に保った
   （2 点、実行して初めて判明した問題を修正: `/api/v1/health` は DEBUG
   ログなので INFO capture のテストでは別ルートか `caplog.set_level(DEBUG)`
   が必要だったこと、`mcp.call_tool()` はエラー時に `is_error=True` を返す
   のではなく `MCPError` を raise すること）。
3. **REST 表面テストの書き換えと `openapi.json` 再生成**: `test_openapi.py`
   の `EXPECTED_OPERATIONS` を health 1 件に、`test_every_reachable_
   error_code_appears_on_some_operation` を `test_rest_surface_is_exactly_
   health`（`set(schema["paths"]) == {"/api/v1/health"}` かつ
   `securitySchemes` 不在）に置き換え。`components.schemas` の完全一致は
   assert しない（pydantic の生成形式に依存するため）。
   `scripts/export_openapi.py` で `openapi.json` を再生成
   （73KB → 4 schema）。
4. **stale docstring 一掃 + docs/ADR-0010 + 最終検証**: `app/mcp_server.py`
   `app/runtime.py` `app/mcp_auth.py` `app/services/note_service.py`
   `app/logging_config.py` `app/config.py` `app/models.py` `app/__init__.py`
   `.env.example` の「REST or MCP」「both transports」等の古い前提記述を
   実態（MCP 単独、または REST は health のみ）に合わせて修正（ロジック
   変更なし）。`tests/test_mcp_auth.py`/`test_mcp_tools.py` の 2 箇所も
   削除済みシンボル（`require_token`）や成立しなくなった比較
   （「REST parity」「unlike REST's access log」）を修正。新規
   `docs/adr/0010-reduce-rest-surface-to-health-only.md`（11 decision
   items）。`README.md`（intro・Security invariants・MCP 節・
   REST 節の全面縮小・Logging サンプル・OMV verification checklist の
   curl 群を MCP 経由に置換）、`Usage.md`（REST API の位置づけ・ADR 一覧）、
   `docs/caddy/obsidian-api.Caddyfile`（`/api/v1/*` → `/api/v1/health`）を
   更新。`docs/IMPLEMENTATION_PLAN.md` は歴史的記録として本文は保持し、
   古いエンドポイント一覧の節の先頭に ADR-0010 へのポインタを追加のみ。

### 意図的に変更しなかったもの

- `GatewayApplication`（`app/application.py`）のロジック。MCP 8 ツールの
  振る舞いは一切変わらない。
- `app/exceptions.py`（`ErrorCode` 体系）。未マッチ REST パスの 404 が
  既存挙動のまま `NOTE_NOT_FOUND` を返す点は意味論的に不自然だが、
  transport 汎用の route-not-found コード追加は範囲外（ADR-0010 決定 8）。
- `app/main.py` の 4 つの exception handler。`handle_validation_error` は
  現在到達不能だが、将来 REST route が追加された際の envelope 不変条件の
  砦として残す（ADR-0010 決定 5）。
- FastAPI の `/docs`/`/redoc`/`/openapi.json`。無認証で残る
  （`docs_url=None` 等は不採用、ADR-0010 決定 2）。例示 Caddy 構成
  （`/api/v1/health` 完全一致）からは到達不能になるが、8000/tcp 直接では
  従来どおり応答する。

### 検証結果

`.venv/bin/pytest -q` → 873 passed（レビュー指摘反映後の実測値。
`query_length`/`q_len` 復活に伴う `tests/test_mcp_tools.py` の追加3件を含む）。
`.venv/bin/ruff check .` → All checks passed。
`.venv/bin/python scripts/export_openapi.py --check` → up to date。
`docker compose config` → この開発環境に docker が無いため未実行
（`compose.yaml` 自体は本変更で触っていない）。

手動確認:
- `openapi.json` の `paths` が `["/api/v1/health"]` のみ、
  `components.securitySchemes` が存在しないこと。
- `TestClient` で `/api/v1/search`・`/api/v1/notes`・`/api/v1/inbox/notes`
  が `{"error": {"code": "NOTE_NOT_FOUND", "message": "Not Found"}}` を
  返すこと（FastAPI 標準の `{"detail": ...}` ではない）。
- `/docs`・`/openapi.json` が 200 のままであること。
- `grep -rn "api/v1/" README.md Usage.md docs/caddy/` の残存参照が
  `/api/v1/health` のみであること。
- `grep -rn "require_token\|InboxNoteCreateRequest\|InboxNoteAppendRequest\|
  RequestSizeLimitMiddleware" app tests` が空であること。
- stale comment sweep（`grep -RniE "REST routers|REST's /search|both
  transports|REST and MCP|MCP and REST|for REST or MCP" app README.md
  Usage.md docs .env.example --exclude-dir=build`）: `app/` 配下のヒットは
  すべて内容として正確（health も REST リクエストである、`ErrorCode` の
  version advertisement は両 transport 共通、等）。`docs/IMPLEMENTATION_
  PLAN.md`・`PHASE2_PLAN.md`・ADR-0001/0004/0006/0007 のヒットは歴史記録
  として残して正しい。
- `.venv/bin/pytest tests/test_mcp_tools.py tests/test_mcp_protocol.py
  tests/test_mcp_auth.py tests/test_health.py -q` → 180 passed
  （MCP 8 ツールと health が無変更で動くこと。実測値は上と同じ理由で更新）。

### PR #21 再レビューでの追加修正（2巡目）

1. **P2**: README OMV checklist の `$QUERY`/`$NOTE`/`$REAL_NOTE_PATH` を
   単純な文字列連結で JSON-RPC body に埋め込んでいたため、値に `"` や `\`
   が含まれると壊れた JSON になる問題を修正。`jq -n --arg` でペイロードを
   組み立てる形に変更し、`jq` が OMV ホストで前提であることを明記した。
2. **P3**: PR 本文・本ファイルのテスト件数を実測値（873/180）に更新。
   これは別途 `query_length`/`q_len` 復活修正（`ce7256f`、`AccessLogMiddleware`
   削除時に誤って落ちていた IMPLEMENTATION_PLAN section 14 の「検索語の
   長さ」要件を `search_notes` の `_McpCall` に復元）で追加された
   `tests/test_mcp_tools.py` の3テストによる増分。
3. 軽微: `test_mcp_access_log_records_query_length_not_the_query` の
   docstring が「削除済みの REST `/search` route が満たす必要がある」と
   読める文言だったため、要件は ADR-0010 以前からの既存要件で現在は
   `search_notes` のみが対象、という文言に修正。

### 未解決・残存事項（ADR-0010 に記録済み）

- 生 Markdown（`content`/`frontmatter`）でのノート新規作成は完全に廃止。
  実運用では未使用という前提に基づく判断。
- `InboxNoteAppendRequest` の `_MAX_CONTENT_CHARS`（2,000,000）上限は消えたが、
  MCP の `append_inbox_note` は元々この上限を持たず、`MAX_REQUEST_BYTES`/
  `MAX_NOTE_SIZE_BYTES` が実効的な bound として変わらず機能する。
- 将来 REST に body を受けるルートを追加する場合、`RequestSizeLimitMiddleware`
  相当と `require_token` 相当の復活が必須（自動的には戻らない）。

# 本文フィールドのrich block化: 表・引用/コールアウト・ネスト箇条書き（ADR-0011）

## 背景

構造化export（ADR-0005）の本文フィールドは`list[Line]`のみで、`one_line`が
改行をすべて空白化するため、表・引用/コールアウト・タスクリスト・ネスト箇条書き
のいずれも保存できなかった。ADR-0009は`procedure.steps`にコードブロックだけを
通したが、その根拠（fenceは自己閉鎖するので無加工でも安全）は表や引用には
転用できない。ADR-0009自身の`tasks/todo.md`エントリが「他モードの本文フィールド
へのrich block化の拡張」を対象外・将来対応として明記していた。本エントリは
その将来対応の実行にあたる。

## 変更内容

3コミットに分割（`32adc0b`・`e4df326`・`43c76d8`、`git log --oneline`で確認可能）。

1. **`BodyItem`一般化 + `TableBlock`**（`32adc0b`）: `app.models`に
   `BulletBlock`（discriminator `"bullet"` — `ProcedureStep.TextBlock`の
   `"text"`とは意味が異なるため別値にした）・`TableBlock`・`BodyBlock`・
   `BodyItem`（`_coerce_body_item`によるbare string後方互換）を新設し、
   `decisions`/`design`/`topics[].points`等20フィールドを`list[Line]`から
   `list[BodyItem]`へ変更。`TableBlock`は生Markdown文字列ではなく
   `headers`/`rows`/`alignments`の構造化入力（表は自己閉鎖しないため、
   列数不一致・空headerは`ValidationError`で明示的に拒否——静かな劣化を
   起こさない）。`app/services/chat_export.py`に`_normalise_body_items`
   （grouping normaliser）・`_normalise_table`・`_render_body_items`
   （grouping renderer：連続bulletを1つのリストにまとめ、table/quoteは
   セクション直下の兄弟blockとして空行で区切る——区切らないと直前のbullet
   リストのlazy continuationに吸収されて消失することをmarkdown-it-pyで
   確認済み）を追加。`_MAX_TOTAL_CODE_CHARS`を`_MAX_TOTAL_BLOCK_CHARS`へ
   改名し、table/code labelも算入対象に追加（値は100,000のまま）。
2. **`QuoteBlock`**（`e4df326`）: 引用/Obsidianコールアウト。`callout`は
   `CodeBlock.language`と同じ方針でパターン検証のみ（語彙固定なし）。
   `title`は`callout`必須。各`lines`に`_escape_block_start`を適用
   （`> # forged`が引用内で本物の見出しになることをmarkdown-it-pyで確認
   済み）。header行の`title`自体は`[!callout] `の後ろで行頭になり得ないため
   `_escape_block_start`不要、かつcode captionと異なりinline Markdownを
   活かすため`_escape_inline`も不要。
3. **`depth`/`checked`**（`43c76d8`）: `BulletBlock`にネスト深さと
   task-list checkboxを追加。深さの逆転（jump）はclampせず`ValidationError`
   で拒否——文字列は残っても要求された構造が黙って書き換わるのは、tableの
   fail-closed方針と不整合になるため。検証は正規化後の列に対して行うが
   （空bulletが脱落した後でないと実際の構造がわからない）、エラーは
   `_NormalisedBullet.source_index`により**クライアント入力側**のindexを
   報告する（正規化後のindexだと、脱落した空bulletの分だけずれて誤った
   項目を指してしまうため）。

主な変更ファイル: `app/models.py`・`app/services/chat_export.py`（3コミット共通）。
テスト: `tests/test_chat_export.py`（schema・formatter・構造・escape・予算の
各層、`_MD_TABLE = MarkdownIt("commonmark").enable("table")`を追加——新規
依存なし）、`tests/test_mcp_tools.py`（schema `$defs`記述）、`tests/test_inbox.py`
（end-to-end書き込み）。`app/mcp_server.py`・`app/application.py`・
`app/services/inbox_service.py`は変更不要（ADR-0009と同じ理由——
`_CREATE_INBOX_NOTE_ALLOWED_ARGUMENTS`は`{"title", "export"}`のままで足りる）。

詳細な設計判断（配置をセクション内の兄弟blockとした理由、tableを構造化入力に
限定した理由、bulletのdepth検証をclampでなくrejectとした理由と`source_index`
の必要性、予算の算入範囲、数式ブロック等を対象外とした理由）は
`docs/adr/0011-*.md`を参照。

## 実装しないもの（対象外、別issueへ）

- 数式ブロック（`$$…$$`）・脚注・水平線・見出しブロック・画像/埋め込み構文
  への対応。`BodyBlock`/`StepBlock`のdiscriminated unionパターンで拡張可能な
  設計にはしたが、本ADRの対象外。
- ADR-0009が記録した将来対応のうち、「他モードの本文フィールドへのrich block化」
  は本エントリで解消した。ただし「対象は今回導入した4種（bullet/code/table/quote）
  のみで、それ以外のblock型は残課題」という限定は変わらない。

## 検証結果

`.venv/bin/pytest -q` → 965 passed。
`.venv/bin/ruff check .` → All checks passed。
`.venv/bin/python scripts/export_openapi.py --check` → up to date
（`ChatExport`はOpenAPIに現れないため`openapi.json`自体は無変更）。
`docker compose config` → この開発環境にdockerが無いため未実行
（`compose.yaml`自体は本変更で触っていない）。

手動確認: 固定`now`での2回レンダリングがbyte一致すること、bullet→table→bullet
がmarkdown-it-pyで`bullet_list_open`/`table_open`/`bullet_list_open`の兄弟列に
なること、列数不一致・空header・depthのjump（0→2）・callout無しのtitleが
それぞれ`ValidationError`で拒否されること、`tempfile.TemporaryDirectory`上の
テスト用Inbox（実Vault・本番`obsidian-api.tokonemore.com`は使用せず）に
table/quote/nested bulletを含むノートを書き込み、生成された`.md`の該当行を
目視確認した。

### PR #22 レビューでの追加修正

1. **P1（機能欠落）**: 承認済み設計（決定1の`BodyItem`図）では section-level
   block として`bullet`/`code`/`table`/`quote`の4種を想定していたが、コミット1
   実装時に`app.models.BodyBlock`を`BulletBlock | TableBlock | QuoteBlock`と
   書いてしまい、`CodeBlock`が本文フィールドで使えなくなっていた
   （`ProcedureStep.blocks`側の`StepBlock`には正しく残っていた）。
   `BodyBlock`に`CodeBlock`を追加（既存モデルの再利用、新規`$defs`は増えない）。
   `app/services/chat_export.py`の`_normalise_body_items`・`_render_body_items`・
   `_body_items_chars`にcodeのdispatchを追加（既存の`_normalise_code_block`/
   `_render_top_level_code_block`/`_code_chars`をそのまま再利用）。
   `tests/test_chat_export.py`に`bullet→code→bullet`・code-onlyフィールド・
   `## コード`へ集約されないこと・予算算入（境界値）・code後の`depth`再開規則の
   テストを追加、`tests/test_inbox.py`にend-to-endテストを追加、
   `tests/test_mcp_tools.py`のdiscriminatorマッピング検証に`"code"`を追加。
2. **P3（ADR記述の誤り）**: `docs/adr/0011-*.md`のNegativeが「`BulletBlock`は
   ADR-0009由来」と誤記していた（`BulletBlock`はADR-0011で新規導入、
   ADR-0009由来なのは`CodeBlock`）。新規`$defs`は`BulletBlock`/`TableBlock`/
   `QuoteBlock`の3つ（`CodeBlock`は再利用で新規ではない）と修正。ADR本文の
   Scope・決定1・決定7・決定8・Positive・Referencesも、`BodyItem`が`code`を
   含むことを反映するよう修正。README.md・Usage.md・
   `docs/IMPLEMENTATION_PLAN.md`にも同様の記述漏れがあったため修正。

修正後の検証結果: `.venv/bin/pytest -q` → 973 passed。
`.venv/bin/ruff check .` → All checks passed。
`.venv/bin/python scripts/export_openapi.py --check` → up to date。

# 本文フィールドのbare stringをParagraph-firstへ変更（ADR-0012）

## 背景

ADR-0011は本文フィールドをrich block化したが、bare stringのcoerce先を
`BulletBlock`とした（`_coerce_body_item`）ため、`context`/`design`/`verification`等へ
渡した普通の説明文が必ず`- `箇条書きになり、さらに`one_line()`が改行をすべて空白へ
潰すため段落構造を保持できなかった。Gatewayが固定すべきはexport mode/章の種類/章名/
章順/frontmatterまでであり、章本文の通常文章までbulletへ強制変換することではない。
本エントリはbare stringの意味を`ParagraphBlock`へ変更し、複数行・空行を保持する
段落を本文フィールドの既定表現にする。意図した破壊的変更であり、既存Vaultノートの
migrationは行わない。

設計段階の実測（markdown-it-py）で、`_escape_block_start`の`_BLOCK_HAZARD_RE`に
既存の穴が3つ見つかった。setext見出し下線（`--`/`=`/`==`等）とGFM表区切り行は
2行以上のプレーンテキストが隣接する場合のみ成立し、複数行`ParagraphBlock`が
初めてその経路を作るため放置すると確実に踏む。加えてthematic break
（`***`/`_ _ _`等）は`BulletBlock`・`tldr`で**既に稼働中**（`- ***`が list item内で
`<hr>`になりテキストが消失、`tldr=["***"]`も同様）で、setextも多行`QuoteBlock`で
既に稼働中（`> a`/`> ==`が引用内に本物の見出しを生成）だった。既存テストが
これらを見逃していた理由は、`_QUOTE_HAZARD_LINES`が1行引用でのみ検証しており、
setextの成立に必要な「直前のテキスト行」が存在しなかったため。同じ
`_escape_block_start`に依存する変更のため、この3つの穴はSlice 1として同PRで
修正した（別PRへの分割も検討したが、段落実装の正しさの議論自体がこの修正に
依存するため一体で扱った）。

## 変更内容

5スライスに分割。

1. **`_escape_block_start`のhazard集合完成**（先行スライス、独立して正しい）:
   `_SETEXT_UNDERLINE_RE`（全て`=`か全て`-`の下線）・`_THEMATIC_BREAK_RE`
   （同一文字3個以上、空白区切り可）・`_TABLE_DELIMITER_RE`（GFM区切り行の
   保守的superset——Obsidianのtable parserはmarkdown-itではないため厳密な
   文法転写は避けた）を追加し、`_BLOCK_HAZARD_RE`の旧`-{3,}$|={3,}$|_{3,}$`
   （いずれも新regexの真部分集合）を削除。既存テストのピン値は一切変わらない
   （実測で確認済み）。
2. **`ParagraphBlock`追加**（加算のみ、shorthandはまだbullet）: `app/models.py`に
   `_MAX_PARAGRAPH_CHARS = 8_000`（`Line`の1,000ではなく`CodeContent`と同じ——
   段落は「1フィールド=1行」という`Line`の前提を持たないため）・
   `ParagraphContent`・`ParagraphBlock`（`min_length`なし——bare string shorthandの
   受け皿として旧`list[Line]`と同等以上に permissive でなければならない）を新設し、
   `BodyBlock`へ追加。`app/services/chat_export.py`に専用の`_canonicalise_paragraph`
   （`one_line`は使わない）を新設: 改行・空行を保持（内部空行数も畳まない——
   要求する決定性は「同じ入力→同じ出力」のみ）、行末空白は除去、ブロック先頭行
   （先頭行・内部空行の直後）のみASCII空白/タブを除去し継続行のインデントは保持
   （`expandtabs`はしない——継続行のインデントはCommonMarkが破棄するため安全かつ
   無害、タブ展開は入力内容そのものを書き換えてしまう）。`_render_paragraph`は
   各行のインデントを分離した本文へ`_escape_block_start`を無条件適用してから
   継続行のみ元インデントを再結合する（ブロック先頭判定の正しさに依存しない
   fail-closed設計）。`_paragraph_chars`は改行separatorも文字数へ算入する
   （`_quote_chars`とは異なる——`QuoteBlock.lines`は`list[Line]`で改行が入力に
   含まれないが`ParagraphContent`は1つの文字列で改行も入力の一部。算入しないと
   改行主体の段落で予算を実質2倍迂回できることを実測で確認した）。
3. **shorthandを反転**（breaking change本体）: `_coerce_body_item`が
   `{"type": "paragraph", ...}`を返すよう変更、`json_schema_input_type`も
   `ParagraphContent | BodyBlock`へ。この1スライスの差分が「意味的に何が変わったか」
   そのものになる。
4. **MCP schema/description**: `create_inbox_note`のdescriptionへ
   「plain string = paragraph、実際のリストのときだけ`type: bullet`」等の短い
   段落とJSON例を1つ追加。`app/models.py`の7フィールドのdescriptionから
   bullet示唆語句（"one point per item"等）を削除（`<mode> mode only.`の
   接頭辞は維持——`_FIELD_OWNER_MODES`駆動テストが依存するため）。
5. **文書**: `docs/adr/0012-*.md`新規（ADR-0011決定1・決定5の該当部分を
   supersede、決定2-4・6-8は無変更と明記）。README.md/Usage.md/
   `docs/IMPLEMENTATION_PLAN.md`§12・§17/本エントリを更新。

主な変更ファイル: `app/models.py`・`app/services/chat_export.py`・
`app/mcp_server.py`（3スライス共通）。テスト: `tests/test_chat_export.py`
（新規セクション「Paragraph-first body blocks」+ 既存19テストの期待値更新+
hazard集合完成のための新規テスト）、`tests/test_mcp_tools.py`
（discriminator mapping・`ParagraphBlock`schema・description）、
`tests/test_inbox.py`（2箇所——1つは期待値更新、1つはbare stringの直後に
`depth:1`bulletが続く構成が新仕様下で`ValidationError`になるため明示bulletへ
変更。breaking changeの最も分かりやすい実例）。

## 実装しないもの（対象外、別issueへ）

- raw Markdown作成API・`content`/`frontmatter`の`create_inbox_note`復活・
  クライアント指定frontmatter・既存Vaultノートのmigration・REST再拡大・
  クライアントによる見出し自由指定・任意のネストしたMarkdown AST・
  `ProcedureStep`の全面再設計・`ParagraphBlock`を`StepBlock`へ追加すること・
  数式ブロック/脚注/水平線/見出しブロック/画像埋め込み（ADR-0011の非目標を継承）
- `create_inbox_note`のwrite pathに`Settings.max_note_size_bytes`相当のbyte capが
  無いという既存ギャップ（`models.py:145-148`が既に記録済み）の解消
- bullet・`Line`フィールドまで`_MAX_TOTAL_BLOCK_CHARS`へ統合すること

## 検証結果

`.venv/bin/pytest -q` → 1146 passed（既存973 + 新規173）。
`.venv/bin/ruff check .` → All checks passed。
`.venv/bin/python scripts/export_openapi.py --check` → up to date
（`ChatExport`はOpenAPIに現れないため`openapi.json`自体は無変更）。
`docker compose config` → この開発環境にdockerが無いため未実行
（`compose.yaml`自体は本変更で触っていない）。

各スライスの境界で上記3コマンドを実行し全て通過を確認。Slice 1・Slice 3では、
新規テストが修正前のコードに対して実際に失敗することを該当行の一時差し戻しで
確認した（setext/thematic breakの6テスト、shorthand反転前のbare string→paragraph
テスト）。

手動確認: `markdown-it-py`で
`paragraph→bullet→bullet→code→paragraph→table→quote→paragraph`が
`level==0`の兄弟トークン列になること（ネストしたbulletがloose listとなり
list item内にも`paragraph_open`が出ることを`level`属性で除外して確認）、
段落+YAML+段落のgolden testでコードのインデント・内部空行・fence前後の
blank lineが完全一致すること、setext（`--`/`=`/`==`）・thematic break
（`***`/`_ _ _`）・GFM区切り行（`--- | ---`等）のいずれも先頭バックスラッシュ1つで
無効化でき表示が変わらないこと、`tempfile.TemporaryDirectory`上のテスト用Inbox
（実Vault・本番`obsidian-api.tokonemore.com`は使用せず）へ段落+YAMLを含むノートを
書き込み生成された`.md`を目視確認した。
