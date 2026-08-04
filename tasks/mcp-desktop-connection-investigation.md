# 調査記録 — ChatGPT デスクトップアプリから `/mcp` に接続できない

日付: 2026-08-02
対象: Phase 2 完了時点の Gateway（`03e5059`）
状態: **解決済み**（§7-B を適用。§8 の副次的発見のうち 1 は未対応）

> AGENTS.md「production paths / tokens を commit しない」に従い、本書ではホスト名を
> `obsidian-api.example.com`（実値は `.env` の管理下）、Windows ユーザー名を `<user>`
> にマスクしている。トークンは一切記載しない（照合はハッシュ比較のみで行った）。

---

## 1. 結論

**Gateway 側は完全に正常。** 原因はクライアント側にあり、以下の一点に集約される。

> ChatGPT デスクトップアプリは Codex エージェントを **WSL の Linux プロセス**として起動する
> （`runCodexInWindowsSubsystemForLinux = true`）。Codex は `bearer_token_env_var` を
> **自プロセスの環境変数**から解決するが、トークンは **Windows の User 環境変数**にしか
> 存在せず、`WSLENV` による転送も設定されていないため、WSL 側のプロセスには届かない。

結果、Codex は `Authorization` ヘッダなしでリクエストし、Gateway が仕様どおり 401 を返し、
クライアントは「サーバーが ready でない」と表示する。

## 2. 症状

- ChatGPT デスクトップアプリで `/mcp obsidian_vault` が失敗（2026-08-02 20:08 頃）
- 一方 `https://obsidian-api.example.com/api/v1/health` は 200 を返す

### 注意: モデルの発言は証拠にならない

失敗時にモデルが返した «The `obsidian_vault` MCP server is configured but isn't currently
ready» という文言は、**証拠として扱ってはいけない**。サーバー名 `obsidian_vault` は
ユーザーのプロンプト（`/mcp obsidian_vault`）に含まれていたため、モデルは
「設定されている」ことを実際には確認せずに言及できる。この文言を根拠に切り分けを
進めると誤誘導される。

## 3. 環境

| 項目 | 値 |
|---|---|
| ChatGPT デスクトップアプリ | build 26.727.51351（Windows） |
| Codex（アプリ内蔵、WSL 実行） | `/mnt/c/Users/<user>/.codex/bin/wsl/…/codex … app-server` |
| Codex CLI（WSL に別途インストール） | `codex-cli 0.146.0` |
| WSL ディストリビューション | Ubuntu-24.04 |
| 有効な `CODEX_HOME` | `/mnt/c/Users/<user>/.codex`（= Windows 側） |

## 4. サーバー側の検証（すべて正常）

実 URL に対して MCP ハンドシェイクを流した結果。

| 検査 | 結果 |
|---|---|
| `GET /api/v1/health` | 200 |
| `POST /mcp` `initialize`（`protocolVersion: 2025-06-18`） | 200 / instructions・capabilities 正常 |
| `POST /mcp` `initialize`（`protocolVersion: 2025-03-26`） | 200 / 同バージョンでネゴシエート |
| `POST /mcp` `tools/list` | 7 ツール全件（`get_health` … `append_inbox_note`） |
| `POST /mcp` `notifications/initialized` | 202 |
| `POST /mcp`（Bearer なし） | 401 + `WWW-Authenticate: Bearer error="invalid_token"` |
| `DELETE /mcp` | 405 |
| `GET /mcp`（`Accept: text/event-stream`） | 200 でストリーム確立、イベントなしで待機 |
| `MCP_ALLOWED_HOSTS` | 実ホスト名と一致 → DNS リバインド保護は通過 |

ステートレス構成（`stateless_http=True`）のため `Mcp-Session-Id` なしで
`tools/list` が通ることも確認済み。**サーバー・Caddy・トークン検証・ホスト許可リストの
いずれにも問題はない。**

## 5. クライアント側の検証

### 5.1 有効な設定ファイル

`/mnt/c/Users/<user>/.codex/config.toml`（**Windows 側**）が有効。

