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
