// Clio Agent configuration schema: every setting a person may change from the
// settings screens (Web + TUI), grouped by category OS-style.
// Field ids are stable wire names; `file` + `path` locate the YAML cell.

export type AgentConfigCategory =
  | 'general'
  | 'connection'
  | 'model'
  | 'budget'
  | 'deliberation'
  | 'solver'
  | 'rhythm'
  | 'memory'
  | 'permissions'
  | 'messaging'
  | 'appearance';

export const AGENT_CONFIG_CATEGORIES: AgentConfigCategory[] = [
  'general',
  'connection',
  'model',
  'budget',
  'deliberation',
  'solver',
  'rhythm',
  'memory',
  'permissions',
  'messaging',
  'appearance',
];

/**
 * The tuning categories — budget, deliberation, solver, rhythm, memory,
 * permissions — whose panes are wall-to-wall knobs. Both settings screens keep
 * them behind an "Advanced settings" section so the everyday categories stay
 * at the top, and direct mode hides them outright.
 */
export const ADVANCED_AGENT_CONFIG_CATEGORIES: AgentConfigCategory[] = [
  'budget',
  'deliberation',
  'solver',
  'rhythm',
  'memory',
  'permissions',
];

export function isAdvancedAgentConfigCategory(category: AgentConfigCategory): boolean {
  return (ADVANCED_AGENT_CONFIG_CATEGORIES as string[]).includes(category);
}

export type AgentConfigValueType = 'text' | 'number' | 'boolean' | 'select';

export interface AgentConfigField {
  id: string;
  category: AgentConfigCategory;
  /** YAML file in config/ */
  file: string;
  /** Dotted yaml path inside the file, e.g. "hard_limits.H2_daily_spend_usd" */
  path: string;
  label: string;
  type: AgentConfigValueType;
  /** For nullable numbers: empty means disabled (null). */
  nullable?: boolean;
  min?: number;
  max?: number;
  options?: string[];
  placeholder?: string;
  hint?: string;
}