```toml
[mcp_servers.obsidian_vault]
enabled = true
url = "https://obsidian-api.example.com/mcp"
bearer_token_env_var = "OBSIDIAN_MCP_TOKEN"
```

URL・`bearer_token_env_var` 名ともに正しい。

同ファイル 74-80 行に決定的な設定がある。

```toml
[desktop]
integratedTerminalShell = "wsl"
runCodexInWindowsSubsystemForLinux = true
```

同ファイル末尾に **WSL 形式のパス**（`/mnt/c/Users/<user>/Documents/Codex/…`）の
`[projects.…]` エントリが書き込まれている。これは
**WSL で動く Codex が Windows 側の config.toml を読み書きしている**直接の証拠。

### 5.2 トークンの所在と正しさ

| スコープ | `OBSIDIAN_MCP_TOKEN` |
|---|---|
| Windows User 環境変数（レジストリ永続） | **SET**（64 文字） |
| Windows Machine 環境変数 | UNSET |
| WSL の現行シェル | UNSET |
| `~/.bashrc` / `~/.bash_profile` / `~/.profile` / `/etc/environment` | どこにも定義なし |

Windows User 環境変数の値は `.env` の `API_TOKEN` と **SHA-256 完全一致**
（前後空白なし、trim 前後で同一ハッシュ）。つまり**トークン自体は正しい**。

### 5.3 実プロセスの環境（決定的証拠）

デスクトップアプリが起動した Codex エージェント本体:

```
pid <PID>
  cmd:  /mnt/c/Users/<user>/.codex/bin/wsl/<hash>/codex \
          -c features.code_mode_host=true app-server --analytics-default-enabled
  親系譜: WSL init 経由で直接起動（ログインシェルを介さない）
  CODEX_HOME=/mnt/c/Users/<user>/.codex
  SHLVL=0
  OBSIDIAN_MCP_TOKEN  → 存在しない          ← /proc/<PID>/environ を直読
```

同プロセスの `WSLENV` はアプリが自前生成したリストで、`OBSIDIAN_MCP_TOKEN` を
**含まなかった**。永続 `WSLENV` は User / Machine とも **未設定**であり、当該リストは
完全にアプリ生成物のため、**アプリが `WSLENV` を丸ごと設定している可能性が高い**
（後述 7-B のリスク）。

### 5.4 Codex 側に代替の注入経路はない

`codex mcp add --help` より:

- `--env <KEY=VALUE>` … **"Only valid with stdio servers"**
- `--bearer-token-env-var <ENV_VAR>` … "Only valid with streamable HTTP servers"

→ HTTP（streamable）サーバーの Bearer トークンは
**Codex プロセス自身の環境変数からしか取得できない**。サーバー単位の `env` は使えない。
他の認証手段は OAuth のみで、本 Gateway は OAuth 非対応。

## 6. 棄却した仮説

| 仮説 | 棄却理由 |
|---|---|
| サーバーが落ちている / MCP 未提供 | §4 でハンドシェイク全通過 |
| URL が誤り（`/api/v1/health` を指している等） | 設定は `…/mcp` で正しい |
| `MCP_ALLOWED_HOSTS` による DNS リバインド拒否 | 実ホスト名と一致、`initialize` が 200 |
| トークンの値が違う / 空白混入 | SHA-256 完全一致、長さ 64 |
| `experimental_use_rmcp_client` が必要 | 0.146.0 では不要。`codex mcp list` が HTTP サーバーを警告なく列挙 |
| **アプリを再起動すれば直る**（初期仮説） | **誤り。** Windows User 環境変数は `WSLENV` なしでは WSL に渡らないため、何度再起動しても届かない |
| `~/.bashrc` / `~/.profile` に書けば届く | `/init` からの直起動・`SHLVL=0` で**シェルが一切走らない**ため読まれない |
| `/etc/environment` に書けば届く | 未適用。`/etc/environment` の `PATH` にある `/snap/bin` が pid <PID> の `PATH` に無い（WSL interop 起動は PAM セッションを作らない）|
| WSL 側 `~/.codex/config.toml` を直せば直る | そちらは有効な設定ではない（§5.1）。ただし別問題として §8 参照 |

