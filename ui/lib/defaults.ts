// Mirror of backend defaults from `services/bot_config.py`. Used to
// surface "(default)" hints inline so admins can see what value the
// bot will fall back to when a field is left blank.
//
// Keep in sync with the LimitsConfig / RAGConfig / LLMConfig defaults
// in the Pydantic models. If a default changes server-side, update
// this file in the same PR.

export const RAG_DEFAULTS = {
  top_k: 5,
  chunk_size: 2048,
  chunk_overlap: 256,
  enable_hybrid_search: false,
  embedding_threshold: 0.20,
} as const;

export const LIMITS_DEFAULTS = {
  max_file_size_mb: 50,
  total_storage_quota_mb: undefined as number | undefined,
  file_count_cap: 10_000,
  daily_token_cap: undefined as number | undefined,
  messages_per_minute_per_user: undefined as number | undefined,
  messages_per_minute_per_bot: undefined as number | undefined,
} as const;

export const LLM_DEFAULTS = {
  model_type: "openai",
  temperature: 0.5,
} as const;

export const GLOBAL_PROMPT_DEFAULTS = {
  welcome_message: "Hi! How can I help today?",
  goodbye_message: "Thanks for chatting!",
  error_message: "Sorry, something went wrong. Please try again.",
  slot_question: "Could you please provide your {slot_name}?",
} as const;
