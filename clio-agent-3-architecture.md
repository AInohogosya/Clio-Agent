# Clio Agent 3 Beta アーキテクチャ

*コードベース `/Users/sasakikousei/Desktop/Ethos`(Python パッケージ名 `ethos`、バージョン `3.0.0-beta.1`、リリース 2026-10-03 JST:`ethos/__init__.py`、`pyproject.toml`)から読み取った事実のみを記述する。*

## 1. 概要

Clio Agent 3 Beta は「常時存在し続ける汎用自律エージェント」である(`pyproject.toml:8`)。典型的なチャットボットとは異なり、プロンプト→応答の往復ではなく、**自分の時計で動き続けるライフループ**(`pace → perceive → attend → decide → act → record`)を持つ。ユーザーは Telegram / Slack / Discord / Email / WhatsApp / Web / CLI などの複数チャネルから非同期に話しかけ、エージェントは注意力評価(顕著度スコアリング)を経て応答・作業・静黙を自ら選ぶ。永続記憶は PostgreSQL 16 + pgvector に置かれ、夜間の記憶統合・自己モデル更新も自律的に行われる。

**ランタイムモデル**:単一プロセスループではない。`ethos up`(`ethos/core/bootstrap.py:428`)はスーパーバイザー配下の**7 プロセス構成**(gateway / toolhost / comms / worker×2 / core / 任意の web)を起動する(`ethos/core/supervisor.py:163`)。中核は `ethos-core` プロセス内のティックベースのライフループ(デフォルト 4 秒 ±15%、`ethos/engine/rhythm.py:184`)で、プロセス間の連携は Postgres の `events` テーブル + `LISTEN/NOTIFY` によるトランザクショナルイベントバス(`ethos/bus/event_bus.py:40`)で行う。ハイブリッド型:知覚はイベント駆動で届くが、処理は各ティックの先頭で排水される。

## 2. コンポーネント

| モジュール | 責務 | 主要ファイル |
|---|---|---|
| `ethos.core` | 起動・スーパーバイザー・制御面・スケジューラ | `bootstrap.py`, `supervisor.py`, `runner.py`, `control.py`, `scheduler.py` |
| `ethos.comms` | チャネルアダプター(Telegram/Slack/Discord/Email/WhatsApp/Web/CLI/Webhook) | `host.py`, `adapters/base.py` ほか各アダプター |
| `ethos.bus` | トランザクショナルイベントバス(Postgres LISTEN/NOTIFY) | `bus/event_bus.py` |
| `ethos.engine` | ライフループ・注意力・状態機械・リズム・作業区画・リヴェリ・ドライブ | `life.py`, `attention.py`, `states.py`, `rhythm.py`, `workspace.py`, `reverie.py`, `drives.py` |
| `ethos.social` | 応答審議・礼儀(送出パイプライン)・会話状態 | `deliberation.py`, `etiquette.py`, `conversation.py` |
| `ethos.gateway` | モデルルーティング・プロバイダー正規化・コスト・レート制限・埋め込み | `service.py`, `router.py`, `normalizer.py`, `providers/`, `cost.py`, `embeddings.py`, `secrets.py` |
| `ethos.gsl` | 目標→計画→実行→検証の一般解決ループ | `loop.py`, `planner.py`, `evaluator.py`, `acceptance.py`, `verification.py` |
| `ethos.toolhost` | ツール実行ホスト(shell/fs/code/http/browser/desktop/pty/watcher など約 50 ツール) | `server.py`, `kernels.py`, `pty_session.py`, `browser.py`, `tools/` |
| `ethos.guardian` | 安全=可逆性。アクションジャーナル・ゲート・ゴミ箱・スナップショット・監査 | `gate.py`, `journal.py`, `trash.py`, `snapshots.py`, `registry.py`, `classifier.py` |
| `ethos.memory` | エピソード/ファクト/ノウハウ/クエスチョンの記憶・検索・統合・文脈層 | `store.py`, `retrieval.py`, `context.py`, `consolidate.py`, `continuity.py`, `self_model.py` |
| `ethos.threads` / `ethos.subagents` | 委譲スレッドと外部サブエージェント(Foundry/APEX) | `threads/manager.py`, `threads/worker.py`, `subagents/{base,bridge,foundry,apex,tools}.py` |
| `ethos.observability` / `ethos.prompts` | ログ・監査ハッシュ鎖・Prometheus / プロンプトテンプレート | `observability/*.py`, `prompts/*.py` |
| `interface/` | TypeScript 製 UI キット 3 パッケージ(`@project-phone/core|web|cli`) | `packages/core/src/`(特に `agent/`, `duplex-client.ts`) |
| `web/` | Node 製インターフェースブリッジ(HTTP/WS ⇄ エージェントの Postgres) | `web/server/index.mjs` |