const FIELD_LABEL_JA: Record<string, string> = {
  'general.self_name': 'エージェントの名前',
  'general.timezone': 'タイムゾーン',
  'gateway.host': 'ゲートウェイのホスト',
  'gateway.port': 'ゲートウェイのポート',
  'gateway.toolhost_port': 'ツールホストのポート',
  'workers.count': 'ワーカー数',
  'threads.max_concurrent': 'スレッド: 最大同時実行',
  'threads.max_depth': 'スレッド: 最大深度',
  'threads.mode': 'スレッドのモード',
  'hard_limits.budget_unit': '予算の単位',
  'hard_limits.H2_per_minute_limit': '1分あたりの予算',
  'hard_limits.H2_per_10_minutes_limit': '10分あたりの予算',
  'hard_limits.H2_per_hour_limit': '1時間あたりの予算',
  'hard_limits.H2_per_5_hours_limit': '5時間あたりの予算',
  'hard_limits.H2_daily_spend_usd': '1日あたりの予算',
  'hard_limits.H2_monthly_spend_usd': '月次予算',
  'hard_limits.on_exceed': '予算超過時の動作',
  'hard_limits.H2_capital_at_risk_usd': 'リスクにさらす資金 (USD)',
  'hard_limits.money_threshold_usd': '金額の閾値 (USD)',
  'gateway.routing.mu': 'ルーティング mu',
  'gateway.routing.nu': 'ルーティング nu',
  'gateway.circuit_breaker.max_failures': 'サーキットブレーカー: 最大失敗数',
  'gateway.circuit_breaker.cooldown_s': 'サーキットブレーカー: クールダウン (秒)',
  'gateway.retry.max_retries': 'リトライ: 最大回数',
  'gateway.retry.backoff_s': 'リトライ: バックオフ (秒)',
  'gateway.retry.max_backoff_s': 'リトライ: 最大バックオフ (秒)',
  'gateway.retry.fail_over': 'リトライ: 別モデルへ切り替え',
  'social.deliberation_max_tokens': '熟慮の最大トークン',
  'social.deliberation_escalated_max_tokens': '熟慮エスカレーション時の最大トークン',
  'social.execution_state_window_min': '実行状態の保持期間 (分)',
  'social.execution_state_max_actions': '実行状態の最大アクション数',
  'social.max_deliberation_attempts': '熟慮の最大試行回数 (0 = 無制限)',
  'social.proactive_enabled': '能動的な報告',
  'social.proactive_progress_interval_min': '進捗報告の間隔 (分)',
  'gsl.understand_tokens': '理解のトークン',
  'gsl.devise_tokens': '考案のトークン',
  'gsl.plan_tokens': '計画のトークン',
  'gsl.step_tokens': '手順のトークン',
  'gsl.reflect_tokens': '振り返りのトークン',
  'gsl.degraded_reasoning_budget': '劣化時の推論予算',
  'gsl.reasoning_budget': '推論予算',
  'gsl.model_hint': 'モデルヒント',
  'gsl.plan_attempts_before_degrade': '劣化までの計画試行回数',
  'gsl.plan_attempts_before_park': '休止までの計画試行回数 (0 = なし)',
  'gsl.plan_backoff_s': '計画のバックオフ (秒)',
  'gsl.plan_backoff_max_s': '計画の最大バックオフ (秒)',
  'gsl.max_strategies': '最大戦略数 (0 = 無制限)',
  'gsl.verify_code': 'コードの検証',
  'gsl.verify_step_checks': '手順チェックの検証',
  'gsl.unverified_is_failure': '未検証は失敗扱い',
  'rhythm.energy.night_floor': 'エネルギー: 夜間の下限',
  'rhythm.energy.peak_hour': 'エネルギー: ピーク時間',
  'rhythm.energy.low_energy': 'エネルギー: 低い状態',
  'rhythm.focus.max_session_min': '集中: 最大セッション (分)',
  'rhythm.focus.micro_break_min': '集中: 小休止 (分)',
  'rhythm.reverie.enabled': '夢想を有効化',
  'rhythm.reverie.max_tokens': '夢想の最大トークン',
  'rhythm.reverie.min_gap_min': '夢想の最小間隔 (分)',
  'rhythm.reverie.max_gap_min': '夢想の最大間隔 (分)',
  'memory.retrieval.top_k': '検索の上位件数',
  'memory.retrieval.candidate_k': '検索の候補件数',
  'memory.retrieval.mmr_lambda': '検索の MMR ラムダ',
  'memory.retrieval.recency_half_life_h': '最新性の半減期 (時間)',
  'memory.context.max_turns': '文脈の最大ターン数',
  'memory.context.max_chars': '文脈の最大文字数',
  'memory.context.narrate_every': '物語化の間隔 (ターン)',
  'memory.decay.demote_strength': '減衰: 降格の強さ',
  'memory.decay.archive_after_days': 'アーカイブまでの日数',
  'permissions.safety_level': '安全レベル',
  'permissions.sudo_allowed': 'sudo を許可',
  'permissions.approval_timeout_s': '承認タイムアウト (秒)',
  'permissions.guardian.mode': '守護者のモード',
  'permissions.network.deny_private_ranges': 'ネットワーク: プライベート範囲を拒否',
  'permissions.network.max_response_bytes': 'ネットワーク: 最大応答バイト',
  'permissions.interruption.daily_budgets.owner': '割り込み予算: 所有者/日',
  'permissions.interruption.digest_window_min': 'ダイジェスト枠 (分)',
  'clio.subagents.tier': 'サブエージェントの階層',
  'clio.subagents.model_name': 'サブエージェントのモデル名',
};