## 7. 対処（B を適用して解決）

Windows の User 環境変数として `WSLENV=OBSIDIAN_MCP_TOKEN/u` を新規作成し、
アプリを再起動して接続成功を確認した。

**懸念していた「アプリが `WSLENV` を自前生成しているため上書きされる」は外れた。**
User スコープの `WSLENV` はそのまま残り、トークンが WSL 側の Codex プロセスへ
届いている。適用後の確認:

```console
$ powershell.exe -NoProfile -Command "[Environment]::GetEnvironmentVariable('WSLENV','User')"
OBSIDIAN_MCP_TOKEN/u
```

`config.toml` の `[mcp_servers.obsidian_vault]` ブロックは変更していない
（＝§8-1 の承認設定の欠落は未解決のまま残っている）。

以下は検討した3案の記録。

### A. アプリの GUI から登録

`config.toml` の手書きブロックを削除し、README「ChatGPT desktop app」節の手順
（Settings → MCP servers → Add server / Authentication: Bearer token）で
**アプリ自身にトークンを保持させる**。WSL の環境変数問題を根本から回避できる。

- 利点: 転送経路に依存しない。最も確実
- 未確認: 現行ビルドに当該 UI が実在するか未検証

### B. `WSLENV` で Windows → WSL に転送 ← **これを採用**

Windows の User 環境変数として `WSLENV=OBSIDIAN_MCP_TOKEN/u` を新規作成し、アプリを再起動。

- 利点: トークンの保管場所が現状の 1 箇所のまま。追加ファイル不要
- **転送機構そのものは実測で確認済み**: `WSLENV=OBSIDIAN_MCP_TOKEN/u` を付けて
  `wsl.exe` を起動したところ、WSL 側に 64 文字で到達した
- リスク: §5.3 のとおりアプリが `WSLENV` を自前生成しているため**上書きされる可能性**。
  再起動後に `/proc/<新 pid>/environ` を読めば成否を即判定できる

### C. WSL 実行をやめる

`runCodexInWindowsSubsystemForLinux = false` にして Codex を Windows ネイティブで動かす。

- 利点: Windows の User 環境変数をそのまま読めるので確実
- 欠点: WSL 前提の他の作業（本リポジトリの開発を含む）に影響

## 8. 副次的な発見（接続とは独立）

1. **書き込み承認設定が欠落**（要対応）
   `[mcp_servers.obsidian_vault]` に `default_tools_approval_mode` と
   各 write ツールの `approval_mode` が無い。README:82 は
   「`readOnlyHint: false` はクライアント側ポリシーが無視できるシグナルであり、
   明示的な承認ポリシーを置くこと」を security invariant として要求している。
   現状では `create_inbox_note` / `append_inbox_note` が無確認で走り得る。
   `startup_timeout_sec` / `tool_timeout_sec` も未設定（README の例には有る）。

2. **WSL 側 `~/.codex/config.toml` に `obsidian_vault` が未登録**
   `[desktop] integratedTerminalShell = "wsl"` のため、統合ターミナルから
   `codex` CLI を直接使う場合はサーバーが見えない。WSL 側の Codex ログ
   （`CODEX_HOME=/home/<user>/.codex`、`codex-tui 0.146.0` が書き込み）を全走査した結果、
   `obsidian_vault` の出現は **0 件**。rmcp クライアントの活動は
   Codex 内蔵の `server_name=codex_apps` のみで、当該サーバーへの接続試行は一度もない。

3. **README:50 の記述誤り**
   「no session is tracked across requests, so terminating one (`DELETE`) is a no-op,
   not an error」とあるが、実際は **405**（`tests/test_mcp_protocol.py:559` も 405 を
   アサート）。405 は「セッション終了をサポートしないサーバー」として仕様準拠なので
   **挙動は正しく、README の文だけが誤り**。

