Status: Completed

Phase 1 was implemented and verified on OMV.
The original ChatGPT Actions direction was superseded by the MCP architecture
described in docs/MCP_IMPLEMENTATION_PLAN.md.

# Obsidian Vault Gateway — Phase 1 実装プラン

`docs/IMPLEMENTATION_PLAN.md` の Phase 1（§16「最小API」）に対する詳細設計。
本文中の `§` 参照はすべて `docs/IMPLEMENTATION_PLAN.md` の節番号を指す。

## 1. 目的とスコープ

`docs/IMPLEMENTATION_PLAN.md` は ChatGPT から Obsidian Vault を検索・参照・保存する
REST ゲートウェイの全体計画（Phase 1〜4）を定義している。本プランはそのうち
**Phase 1 のみ**を対象とする。

到達目標（§16 Phase 1 完了条件）:
curl だけで全 API が動作し、Vault 全体を検索でき、`00_Inbox/ChatGPT` へノートを作成でき、
**それ以外へは一切書き込めない**こと。

### 決定事項

| 項目 | 決定 |
|---|---|
| 実装範囲 | §16 Phase 1 リスト通り。§6.7 append / §6.4 tree / §6.5 summary は**実装しない** |
| ファイル配置 | リポジトリルート直下（`obsidian-api/` サブディレクトリは作らない） |
| 実行環境 | ローカルは `.venv` + Python 3.12 で pytest。Docker イメージは `python:3.13-slim` 固定、`requires-python = ">=3.12"` |
| Vault パス | `.env` 変数（`VAULT_HOST_PATH` / `INBOX_HOST_PATH`）に切り出し、`.env.example` に OMV 例を記載 |

### 環境制約

開発機は WSL2 / Python 3.12.3。**docker が存在しない**ため、`docker compose config`、
§15「Docker権限テスト」、「LiveSync確認」はローカルで実行できない。OMV 上で実施する
検証手順を README に用意する（§7 参照）。

## 2. 実装するエンドポイント

| # | エンドポイント | operationId | 認証 |
|---|---|---|---|
| §6.1 | `GET /api/v1/health` | `getHealth` | 不要 |
| §6.2 | `GET /api/v1/search` | `searchNotes` | 必要 |
| §6.3 | `GET /api/v1/notes?path=<vault相対パス>` | `readNote` | 必要 |
| §6.6 | `POST /api/v1/inbox/notes` | `createInboxNote` | 必要 |

## 3. ファイル構成

```text
app/
  main.py            FastAPI app・例外ハンドラ・ミドルウェア登録
  config.py          pydantic-settings による環境変数読み込み（キャッシュ）
  auth.py            HTTPBearer + secrets.compare_digest
  models.py          リクエスト/レスポンスの Pydantic モデル
  exceptions.py      GatewayError と §13 エラーコード定義
  middleware.py      アクセスログ・リクエストサイズ制限
  routers/           health.py / search.py / notes.py / inbox.py
  services/          path_security.py / markdown_parser.py /
                     search_service.py / inbox_service.py / filenames.py
tests/
  conftest.py        fixture Vault を tmp_path に構築（symlink 等は実行時生成）
  fixtures/vault/    プレーンな .md 群のみコミット
  test_health.py test_auth.py test_path_security.py
  test_search.py test_notes.py test_inbox.py
  test_logging.py test_openapi.py
scripts/export_openapi.py
Dockerfile  .dockerignore  compose.yaml  pyproject.toml
.env.example  .gitignore  README.md  openapi.json
docs/PHASE1_PLAN.md              本ファイル
docs/caddy/obsidian-api.Caddyfile
tasks/todo.md
```

§5 の `routers/vault.py` と `services/vault_service.py` は Phase 2 用のため作らない。

## 4. 設計の要点

### 4.1 パスセキュリティ（§7）— `services/path_security.py`

`resolve_read_path(raw) -> Path` を単一の入口とし、順に検証する。
1 つでも失敗したら即座に例外。

1. 空文字 / null byte (`\x00`) を拒否
2. **二重デコード対策**: `unquote(value) != value` なら再デコード後の値にも全検証を適用
   （`%2e%2e%2fsecret.md` を拒否）