const FIELD_LABEL_ZH: Record<string, string> = {
  'general.self_name': '智能体名称',
  'general.timezone': '时区',
  'gateway.host': '网关主机',
  'gateway.port': '网关端口',
  'gateway.toolhost_port': '工具主机端口',
  'workers.count': '工作线程数',
  'threads.max_concurrent': '线程：最大并发',
  'threads.max_depth': '线程：最大深度',
  'threads.mode': '线程模式',
  'hard_limits.budget_unit': '预算单位',
  'hard_limits.H2_per_minute_limit': '每分钟预算',
  'hard_limits.H2_per_10_minutes_limit': '每 10 分钟预算',
  'hard_limits.H2_per_hour_limit': '每小时预算',
  'hard_limits.H2_per_5_hours_limit': '每 5 小时预算',
  'hard_limits.H2_daily_spend_usd': '每日预算',
  'hard_limits.H2_monthly_spend_usd': '月度预算',
  'hard_limits.on_exceed': '超出预算时的处理',
  'hard_limits.H2_capital_at_risk_usd': '风险资金 (美元)',
  'hard_limits.money_threshold_usd': '金额阈值 (美元)',
  'gateway.routing.mu': '路由 mu',
  'gateway.routing.nu': '路由 nu',
  'gateway.circuit_breaker.max_failures': '断路器：最大失败数',
  'gateway.circuit_breaker.cooldown_s': '断路器：冷却时间 (秒)',
  'gateway.retry.max_retries': '重试：最大次数',
  'gateway.retry.backoff_s': '重试：退避时间 (秒)',
  'gateway.retry.max_backoff_s': '重试：最大退避 (秒)',
  'gateway.retry.fail_over': '重试：切换到另一个模型',
  'social.deliberation_max_tokens': '审议最大令牌',
  'social.deliberation_escalated_max_tokens': '审议升级时最大令牌',
  'social.execution_state_window_min': '执行状态窗口 (分钟)',
  'social.execution_state_max_actions': '执行状态最大动作数',
  'social.max_deliberation_attempts': '审议最大尝试次数 (0 = 无限制)',
  'social.proactive_enabled': '主动汇报',
  'social.proactive_progress_interval_min': '进展汇报间隔 (分钟)',
  'gsl.understand_tokens': '理解令牌',
  'gsl.devise_tokens': '构思令牌',
  'gsl.plan_tokens': '计划令牌',
  'gsl.step_tokens': '步骤令牌',
  'gsl.reflect_tokens': '反思令牌',
  'gsl.degraded_reasoning_budget': '降级推理预算',
  'gsl.reasoning_budget': '推理预算',
  'gsl.model_hint': '模型提示',
  'gsl.plan_attempts_before_degrade': '降级前的计划尝试次数',
  'gsl.plan_attempts_before_park': '挂起前的计划尝试次数 (0 = 永不)',
  'gsl.plan_backoff_s': '计划退避 (秒)',
  'gsl.plan_backoff_max_s': '计划最大退避 (秒)',
  'gsl.max_strategies': '最大策略数 (0 = 无限制)',
  'gsl.verify_code': '校验代码',
  'gsl.verify_step_checks': '校验步骤检查',
  'gsl.unverified_is_failure': '未校验视为失败',
  'rhythm.energy.night_floor': '能量：夜间下限',
  'rhythm.energy.peak_hour': '能量：高峰时段',
  'rhythm.energy.low_energy': '能量：低能量水平',
  'rhythm.focus.max_session_min': '专注：最大会话 (分钟)',
  'rhythm.focus.micro_break_min': '专注：微休息 (分钟)',
  'rhythm.reverie.enabled': '启用遐思',
  'rhythm.reverie.max_tokens': '遐思最大令牌',
  'rhythm.reverie.min_gap_min': '遐思最小间隔 (分钟)',
  'rhythm.reverie.max_gap_min': '遐思最大间隔 (分钟)',
  'memory.retrieval.top_k': '检索 Top-K',
  'memory.retrieval.candidate_k': '检索候选 K',
  'memory.retrieval.mmr_lambda': '检索 MMR lambda',
  'memory.retrieval.recency_half_life_h': '时效半衰期 (小时)',
  'memory.context.max_turns': '上下文最大轮次',
  'memory.context.max_chars': '上下文最大字符数',
  'memory.context.narrate_every': '每几轮叙述一次',
  'memory.decay.demote_strength': '衰减：降级强度',
  'memory.decay.archive_after_days': '归档周期 (天)',
  'permissions.safety_level': '安全等级',
  'permissions.sudo_allowed': '允许 sudo',
  'permissions.approval_timeout_s': '审批超时 (秒)',
  'permissions.guardian.mode': '守护模式',
  'permissions.network.deny_private_ranges': '网络：拒绝私有地址段',
  'permissions.network.max_response_bytes': '网络：最大响应字节',
  'permissions.interruption.daily_budgets.owner': '打断预算：所有者/天',
  'permissions.interruption.digest_window_min': '摘要窗口 (分钟)',
  'clio.subagents.tier': '子智能体等级',
  'clio.subagents.model_name': '子智能体模型名',
};