4. **`serverInfo.version` が空文字列**
   `MCPServer(name=…, instructions=…)` に version を渡していない。
   `pyproject.toml` は `0.1.0`。実害は未確認。

## 9. 未解決事項

- **§8-1 の承認設定の欠落が未対応。** `[mcp_servers.obsidian_vault]` に
  `default_tools_approval_mode` と各 write ツールの `approval_mode` が無いまま。
  README:82 が security invariant として要求している項目であり、現状では
  `create_inbox_note` / `append_inbox_note` が無確認で走り得る。
- **Windows 側の Codex ログは未確認のまま。**
  `C:\Users\<user>\.codex\logs_2.sqlite` に 401 の実レコードが残っている可能性が
  高いが、権限制約で読めなかった。原因は §5.3 のプロセス環境の直読で確定できた
  ため追う必要はなくなったが、同種の切り分けでは有力な情報源になる。
- §8-3（README の `DELETE /mcp` 記述誤り）と §8-4（`serverInfo.version` が空）は
  別途対応。§8-3 は運用向けログ整備の変更に含めて修正済み。

## 10. 検証に使ったコマンド

```bash
# サーバー側ハンドシェイク（トークンは .env から読み、出力しない）
set -a && . ./.env && set +a
curl -sS -i -X POST https://obsidian-api.example.com/mcp \
  -H "Authorization: Bearer $API_TOKEN" \
  -H "Content-Type: application/json" \
  -H "Accept: application/json, text/event-stream" \
  -d '{"jsonrpc":"2.0","id":1,"method":"initialize","params":{
       "protocolVersion":"2025-06-18","capabilities":{},
       "clientInfo":{"name":"curl","version":"0"}}}'

# トークン照合（値は出さずハッシュのみ比較）
printf '%s' "$API_TOKEN" | sha256sum
powershell.exe -NoProfile -Command "
  \$t=[Environment]::GetEnvironmentVariable('OBSIDIAN_MCP_TOKEN','User')
  \$h=[Security.Cryptography.SHA256]::Create().ComputeHash(
       [Text.Encoding]::UTF8.GetBytes(\$t))
  -join(\$h|ForEach-Object{\$_.ToString('x2')})"

# 実プロセスの環境（決定的証拠）
ps -eo pid,lstart,cmd | grep [c]odex
tr '\0' '\n' < /proc/<PID>/environ | grep -E '^(CODEX_HOME|WSLENV|SHLVL|PATH)='
tr '\0' '\n' < /proc/<PID>/environ | grep -c '^OBSIDIAN_MCP_TOKEN='

# WSLENV 転送機構の実測
powershell.exe -NoProfile -Command "
  \$env:OBSIDIAN_MCP_TOKEN=[Environment]::GetEnvironmentVariable(
      'OBSIDIAN_MCP_TOKEN','User')
  \$env:WSLENV='OBSIDIAN_MCP_TOKEN/u'
  wsl.exe -e /bin/sh -c 'echo \${#OBSIDIAN_MCP_TOKEN}'"

# WSL 側 Codex ログの走査（読み取り専用で開く）
python3 -c "
import sqlite3
c=sqlite3.connect('file:/home/<user>/.codex/logs_2.sqlite?mode=ro', uri=True)
print(list(c.execute(
  'select count(*) from logs where feedback_log_body like \"%obsidian_vault%\"')))"
```

## 11. 教訓

- **エージェントの自己申告を証拠にしない。** 「configured but isn't currently ready」は
  ユーザーのプロンプトから復唱可能な情報であり、実際の設定読み込みを裏付けない。
- **設定ファイルの正しさとプロセス環境の正しさは別問題。** 設定・トークン・サーバーが
  すべて正しくても、トークンが「どのプロセスの環境に」入っているかで結果が変わる。
  `/proc/<pid>/environ` を読むまでは推測にすぎない。
- **WSL 越しのクライアントでは env 由来の認証情報が最初の疑い先。** シェルを介さない
  interop 起動では rc ファイルも `/etc/environment` も効かず、`WSLENV` だけが経路になる。
