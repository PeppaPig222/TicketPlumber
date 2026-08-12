export type AgentStatus = "success" | "error" | "timeout" | "degraded" | "unknown";

export interface AgentNode {
  agent_name: string;
  priority: number;
  status: AgentStatus;
  duration_ms: number;
  output_summary: string;
  tools_called: string[];
  recommended_skills: string[];
  evidence: string[];
}

export interface Round {
  round_num: number;
  intent: string;
  duration_ms: number;
  decision: string;
  agents: AgentNode[];
}

export interface Trace {
  ticket_id: string;
  start_time: string;
  total_duration_ms: number;
  total_rounds: number;
  rounds: Round[];
}

export interface ResponsibilityMatrixItem {
  party: string;
  score: number;
  reasons: string[];
}

export interface DiagnosisData {
  summary: string;
  root_cause: string;
  responsible_party: string;
  responsible_party_matrix?: ResponsibilityMatrixItem[];
  recommendations: string[];
}

export interface DiagnosisResult {
  trace_id: string;
  status: string;
  scenario: string;
  ticket_id: string;
  trace: Trace;
  diagnosis: DiagnosisData;
}

export interface SSEAgentUpdateEvent {
  event: "agent_update";
  data: {
    round: number;
    agent: string;
    status: AgentStatus;
    duration_ms: number;
    tools: string[];
    recommended_skills: string[];
  };
}

export interface SSERoundCompleteEvent {
  event: "round_complete";
  data: {
    round: number;
    decision: string;
    duration_ms: number;
  };
}

export interface SSEDiagnosisCompleteEvent {
  event: "diagnosis_complete";
  data: {
    total_duration_ms: number;
  };
}

export type SSEEvent =
  | SSEAgentUpdateEvent
  | SSERoundCompleteEvent
  | SSEDiagnosisCompleteEvent;