**依存関係(概略)**:

```
interface(web/cli) ──HTTP/WS──> web/server (bridge) ──SQL/events──> Postgres
Telegram 等チャネル ──> ethos-comms ──> events テーブル ──NOTIFY──> ethos-core (Life)
ethos-core ──> engine/social/gsl ──> gateway (in-process または unix socket 8700)
            └─> toolhost (in-process または socket/TCP 8701) ──> guardian ──> Postgres/ファイルシステム
ethos-workers ──> thread.request イベント ──> threads.worker ──> gateway/toolhost
subagents (Foundry/APEX) ──OpenAI 互換シム(gateway/compat.py)──> gateway
```

## 3. コアフロー:ユーザーメッセージ一件の追跡

Telegram 到着を例にとる(ソケットは `deploy/systemd/` と `config/ethos.yaml` 参照)。

1. **受信**:`TelegramAdapter.listen()` が long poll し、許可リスト判定の上 `Message` を組み立て `on_inbound` を呼ぶ(`ethos/comms/adapters/telegram.py:218,169`)。
2. **取り込み**:`inbound_to_bus()` のハンドラー(`ethos/comms/host.py:733`)が `SocialCognition.record_inbound()` で `messages` テーブルに INSERT し(`ethos/social/deliberation.py:840`)、`EventBus.publish(EventKind.INBOUND_MSG, ...)`(`event_bus.py:171`——INSERT と `pg_notify` が同一トランザクション)。
3. **知覚**:`ethos-core` のワイルドカード購読 `CoreRuntime._wire_bus().on_event`(`ethos/core/runner.py:203`)が `percept_from_event`(`ethos/schemas/events.py:79`)で `Percept` 化し `Life.percept_queue` へ。
4. **ティック**:`Life.life()` の while ループ(`ethos/engine/life.py:1126`)が `_cycle()`(`:1187`)を回す。`Attention.appraise()`(`ethos/engine/attention.py:145`)が T0 ルールで顕著度を算出し、中間帯は T1 モデル呼び出し `refine_salience()` で補正、閾値を超えれば現フォーカスを `bookmark()` して割り込む。
5. **審議**:`Life.decide()`(`life.py:390`)が `SocialCognition.pending_inbound()`(`deliberation.py:759`)で未回答メッセージを取り、`_message_attention_verdict()` で割り込み判定。勝てば `SocialCognition.deliberate()`(`deliberation.py:258`)へ:T0 無視ルール → プロンプト組み立て `social_deliberation_messages()`(`ethos/prompts/deliberation.py:231`)→ `_ask()` が `ModelRequest` を組み `gateway.complete(request)`(`deliberation.py:610`)。ゲートウェイは `GatewayService.complete()`(`ethos/gateway/service.py:627`)で予算検査(`SpendGuard`)→ `ModelRouter.select()`(品質−μ·コスト−ν·レイテンシ、`router.py:138`)→ `provider.complete()`(Anthropic/OpenAI/Google/OpenAI 互換の各 HTTP 呼び出し)。
6. **解析**:応答 JSON を `_parse_json()` と多段リカバリー(切り詰め・散文・誤キー)で `SocialDecision` にする(`deliberation.py:950+`)。解釈不能なら `_undecided()`——何も捏造しない。動作中を装う返信は `_withhold_unearned_progress()` で剥がされる。
7. **実行**:`Life.act()` → `_act_social()`(`life.py:596`)。`reply_now` なら各テキストを `comms_out.send()`(`life.py:709`)= `OutboundPipeline.send()`(`ethos/social/etiquette.py:146`)へ。`in_reply_to` 付き返信はすべてのゲート(静寂時間・割り込み予算)をバイパスし `_deliver()` → `CommsHost.send()` → `TelegramAdapter.send()`。意思表明を含むなら `memory.create_intention()` でコミット化。
8. **記録**:`Life.record()`(`life.py:969`)が `cycles` 行 INSERT、`ContinuityManager.save()` で `~/.ethos/data/continuity.json` を原子的更新 + `checkpoints` 行、presence/budget イベント発行。`mark_handled()`(`deliberation.py:798`)でメッセージを完結扱いに。最後に `rhythm.wait_until_next(state)` で次ティックまで sleep。

