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
