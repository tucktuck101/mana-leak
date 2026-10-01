// TypeScript mirror of docs/contracts.md shapes used by the web client.
// Names/fields follow contracts.md verbatim (Core enums, Core service
// interfaces, Streaming events, HTTP error mapping) — do not redefine a
// second shape here if the contract changes; update this file to match.

export type Route = "cards" | "combos" | "judge" | "other";
export type MessageRole = "user" | "assistant" | "tool";

export type ErrorCode =
  | "validation_error"
  | "not_found"
  | "ambiguous_match"
  | "conflict"
  | "dependency_unavailable"
  | "timeout"
  | "tool_not_allowed"
  | "tool_limit_exceeded"
  | "model_limit_exceeded"
  | "structured_output_invalid"
  | "citation_validation_failed"
  | "insufficient_evidence"
  | "safeguard_rejected"
  | "internal_error";

export interface ErrorInfo {
  code: ErrorCode;
  message: string;
  retryable: boolean;
  details: Record<string, unknown>;
}

export interface ErrorResponse {
  error: ErrorInfo;
}

export interface ConversationSummary {
  id: string;
  title: string | null;
  created_at: string;
  updated_at: string;
}

export interface MessageOut {
  id: string;
  seq: number;
  turn_id: string;
  role: MessageRole;
  content: string;
  payload: Record<string, unknown> | null;
  route: Route | null;
  created_at: string;
}

// `active_judge_session` (JudgeSessionState | null) is unused until M8; kept
// as `unknown` rather than omitted so the field is still present on the type.
export interface ConversationDetail extends ConversationSummary {
  messages: MessageOut[]; // all messages, ascending seq
  active_judge_session: unknown | null;
}

export interface HealthResponse {
  status: "ok" | "degraded";
  database: "ok" | "unavailable";
  langfuse: "ok" | "disabled" | "unavailable";
  rules_version: string | null;
  card_source_version: string | null;
}

// Streaming events (contracts.md → Streaming events → Canonical TurnEvent).
// Ordering: message_start -> (text_delta | tool_start | tool_end)* -> one of
// (final | error) -> message_end. M1 never emits tool_start/tool_end (no
// tools on the `other` route) but the union includes them for forward
// compatibility with the fixed wire contract.

interface EventBase {
  conversation_id: string;
  turn_id: string;
}

export interface MessageStartEvent extends EventBase {
  type: "message_start";
  message_id: string;
}

export interface TextDeltaEvent extends EventBase {
  type: "text_delta";
  delta: string;
}

export interface ToolStartEvent extends EventBase {
  type: "tool_start";
  call_id: string;
  tool: string;
  args: Record<string, unknown>;
}

export interface ToolEndEvent extends EventBase {
  type: "tool_end";
  call_id: string;
  tool: string;
  ok: boolean;
  summary: string;
}

export interface FinalEvent extends EventBase {
  type: "final";
  route: Route | null;
  text: string; // full assistant text
  result: unknown | null; // TurnResult; always null in M1 (`other` route)
  error: ErrorInfo | null; // refusal/limit info
}

export interface TurnErrorEvent extends EventBase {
  type: "error";
  error: ErrorInfo;
}

export interface MessageEndEvent extends EventBase {
  type: "message_end";
}

export type TurnEvent =
  | MessageStartEvent
  | TextDeltaEvent
  | ToolStartEvent
  | ToolEndEvent
  | FinalEvent
  | TurnErrorEvent
  | MessageEndEvent;