**ツール呼び出しの決定・実行・還元**(自発的な目標達成時、GSL 経路):

- `Life.decide()` が `advance_goal` を選ぶと `GeneralSolverLoop.advance()`(`ethos/gsl/loop.py:298`)が `intention.resume_state`(JSONB)を状態機械として進める:`understand` → `devise`(3 案)→ `pick_approach` → `make_plan`(3–7 ステップ、`gsl/planner.py`)。
- 各ステップ `_execute_step()`(`loop.py:555`)は `STEP_PROMPT`(`prompts/gsl.py:272`)でモデルに **JSON のアクションリスト**(`{tool, args}` 最大 6 件)を要求。ツールカタログはワークスペースの安定キャッシュ前置ブロックに置かれる(`ethos/engine/workspace.py:13,27`)。
- 各アクションに冪等キー `f"{intention_id}:{strategy}:{step}:{n}"` 付きの `ToolContext` を組み `Toolhost.execute()`(`ethos/toolhost/server.py:189`)へ:不明ツール→ NOT_FOUND → `ActionJournal.begin`(先行書き込みジャーナル、`guardian/journal.py:82`)→ `GuardianGate.review()`(`guardian/gate.py:143`;H1 停止確認、H3/H6 危険パターン、H2 金額、コミッション成果物への削除は「デュープロセス」検証の上 `fs.read` へ変換)→ タイムアウト付き実行 → `journal.end`。拒否は `error_code=guardian_reject`。
- 結果は `SolverResult.actions` に載り、`resume_state.artifacts`(上限 20)と `ContextLayer.note_actions()`(`ethos/memory/context.py:144`)経由で次サイクルの文脈に還元される。ステップ失敗 2 回で戦略切替、3 戦略で park(`loop.py:418-449`)。検証は `ObjectiveChecker`(機械的チェック、`gsl/evaluator.py:17`)→ 独立検証者(別系統のモデル、`gsl/verification.py:329`)→ 人間レビューの三段(`verification.py:81`)。
- なおネイティブ関数呼び出し(`ModelRequest.tools` → `NormalizedResponse.tool_calls`)もゲートウェイ層で完全実装されており、Foundry/APEX はこちらを OpenAI 互換シム経由で使う(`gateway/compat.py`)。内部の Self ループは現在 JSON 契約経路を用いる。

## 4. 状態とデータ