const FIELD_HINT_JA: Record<string, string> = {
  'hard_limits.budget_unit': 'usd = ドルでのコスト · tokens = トークン数',
  'hard_limits.on_exceed': 'warn = 記録のみ · block = 呼び出しを拒否',
  'permissions.safety_level': '1 対話のみ · 2 全承認 · 3 インターネット承認 · 4 自動',
};

const FIELD_HINT_ZH: Record<string, string> = {
  'hard_limits.budget_unit': 'usd = 以美元计的成本 · tokens = 令牌数',
  'hard_limits.on_exceed': 'warn = 仅记录 · block = 拒绝调用',
  'permissions.safety_level': '1 仅对话 · 2 全部审批 · 3 审批联网 · 4 自动',
};

const FIELD_PLACEHOLDER_JA: Record<string, string> = {
  'gsl.model_hint': '(ルーターが選択)',
  'hard_limits.H2_per_minute_limit': 'オフ',
  'hard_limits.H2_per_10_minutes_limit': 'オフ',
  'hard_limits.H2_per_hour_limit': 'オフ',
  'hard_limits.H2_per_5_hours_limit': 'オフ',
  'hard_limits.H2_daily_spend_usd': 'オフ',
  'hard_limits.H2_monthly_spend_usd': 'オフ',
};

const FIELD_PLACEHOLDER_ZH: Record<string, string> = {
  'gsl.model_hint': '(由路由器选择)',
  'hard_limits.H2_per_minute_limit': '关',
  'hard_limits.H2_per_10_minutes_limit': '关',
  'hard_limits.H2_per_hour_limit': '关',
  'hard_limits.H2_per_5_hours_limit': '关',
  'hard_limits.H2_daily_spend_usd': '关',
  'hard_limits.H2_monthly_spend_usd': '关',
};

export function fieldLabel(field: AgentConfigField, language: string): string {
  if (language === 'ja') return FIELD_LABEL_JA[field.id] ?? field.label;
  if (language === 'zh') return FIELD_LABEL_ZH[field.id] ?? field.label;
  return field.label;
}

export function fieldHint(field: AgentConfigField, language: string): string | undefined {
  if (language === 'ja') return FIELD_HINT_JA[field.id] ?? field.hint;
  if (language === 'zh') return FIELD_HINT_ZH[field.id] ?? field.hint;
  return field.hint;
}

export function fieldPlaceholder(field: AgentConfigField, language: string): string | undefined {
  if (language === 'ja') return FIELD_PLACEHOLDER_JA[field.id] ?? field.placeholder;
  if (language === 'zh') return FIELD_PLACEHOLDER_ZH[field.id] ?? field.placeholder;
  return field.placeholder;
}