3. バックスラッシュ `\` を拒否
4. 絶対パス（先頭 `/`、Windows ドライブレター `^[A-Za-z]:`）を拒否
5. `/` で分割し、各要素が `..` / `.` / 空文字 / `.` 始まり（隠しファイル）なら拒否
6. 拡張子が `.md` 以外なら `INVALID_FILE_TYPE`
7. 全体長 1024 / 要素長 255 を超えたら拒否
8. **root 直下から 1 要素ずつ `is_symlink()` を検査**して拒否。
   `resolve()` の前に行うのが要点（`resolve()` は symlink を黙って追従するため）
9. `resolve(strict=True)` → `is_relative_to(READ_ROOT.resolve())` を確認
10. `stat.S_ISREG` で通常ファイルであることを確認

`VAULT_READ_ROOT` / `VAULT_INBOX_ROOT` 自体は起動時に一度 `resolve()` してキャッシュし、
symlink 検査の対象外とする（マウントポイントが symlink の構成を許容するため）。

書き込み（Inbox作成）はここで想定していた「caller指定パスをINBOX_ROOT配下か検証する」
形にはならなかった。実装ではcallerはパスを一切渡さず、常にサニタイズ済みtitleから
サーバ側でファイル名を導出するため、検証すべき「caller指定の書き込みパス」自体が
存在しない（§5 項目12、§4.3）。

### 4.2 ファイル名サニタイズ（§8）— `services/filenames.py`

NFC 正規化 → 制御文字除去 → `/ \ : * ? " < > |` を `-` に置換 →
空白畳み込みと前後の空白・ピリオド除去 → 100 文字で切り詰め → 空なら `INVALID_TITLE` →
Windows 予約名（CON/PRN/AUX/NUL/COM1-9/LPT1-9、大小無視）なら `INVALID_TITLE` → `.md` 付与。

### 4.3 原子的な書き込み（§17）— `services/inbox_service.py`

**新規作成では `os.replace()` は使わない。** `os.replace()` は既存ファイルを
上書きするため、§6.6「既存ファイルを上書きしない」と両立しない。代わりに:

> Phase 2 の `append_inbox_note`（既存ノートへの追記、`PHASE2_PLAN.md` §6）は
> 例外として `os.replace()` を使用する。対象は解決済み・存在確認済みの既存
> ノートに限られ、ここで述べる新規作成の上書き禁止は緩めない。詳細は
> `docs/adr/0003-allow-os-replace-for-inbox-append.md`。

1. Inbox ディレクトリ内に隠し一時ファイル（`.tmp-<random>`）を作成 → 書き込み → `fsync`
2. 候補名 `title.md`, `title-2.md`, … に対して `os.link(tmp, candidate)` を試行。
   既存なら `FileExistsError` で次の候補へ（**アトミックかつ非破壊**）
3. 成功したら `os.unlink(tmp)`、ディレクトリを `fsync`
4. 100 回試して空きが無ければ `NOTE_ALREADY_EXISTS`
5. `finally` で一時ファイルを必ず後始末。`os.link` が `OSError` なら `INTERNAL_ERROR`
   （黙って非原子的な方法へ劣化させない）

一時ファイルは `/tmp`（tmpfs = 別 FS）ではなく **Inbox 内**に作る必要がある
（`os.link` は同一 FS 必須、かつコンテナは `read_only: true`）。名前を `.` 始まりに
することで Obsidian / LiveSync / 自身の検索走査から除外される。

### 4.4 検索（§6.2）— `services/search_service.py`

- `os.walk` で走査し、`dirnames` をその場で書き換えて `.` 始まりディレクトリ
  （`.obsidian` / `.git` / `.trash`）を枝刈り。symlink と `.md` 以外はスキップ
- 照合は **NFKC 正規化 + casefold** を query と対象の両方に適用
  （日本語の全角/半角差、大文字小文字を吸収）
- 対象フィールド: ファイル名 / YAML `title` / YAML `tags` / 本文 / 見出し
- 並び順: タイトル・ファイル名一致 > 見出し一致 > タグ一致 > 本文一致、
  同点は `modified_at` 降順
- `excerpt`: 本文の最初のヒット周辺 ±100 文字（空白畳み込み、frontmatter は含めない）。
  ヒットが無い場合は本文冒頭 200 文字を返す