- **会話・記憶**:PostgreSQL 16 + pgvector + pg_trgm(`migrations/versions/0001_initial_schema.py` の 20 テーブル:`events`, `episodes`, `facts`, `knowhow`, `questions`, `intentions`, `decisions`, `attempts`, `env_entities`, `self_model`, `people`, `checkpoints`, `cycles`, `model_calls`, `action_journal`, `artifacts`, `messages`, `audit_log`, `experiments`, `value_ledger`)。埋め込みは 1024 次元 `BAAI/bge-large-en-v1.5`(fastembed、`gateway/embeddings.py:112`)。検索はハイブリッド:コサイン .40 + BM25 .15 + 新近性 .15 + 重要度 .20 + 強度 .10、MMR 再順位(`memory/retrieval.py:70,114`)。
- **作業記憶**:`ContextLayer`(`memory/context.py:54`)——ローリングの `ContextTurn` バッファが溢れると T1 モデルで一人称 `NarrativeLine` に蒸留、外に出た行はエピソードとして長期記憶へ。
- **継続性**:毎サイクル `continuity.json`(tmp+fsync+rename の原子的書き込み)+ `checkpoints` 行。再起動時 `ContinuityManager.wake_up()`(`memory/continuity.py:63`)が走り中のジャーナル行を `interrupted` に、フォーカスと文脈を復元。
- **GSL 状態**:`intentions.resume_state`(JSONB)に計画・ブックマーク・部分成果が永続化され、プロセス再起動を跨ぐ。
- **設定**:`config/*.yaml` 11 ファイルを `load_config()`(`ethos/config.py:1596`)が統合。`~/.ethos/` 配下の `channels.yaml`/`people.yaml`/`base_model.yaml`/`identity.yaml` は上書きマージ。メッセージ形式は `ethos/schemas/messages.py` の `Message`/`SocialDecision`、イベントは `schemas/events.py`。
- **その他の永続化**:アーカイブは月次 Parquet(`memory/store.py:287`)、ゴミ箱は `~/.trash/<id>/manifest.json`(`guardian/trash.py:80`)、監査ログはハッシュ鎖(`observability/audit.py:33`)。**暗号化-at-rest は存在しない**。シークレットはエージェントには `{{secret:KEY}}` ハンドルのみ渡し、実値は許可ホストへの HTTP 時のみ `SecretVault.substitute()` で展開(`gateway/secrets.py:109`)。

## 5. 拡張ポイント

- **ツール**:`Tool` ABC(`toolhost/server.py:45`)を継承し、`_register_builtin_tools()`(`server.py:103`)に 1 行追加するだけ。カタログとプロンプトへ自動反映。実行時登録は toolshed(`tools/toolshed.py`——`~/toolshed/<name>/tool.yaml` + stdin/stdout JSON サブプロセス)。
- **プロバイダー**:`Provider` ABC(`gateway/providers/base.py:103`)実装 + `normalizer.py` に変換対 + `PROVIDER_CLASSES`(`service.py:40`)登録 + `models.yaml`/`quirks.yaml`/`pricing.yaml` へのエントリ。
- **チャネル**:`ChannelAdapter`(`comms/adapters/base.py:93`)実装(start/listen/send)を `CommsHost.plan()` に繋ぎ `channels.yaml` に資格情報。
- **サブエージェント**:`SubAgent`(`subagents/base.py:52`)継承、`SubagentRegistry._load`(`subagents/__init__.py:41`)へ追加、`ethos.yaml` の `subagents:` ブロック、必要なら `subagents/tools.py` に委譲ツール。外部エージェントは OpenAI 互換シム(`gateway/compat.py:166`)でゲートウェイを下流にできる。
- **UI**:インターフェース(`@project-phone/*`)はエージェントの Postgres を HTTP/WS に翻訳するだけなので、エージェント側に手を入れずに置き換え可能。逆にブリッジ(`web/server/index.mjs`)は stateless でエージェントのテーブル以外に状態を持たない。

## 6. 規約と制約