export const AGENT_CONFIG_FIELDS: AgentConfigField[] = [
  // General / Hero
  { id: 'general.self_name', category: 'general', file: 'clio.yaml', path: 'self_name', label: 'Agent name', type: 'text', placeholder: 'Clio Agent 3.1' },
  { id: 'general.timezone', category: 'general', file: 'clio.yaml', path: 'timezone', label: 'Timezone', type: 'select', options: ['local', 'UTC', 'Asia/Tokyo', 'America/Los_Angeles', 'Europe/London'] },

  // Daemon / workers+threads+gateway (under Connection? keep as Connection for simplicity, but also general)
  { id: 'gateway.host', category: 'connection', file: 'clio.yaml', path: 'gateway.host', label: 'Gateway host', type: 'text' },
  { id: 'gateway.port', category: 'connection', file: 'clio.yaml', path: 'gateway.port', label: 'Gateway port', type: 'number', min: 1, max: 65535 },
  { id: 'gateway.toolhost_port', category: 'connection', file: 'clio.yaml', path: 'gateway.toolhost_port', label: 'Toolhost port', type: 'number', min: 1, max: 65535 },
  { id: 'workers.count', category: 'connection', file: 'clio.yaml', path: 'workers.count', label: 'Worker count', type: 'number', min: 1, max: 32 },
  { id: 'threads.max_concurrent', category: 'connection', file: 'clio.yaml', path: 'threads.max_concurrent', label: 'Threads: max concurrent', type: 'number', min: 1, max: 64 },
  { id: 'threads.max_depth', category: 'connection', file: 'clio.yaml', path: 'threads.max_depth', label: 'Threads: max depth', type: 'number', min: 1, max: 16 },
  { id: 'threads.mode', category: 'connection', file: 'clio.yaml', path: 'threads.mode', label: 'Threads mode', type: 'select', options: ['inprocess', 'subprocess'] },

  // Token Budget & Spend — the headline ask
  { id: 'hard_limits.budget_unit', category: 'budget', file: 'permissions.yaml', path: 'hard_limits.budget_unit', label: 'Budget unit', type: 'select', options: ['usd', 'tokens'], hint: 'usd = dollars, tokens = token count' },
  { id: 'hard_limits.H2_per_minute_limit', category: 'budget', file: 'permissions.yaml', path: 'hard_limits.H2_per_minute_limit', label: 'Per-minute budget', type: 'number', nullable: true, min: 0, placeholder: 'off' },
  { id: 'hard_limits.H2_per_10_minutes_limit', category: 'budget', file: 'permissions.yaml', path: 'hard_limits.H2_per_10_minutes_limit', label: 'Per-10-minutes budget', type: 'number', nullable: true, min: 0, placeholder: 'off' },
  { id: 'hard_limits.H2_per_hour_limit', category: 'budget', file: 'permissions.yaml', path: 'hard_limits.H2_per_hour_limit', label: 'Per-hour budget', type: 'number', nullable: true, min: 0, placeholder: 'off' },
  { id: 'hard_limits.H2_per_5_hours_limit', category: 'budget', file: 'permissions.yaml', path: 'hard_limits.H2_per_5_hours_limit', label: 'Per-5-hours budget', type: 'number', nullable: true, min: 0, placeholder: 'off' },
  { id: 'hard_limits.H2_daily_spend_usd', category: 'budget', file: 'permissions.yaml', path: 'hard_limits.H2_daily_spend_usd', label: 'Daily budget', type: 'number', nullable: true, min: 0, placeholder: 'off' },
  { id: 'hard_limits.H2_monthly_spend_usd', category: 'budget', file: 'permissions.yaml', path: 'hard_limits.H2_monthly_spend_usd', label: 'Monthly budget', type: 'number', nullable: true, min: 0, placeholder: 'off' },
  { id: 'hard_limits.on_exceed', category: 'budget', file: 'permissions.yaml', path: 'hard_limits.on_exceed', label: 'On budget exceed', type: 'select', options: ['warn', 'block'], hint: 'warn = log only, block = refuse calls' },
  { id: 'hard_limits.H2_capital_at_risk_usd', category: 'budget', file: 'permissions.yaml', path: 'hard_limits.H2_capital_at_risk_usd', label: 'Capital at risk (USD)', type: 'number', min: 0 },
  { id: 'hard_limits.money_threshold_usd', category: 'budget', file: 'permissions.yaml', path: 'hard_limits.money_threshold_usd', label: 'Money threshold (USD)', type: 'number', min: 0 },
  { id: 'gateway.routing.mu', category: 'budget', file: 'clio.yaml', path: 'gateway.routing.mu', label: 'Routing mu', type: 'number', min: 0 },
  { id: 'gateway.routing.nu', category: 'budget', file: 'clio.yaml', path: 'gateway.routing.nu', label: 'Routing nu', type: 'number', min: 0 },
  { id: 'gateway.circuit_breaker.max_failures', category: 'budget', file: 'clio.yaml', path: 'gateway.circuit_breaker.max_failures', label: 'Circuit breaker: max failures', type: 'number', min: 1 },
  { id: 'gateway.circuit_breaker.cooldown_s', category: 'budget', file: 'clio.yaml', path: 'gateway.circuit_breaker.cooldown_s', label: 'Circuit breaker: cooldown (s)', type: 'number', min: 0 },
  { id: 'gateway.retry.max_retries', category: 'budget', file: 'clio.yaml', path: 'gateway.retry.max_retries', label: 'Retry: max retries', type: 'number', min: 0, max: 50 },
  { id: 'gateway.retry.backoff_s', category: 'budget', file: 'clio.yaml', path: 'gateway.retry.backoff_s', label: 'Retry: backoff (s)', type: 'number', min: 0 },
  { id: 'gateway.retry.max_backoff_s', category: 'budget', file: 'clio.yaml', path: 'gateway.retry.max_backoff_s', label: 'Retry: max backoff (s)', type: 'number', min: 0 },
  { id: 'gateway.retry.fail_over', category: 'budget', file: 'clio.yaml', path: 'gateway.retry.fail_over', label: 'Retry: fail over to another model', type: 'boolean' },

  // Deliberation (social) — social.yaml holds SocialConfig top-level
  { id: 'social.deliberation_max_tokens', category: 'deliberation', file: 'social.yaml', path: 'deliberation_max_tokens', label: 'Deliberation max tokens', type: 'number', min: 500, max: 200000 },
  { id: 'social.deliberation_escalated_max_tokens', category: 'deliberation', file: 'social.yaml', path: 'deliberation_escalated_max_tokens', label: 'Deliberation escalated max tokens', type: 'number', min: 500, max: 500000 },
  { id: 'social.execution_state_window_min', category: 'deliberation', file: 'social.yaml', path: 'execution_state_window_min', label: 'Execution-state window (min)', type: 'number', min: 1 },
  { id: 'social.execution_state_max_actions', category: 'deliberation', file: 'social.yaml', path: 'execution_state_max_actions', label: 'Execution-state max actions', type: 'number', min: 1 },
  { id: 'social.max_deliberation_attempts', category: 'deliberation', file: 'social.yaml', path: 'max_deliberation_attempts', label: 'Max deliberation attempts (0 = unlimited)', type: 'number', min: 0 },
  { id: 'social.proactive_enabled', category: 'deliberation', file: 'social.yaml', path: 'proactive_enabled', label: 'Proactive reporting', type: 'boolean' },
  { id: 'social.proactive_progress_interval_min', category: 'deliberation', file: 'social.yaml', path: 'proactive_progress_interval_min', label: 'Proactive progress interval (min)', type: 'number', min: 1 },

  // Solver (GSL)
  { id: 'gsl.understand_tokens', category: 'solver', file: 'gsl.yaml', path: 'understand_tokens', label: 'Understand tokens', type: 'number', min: 100, max: 100000 },
  { id: 'gsl.devise_tokens', category: 'solver', file: 'gsl.yaml', path: 'devise_tokens', label: 'Devise tokens', type: 'number', min: 100, max: 100000 },
  { id: 'gsl.plan_tokens', category: 'solver', file: 'gsl.yaml', path: 'plan_tokens', label: 'Plan tokens', type: 'number', min: 100, max: 100000 },
  { id: 'gsl.step_tokens', category: 'solver', file: 'gsl.yaml', path: 'step_tokens', label: 'Step tokens', type: 'number', min: 100, max: 100000 },
  { id: 'gsl.reflect_tokens', category: 'solver', file: 'gsl.yaml', path: 'reflect_tokens', label: 'Reflect tokens', type: 'number', min: 100, max: 100000 },
  { id: 'gsl.degraded_reasoning_budget', category: 'solver', file: 'gsl.yaml', path: 'degraded_reasoning_budget', label: 'Degraded reasoning budget', type: 'number', min: 0, max: 100000 },
  { id: 'gsl.reasoning_budget', category: 'solver', file: 'gsl.yaml', path: 'reasoning_budget', label: 'Reasoning budget', type: 'number', min: 0, max: 100000 },
  { id: 'gsl.model_hint', category: 'solver', file: 'gsl.yaml', path: 'model_hint', label: 'Model hint', type: 'text', placeholder: '(router picks)' },
  { id: 'gsl.plan_attempts_before_degrade', category: 'solver', file: 'gsl.yaml', path: 'plan_attempts_before_degrade', label: 'Plan attempts before degrade', type: 'number', min: 0 },
  { id: 'gsl.plan_attempts_before_park', category: 'solver', file: 'gsl.yaml', path: 'plan_attempts_before_park', label: 'Plan attempts before park (0 = never)', type: 'number', min: 0 },
  { id: 'gsl.plan_backoff_s', category: 'solver', file: 'gsl.yaml', path: 'plan_backoff_s', label: 'Plan backoff (s)', type: 'number', min: 0 },
  { id: 'gsl.plan_backoff_max_s', category: 'solver', file: 'gsl.yaml', path: 'plan_backoff_max_s', label: 'Plan max backoff (s)', type: 'number', min: 0 },
  { id: 'gsl.max_strategies', category: 'solver', file: 'gsl.yaml', path: 'max_strategies', label: 'Max strategies (0 = unlimited)', type: 'number', min: 0 },
  { id: 'gsl.verify_code', category: 'solver', file: 'gsl.yaml', path: 'verify_code', label: 'Verify code', type: 'boolean' },
  { id: 'gsl.verify_step_checks', category: 'solver', file: 'gsl.yaml', path: 'verify_step_checks', label: 'Verify step checks', type: 'boolean' },
  { id: 'gsl.unverified_is_failure', category: 'solver', file: 'gsl.yaml', path: 'unverified_is_failure', label: 'Unverified = failure', type: 'boolean' },

  // Rhythm
  { id: 'rhythm.energy.night_floor', category: 'rhythm', file: 'rhythm.yaml', path: 'energy.night_floor', label: 'Energy: night floor', type: 'number', min: 0, max: 1 },
  { id: 'rhythm.energy.peak_hour', category: 'rhythm', file: 'rhythm.yaml', path: 'energy.peak_hour', label: 'Energy: peak hour', type: 'number', min: 0, max: 24 },
  { id: 'rhythm.energy.low_energy', category: 'rhythm', file: 'rhythm.yaml', path: 'energy.low_energy', label: 'Energy: low energy', type: 'number', min: 0, max: 1 },
  { id: 'rhythm.focus.max_session_min', category: 'rhythm', file: 'rhythm.yaml', path: 'focus.max_session_min', label: 'Focus: max session (min)', type: 'number', min: 1 },
  { id: 'rhythm.focus.micro_break_min', category: 'rhythm', file: 'rhythm.yaml', path: 'focus.micro_break_min', label: 'Focus: micro break (min)', type: 'number', min: 0 },
  { id: 'rhythm.reverie.enabled', category: 'rhythm', file: 'rhythm.yaml', path: 'reverie.enabled', label: 'Reverie enabled', type: 'boolean' },
  { id: 'rhythm.reverie.max_tokens', category: 'rhythm', file: 'rhythm.yaml', path: 'reverie.max_tokens', label: 'Reverie max tokens', type: 'number', min: 50, max: 10000 },
  { id: 'rhythm.reverie.min_gap_min', category: 'rhythm', file: 'rhythm.yaml', path: 'reverie.min_gap_min', label: 'Reverie min gap (min)', type: 'number', min: 1 },
  { id: 'rhythm.reverie.max_gap_min', category: 'rhythm', file: 'rhythm.yaml', path: 'reverie.max_gap_min', label: 'Reverie max gap (min)', type: 'number', min: 1 },

  // Memory
  { id: 'memory.retrieval.top_k', category: 'memory', file: 'memory.yaml', path: 'retrieval.top_k', label: 'Retrieval top-k', type: 'number', min: 1, max: 500 },
  { id: 'memory.retrieval.candidate_k', category: 'memory', file: 'memory.yaml', path: 'retrieval.candidate_k', label: 'Retrieval candidate-k', type: 'number', min: 1, max: 1000 },
  { id: 'memory.retrieval.mmr_lambda', category: 'memory', file: 'memory.yaml', path: 'retrieval.mmr_lambda', label: 'Retrieval MMR lambda', type: 'number', min: 0, max: 1 },
  { id: 'memory.retrieval.recency_half_life_h', category: 'memory', file: 'memory.yaml', path: 'retrieval.recency_half_life_h', label: 'Recency half-life (h)', type: 'number', min: 1 },
  { id: 'memory.context.max_turns', category: 'memory', file: 'memory.yaml', path: 'context.max_turns', label: 'Context max turns', type: 'number', min: 1, max: 500 },
  { id: 'memory.context.max_chars', category: 'memory', file: 'memory.yaml', path: 'context.max_chars', label: 'Context max chars', type: 'number', min: 100, max: 100000 },
  { id: 'memory.context.narrate_every', category: 'memory', file: 'memory.yaml', path: 'context.narrate_every', label: 'Narrate every (turns)', type: 'number', min: 1 },
  { id: 'memory.decay.demote_strength', category: 'memory', file: 'memory.yaml', path: 'decay.demote_strength', label: 'Decay: demote strength', type: 'number', min: 0, max: 1 },
  { id: 'memory.decay.archive_after_days', category: 'memory', file: 'memory.yaml', path: 'decay.archive_after_days', label: 'Archive after (days)', type: 'number', min: 1 },

  // Permissions & Safety
  { id: 'permissions.safety_level', category: 'permissions', file: 'permissions.yaml', path: 'safety_level', label: 'Safety level', type: 'select', options: ['1', '2', '3', '4'], hint: '1 dialogue-only · 2 approve all · 3 approve internet · 4 auto' },
  { id: 'permissions.sudo_allowed', category: 'permissions', file: 'permissions.yaml', path: 'sudo_allowed', label: 'Allow sudo', type: 'boolean' },
  { id: 'permissions.approval_timeout_s', category: 'permissions', file: 'permissions.yaml', path: 'approval_timeout_s', label: 'Approval timeout (s)', type: 'number', min: 0 },
  { id: 'permissions.guardian.mode', category: 'permissions', file: 'permissions.yaml', path: 'guardian.mode', label: 'Guardian mode', type: 'select', options: ['field', 'normal'] },
  { id: 'permissions.network.deny_private_ranges', category: 'permissions', file: 'permissions.yaml', path: 'network.deny_private_ranges', label: 'Network: deny private ranges', type: 'boolean' },
  { id: 'permissions.network.max_response_bytes', category: 'permissions', file: 'permissions.yaml', path: 'network.max_response_bytes', label: 'Network: max response bytes', type: 'number', min: 0 },
  { id: 'permissions.interruption.daily_budgets.owner', category: 'permissions', file: 'permissions.yaml', path: 'interruption.daily_budgets.owner', label: 'Interruption budget: owner/day', type: 'number', min: 0 },
  { id: 'permissions.interruption.digest_window_min', category: 'permissions', file: 'permissions.yaml', path: 'interruption.digest_window_min', label: 'Digest window (min)', type: 'number', min: 0 },
  { id: 'clio.subagents.tier', category: 'permissions', file: 'clio.yaml', path: 'subagents.tier', label: 'Subagent tier', type: 'select', options: ['T1', 'T2', 'T3'] },
  { id: 'clio.subagents.model_name', category: 'permissions', file: 'clio.yaml', path: 'subagents.model_name', label: 'Subagent model name', type: 'text' },
];