- `limit`: 既定 20、`MAX_SEARCH_RESULTS`(50) でクランプ
- `modified_at`: `st_mtime` を `ZoneInfo(TZ)` 付き ISO8601 で返す（例と同じ `+09:00`）

### 4.5 ノート読み取り（§6.3）— `routers/notes.py`

- **パスはクエリパラメータで受ける**: `GET /api/v1/notes?path=Knowledge/PC/GPU/RTX 5070.md`。
  §6.3 は `/notes/{note_id}` にエンコード済みパスを埋める形だが、エンコードされた
  スラッシュ (`%2F`) の扱いは ASGI サーバ・リバースプロキシ・ChatGPT Actions の
  それぞれで挙動が異なり、パス検証の前段に不確実性が入る。クエリパラメータなら
  Starlette が 1 回だけデコードした値がそのまま渡り、検証対象が一意に定まる
- 受け取った値は `resolve_read_path()` で検証する。二重デコード検査もそのまま
  適用されるため `%2e%2e%2fsecret.md` / `%252e%252e%252f…` はいずれも拒否される
- 応答の `id` / `path` は Vault ルート相対パスで、そのまま `?path=` に渡せる
  （検索結果 → 読み取りが素通しでつながる）
- `st_size > MAX_NOTE_SIZE_BYTES` なら文字数で切り詰めて `truncated: true`。
  `truncated` は常に返す（スキーマ固定のため）
- `newline=""` で開き、CRLF を変換しない（§17「改行コードを尊重する」）
- YAML が壊れていても例外を投げず `frontmatter: {}` + 全文を content として返す（実 Vault 耐性）
- frontmatter の値は JSON 安全な型へ再帰変換してから返す

### 4.6 Inbox 作成（§6.6）— `routers/inbox.py`

- リクエスト: `title` (1..300) / `content` /
  `frontmatter`（`dict[str, str|int|float|bool|list[str]|None]` に型で制限 →
  任意 YAML 注入を Pydantic 段階で拒否）
- 保存パスは API 側で固定。クライアントからパスは受け取らない
- `yaml.safe_dump(..., allow_unicode=True, sort_keys=False)` で frontmatter ブロックを
  生成。本文は LF 正規化 + 末尾改行
- 201 を返し、`id`/`path` は **Vault ルート相対**（`00_Inbox/ChatGPT/foo.md`）で返す。
  `GET /notes?path=...` にそのまま渡せることを保証し、絶対パスを露出しない

> **計画書に無い設定を 1 つ追加する。** コンテナ内では読み取り (`/vault-ro`) と
> 書き込み (`/vault-write/inbox`) が別マウントなので、Inbox の Vault ルート相対パスを
> プログラム的に導出できない。`VAULT_INBOX_RELATIVE_PATH`（既定 `00_Inbox/ChatGPT`）を追加する。

### 4.7 エラー（§13）・ログ（§14）

- `{"error": {"code", "message"}}` に統一。FastAPI 既定の `{"detail": ...}` を潰すため
  `HTTPException` / `RequestValidationError` / 未捕捉例外の各ハンドラを差し替える。
  絶対パスとスタックトレースは応答に含めない（トレースはサーバ側ログのみ）
- **uvicorn のアクセスログは無効化する**（`--no-access-log`）。既定のアクセスログは
  クエリ文字列全体を出力するため、検索語 `q` が漏れる
- 自前ミドルウェアで 1 リクエスト 1 行: 日時 / メソッド / ルート / ステータス / 処理時間。
  `q` は**値ではなく長さ**のみ記録。読み取ったノートと作成した Inbox ノートの相対パスは
  ルータ側から明示的に記録（§14 の要求項目）
- リクエストサイズ: `Content-Length > MAX_REQUEST_BYTES`(2MB) で `413 FILE_TOO_LARGE`。
  Caddy 側の `max_size` と二重に防御

### 4.8 Docker（§9 §10）

- `python:3.13-slim-bookworm` を multi-stage で。builder が venv を作り、最終段はそれをコピー
- 非 root（uid 10001）、`HEALTHCHECK` は slim に curl が無いので `python -c` + `urllib`
  で `/api/v1/health` を叩く