- **言語/ランタイム**:Python ≥3.12(`pyproject.toml:9`)。主要依存:pydantic ≥2.7、asyncpg ≥0.29、httpx ≥0.27、anthropic ≥0.34、openai ≥1.35、google-genai ≥0.3、fastembed ≥0.3、playwright ≥1.44、bashlex ≥0.18、pyrage ≥1.1、structlog ≥24.1、fastapi ≥0.111、uvicorn ≥0.30、typer ≥0.12、python-telegram-bot ≥21.1、numpy ≥1.26、pyarrow ≥16.0、prometheus-client ≥0.20、OpenTelemetry ≥1.25。PostgreSQL 16 + pgvector が「唯一のハード依存」(`.env.example`)。UI/ブリッジ側:Node ≥20、TypeScript 5.7、React 18、Vite 6、Ink 5、express 4、ws 8、pg 8(`interface/*/package.json`, `web/package.json`)。
- **エラー処理**:ライフループの 1 サイクルでの例外は記録のみで致命扱いしない(`life.py:1147-1162`)。ゲートウェイは `ProviderError.scope`(`providers/base.py:13`)に応じ再試行レバー(リクエスト拡張/モデル切替/時間退避)を使い分け、サーキットブレーカー(`router.py:50`)とフェイルオーバーを備える。
- **ログ/観測**:structlog JSON、シークレットの正規表現マスキング(`observability/logger.py:24`)。Prometheus メトリクス(目的別モデル呼び出しカウンタ等、`metrics.py`)、ハッシュ鎖監査ログ。
- **テスト**:pytest(asyncio_mode=auto、タイムアウト 120s)。`tests/unit`(29 ファイル、DB 不要)、`tests/integration`(要 Postgres、`integration` マーカー)、`tests/scenarios`(E2E)。JS 側は `node --test`。`make test*`、`make lint`(ruff、line-length 110)。`ethos doctor` は実ファイル到達範囲を実測(`paths.py:199`)。
- **安全モデル**:サンドボックスなし。「安全は拒否ではなく可逆性で」——H1 監督優越〜H6 認証スコープの 6 つのハードリミット(`prompts/identity.py:484`)、先行ジャーナル、ゴミ箱化、スナップショット、コミッション成果物へのデュープロセス。帯域外監視として `deploy/oob/oob_guardian.py`(ハイパーバイザー側、$20/日・$400/月の支出監視と kill switch)。

## 7. 未解決の疑問(コードからは確定できない点)

- **命名の不一致**:製品名は Clio Agent 3 Beta だが、UI パッケージ名は `@project-phone/core|web|cli`、CLI バイナリは `phone`(`interface/packages/cli/package.json`)。`project-phone` という名前が README の他の箇所では説明されておらず、移行途中の名前と思われるが根拠はコードにない。
- **ロックファイル**:Python 側に `uv.lock` 等のロックファイルがなく、依存は `pyproject.toml` の範囲指定のみ。実際に解決される版数は環境依存。
- **ネイティブ関数呼び出しの二重経路**:内部 Self ループは JSON アクション契約、外部サブエージェントはネイティブ tool_calls。どちらへ統一する意向(あるかどうか)はコード上に明言がない。
- **`ethos-core` の `--listen-channels`**:README と `ethos/__main__.py:347` に dev 用フラグが存在するが、7 プロセス運用では comms 側が listen を独占する設計で、両者の併用時の重複受信の扱いはドキュメント化されていない。
- **ライセンス**:README・`pyproject.toml` いずれにもライセンス表記が見当たらない。
- **サブモジュールの独立性**:`APEX/`(`revision-aware-hybrid-desktop-controller`、`APEX/pyproject.toml:6`)と `Foundry/`(`foundry-factory`、`Foundry/pyproject.toml:6`)は `.gitmodules` 宣言の別リポジトリ。本ドキュメントの記述は `ethos/subagents/` のブリッジコードと各ディレクトリ内のドキュメントから得たもので、サブモジュール内部の動作までは検証していない。