const FIELD_MAP = new Map(AGENT_CONFIG_FIELDS.map((f) => [f.id, f]));

export function fieldById(id: string): AgentConfigField | undefined {
  return FIELD_MAP.get(id);
}

export function fieldsForCategory(category: AgentConfigCategory): AgentConfigField[] {
  return AGENT_CONFIG_FIELDS.filter((f) => f.category === category);
}

export function defaultAgentConfig(): Record<string, string | number | boolean | null> {
  const out: Record<string, string | number | boolean | null> = {};
  for (const f of AGENT_CONFIG_FIELDS) {
    // Fallback only; server returns the file's value. These keep forms stable before load.
    out[f.id] = f.type === 'boolean' ? false : f.type === 'number' ? (f.nullable ? null : 0) : '';
  }
  return out;
}

export function validateAgentConfigValue(
  field: AgentConfigField,
  value: unknown,
): { ok: true; value: string | number | boolean | null } | { ok: false; error: string } {
  if (field.nullable && (value === null || value === undefined || value === '')) {
    return { ok: true, value: null };
  }
  if (field.type === 'boolean') {
    if (typeof value === 'boolean') return { ok: true, value };
    if (value === 'true' || value === 1) return { ok: true, value: true };
    if (value === 'false' || value === 0) return { ok: true, value: false };
    return { ok: false, error: 'Expected true/false' };
  }
  if (field.type === 'number') {
    const n = typeof value === 'string' ? Number(String(value).trim()) : value;
    if (typeof n !== 'number' || !Number.isFinite(n)) return { ok: false, error: 'Expected a number' };
    if (field.min !== undefined && n < field.min) return { ok: false, error: `Below minimum ${field.min}` };
    if (field.max !== undefined && n > field.max) return { ok: false, error: `Above maximum ${field.max}` };
    return { ok: true, value: n };
  }
  if (field.type === 'select') {
    const s = String(value ?? '').trim();
    if (field.options && !field.options.includes(s)) return { ok: false, error: `Choose one of ${field.options.join(', ')}` };
    return { ok: true, value: s };
  }
  const s = String(value ?? '').trim();
  if (s.length > 500) return { ok: false, error: 'Too long (max 500)' };
  return { ok: true, value: s };
}