- `read_only: true` 下では `.pyc` を書けないため、ビルド時に `compileall` +
  `PYTHONDONTWRITEBYTECODE=1`
- compose は §9 の `security_opt` / `cap_drop: ALL` / `read_only` / `tmpfs` を維持し、
  ボリュームのホスト側だけ `${VAULT_HOST_PATH}` / `${INBOX_HOST_PATH}` に置換

### 4.9 依存関係

実行時: `fastapi` / `uvicorn[standard]` / `pydantic` / `pydantic-settings` /
`PyYAML`。開発時: `pytest` / `httpx` / `ruff`。
すべて `==` で固定し、`requirements.lock`（`pip freeze`）もコミットする。

§4 の「Markdown解析用ライブラリ」は Phase 1 では**採用しない**。必要なのは見出し抽出
（正規表現で足りる）と本文のそのまま返却だけで、パーサを入れても未使用の重量になる。

`python-frontmatter` も採用しない — 当初この節はランタイム依存として明記していたが、
実装時に発見した挙動（§5 項目11）により不採用とした。

## 5. 計画書からの逸脱・前提

1. `os.replace()` を使わず `os.link()` を使う（上書き禁止と原子性の両立のため。§4.3）
2. Markdown パーサライブラリを入れない（見出しは正規表現。§4.9）
3. `VAULT_INBOX_RELATIVE_PATH` 設定を追加（相対パス返却のため。§4.6）
4. `search` の `cursor` パラメータは Phase 1 では**受け付けない**（ページングは §16 Phase 2）。
   ただし応答の `next_cursor` は常に `null` として残し、スキーマの前方互換を保つ
5. `tags` 複数指定は **AND**（絞り込み）とする。§6.2 に明記が無いため
6. `.md` 拡張子は小文字のみ許可
7. §6.6 の応答形式は計画書に無いため `{id, path, title, modified_at}` + `Location` ヘッダとする
8. symlink 拒否のため、実 Vault 内に symlink されたノートがあれば不可視になる
9. 添付ファイルは対象外（§3）
10. **ノート読み取りはパスパラメータではなくクエリパラメータ** (`GET /notes?path=...`)。
    エンコード済みスラッシュの扱いを回避するため（§4.5）
11. **`python-frontmatter` ライブラリを採用しない**（§4 は技術構成として明記しているが、
    実装時に発見した挙動により不採用とした）。このライブラリの `loads()` は入力全体の
    `\r\n` を無条件に `\n` へ正規化してから frontmatter を検出する
    （`frontmatter.util.u()`）。これは frontmatter が無いノートを含む**すべての読み取り**で
    CRLF を静かに書き換えてしまい、§17「改行コードは既存ノートを尊重する」と直接矛盾する。
    代わりに `services/markdown_parser.py` で開始・終了デリミタを自前の正規表現で検出し、
    YAML 本体だけを `yaml.safe_load()`（既存の PyYAML 依存）に渡し、本文は元テキストから
    そのままスライスして返す。依存関係からは `python-frontmatter` を除いた
12. **`resolve_inbox_write_path()` は実装しなかった（§4.1）。** ノート作成は caller から
    パスを一切受け取らず、常にサニタイズ済み title からサーバ側でファイル名を導出する
    （`filenames.sanitise_title` → `os.link()` の `FileExistsError` リトライ、§4.3）ため、
    「caller 指定の書き込みパスを検証する」という前提自体が実装と一致しなかった

## 6. ローカル検証

```bash
python3 -m venv .venv && .venv/bin/pip install -e '.[dev]'
.venv/bin/pytest -q
.venv/bin/ruff check .
.venv/bin/python scripts/export_openapi.py --check   # openapi.json の差分検出
```

テストは §15 の項目を網羅する。セキュリティは以下を**すべて拒否**することを検査:
`../secret.md` / `../../.obsidian/config` / `%2e%2e%2fsecret.md` / `..\secret.md` /
`/vault/secret.md` / symlink / `test.txt` / `.hidden.md` / null byte / `a//b.md`

その他の重点テスト:

- 認証: トークン無し・誤り・不正形式で 401、`/health` は認証不要
- 検索: 日本語、全角/半角、大小無視、タグ AND、`folder` 絞り込み、`limit` の 50 クランプ、
  `.obsidian` と隠しファイルが結果に出ない、excerpt に frontmatter が混入しない
- 読み取り: frontmatter 解析、WikiLink 保持、壊れた YAML の許容、切り詰めフラグ、
  CRLF 保持、`path` 未指定で 400、`path` に `%2e%2e%2f` を入れて拒否
- Inbox: 連番 `-2`/`-3`、日本語ファイル名、禁止文字置換、先頭ピリオド拒否、
  Windows 予約名拒否、100 文字切り詰め、**既存ファイルを上書きしない**、
  一時ファイルが残らない、Inbox 外に書き込まない、作成直後に `GET /notes?path=...` で
  読める、2MB 超で 413
- ログ: トークン / 本文 / `q` の値がログに出ない（`caplog` で検査）
- OpenAPI: 全 operationId が存在し、コミット済み `openapi.json` がアプリと一致

**Inbox の書き込みテストは必ず `tmp_path` 上の一時 Vault に対して行う。**
`conftest.py` が fixture をコピーして作った使い捨てディレクトリを `VAULT_READ_ROOT` /
`VAULT_INBOX_ROOT` として注入し、実 Vault には一切書き込まない
（AGENTS.md「自動テストで実際の Vault を変更しない」）。symlink・隠しファイル・
巨大ファイルも `conftest.py` が実行時に生成する（リポジトリに symlink をコミットしない）。

## 7. OMV 上の検証

docker が無いためローカルでは実行できない。README に checklist として記載する。
curl はすべて `-fsS` を使う（`-f` で HTTP エラーが終了コードに出るため、
checklist をコピペしたときに失敗を見逃さない）。

```bash
docker compose config          # §18 必須
docker compose up -d

BASE=https://obsidian-api.example.com/api/v1
curl -fsS "$BASE/health"
curl -fsS -H "Authorization: Bearer $API_TOKEN" --get "$BASE/search" \
     --data-urlencode 'q=RTX 5070' --data-urlencode 'limit=5'
curl -fsS -H "Authorization: Bearer $API_TOKEN" --get "$BASE/notes" \
     --data-urlencode 'path=Knowledge/PC/GPU/RTX 5070.md'
curl -fsS -X POST -H "Authorization: Bearer $API_TOKEN" -H 'Content-Type: application/json' \
     -d '{"title":"Gateway smoke test","content":"# Gateway smoke test\n"}' "$BASE/inbox/notes"

# コンテナ内権限確認
docker compose exec obsidian-api sh -c 'id; touch /vault-ro/x 2>&1; touch /vault-write/inbox/x && rm /vault-write/inbox/x'
```

> **本番スモークテストで作ったノートは手動で削除する必要がある。**
> Phase 1 には削除 API が無く（§3「実装しない操作」）、ゲートウェイ経由では消せない。
> 上の `POST /inbox/notes` は実 Vault の `00_Inbox/ChatGPT/` にノートを作成するため、
> 確認後に Obsidian または OMV 上のファイル操作で手動削除する。

続けて §15 の LiveSync 確認（Vault 生成 → livesync-cli 検出 → CouchDB → PC → iPhone）を
手動で行う。この確認で作ったノートも同様に手動削除の対象。

## 8. 進め方

`tasks/todo.md` にチェックリストを作り、§18 に従って小さなコミットへ分割する
（空リポジトリなので 1 つ目が initial commit）。

0. 本プランを `docs/PHASE1_PLAN.md` として保存する
1. scaffolding（`pyproject.toml` / `.gitignore` / `.env.example` / README 骨子）
2. config + auth + エラーエンベロープ + health
3. path_security + markdown_parser + filenames（+ テスト）
4. search（+ テスト）
5. notes 読み取り（+ テスト）
6. inbox 作成（+ テスト）
7. ログ・リクエストサイズ制限ミドルウェア
8. Dockerfile / compose / Caddy 設定例 / `openapi.json` 出力
9. README と OMV 検証 checklist

各コミット前に `pytest` と `ruff` を実行する。完了時に変更ファイル一覧・テスト結果・
未解決事項（Docker と LiveSync 検証が未実行であること）を報告する。
