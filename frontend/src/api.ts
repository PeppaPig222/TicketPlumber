import type { DiagnosisResult, SSEEvent, AgentNode } from "./types";

const API_BASE = "/api/v1";
const SSE_CONNECT_TIMEOUT_MS = 8000; // 连接超时
const DIAGNOSE_TIMEOUT_MS = 60000;   // 诊断请求总超时

export async function diagnose(query: string): Promise<DiagnosisResult> {
  const controller = new AbortController();
  const timer = setTimeout(() => controller.abort(), DIAGNOSE_TIMEOUT_MS);

  try {
    const response = await fetch(`${API_BASE}/diagnose`, {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ query }),
      signal: controller.signal,
    });
    if (!response.ok) {
      throw new Error(`诊断请求失败: ${response.status}`);
    }
    return response.json();
  } catch (err) {
    if (err instanceof DOMException && err.name === "AbortError") {
      throw new Error("诊断请求超时，请稍后重试");
    }
    throw err;
  } finally {
    clearTimeout(timer);
  }
}

export function streamTrace(
  traceId: string,
  onEvent: (event: SSEEvent) => void,
  onDone: () => void,
  onError: (error: Error) => void
): () => void {
  const url = `${API_BASE}/trace/stream/${encodeURIComponent(traceId)}`;
  const eventSource = new EventSource(url);
  let completed = false;

  // 连接超时：8 秒内未建立连接则报错
  const connectTimer = setTimeout(() => {
    if (eventSource.readyState === EventSource.CONNECTING) {
      eventSource.close();
      onError(new Error("SSE 连接超时"));
    }
  }, SSE_CONNECT_TIMEOUT_MS);

  eventSource.onopen = () => {
    clearTimeout(connectTimer);
  };

  // SSE 规范：带 event: 字段的命名事件不会触发 onmessage，
  // 必须用 addEventListener 按事件名分别监听
  const EVENT_TYPES = ["agent_update", "round_complete", "diagnosis_complete"];

  EVENT_TYPES.forEach((eventType) => {
    eventSource.addEventListener(eventType, (e: MessageEvent) => {
      try {
        const payload = JSON.parse(e.data);
        onEvent({ event: eventType as SSEEvent["event"], data: payload });

        if (eventType === "diagnosis_complete") {
          completed = true;
          clearTimeout(connectTimer);
          eventSource.close();
          onDone();
        }
      } catch (err) {
        console.error("SSE 事件解析失败:", err);
      }
    });
  });

  eventSource.onerror = () => {
    eventSource.close();
    clearTimeout(connectTimer);
    if (!completed) {
      onError(new Error("SSE 连接异常"));
    }
  };

  return () => {
    clearTimeout(connectTimer);
    eventSource.close();
  };
}

// 将 SSE agent_update 事件转换为 AgentNode 用于逐步渲染
export function agentUpdateToNode(event: SSEEvent): AgentNode | null {
  if (event.event !== "agent_update") return null;
  const { data } = event;
  return {
    agent_name: data.agent,
    priority: 1,
    status: data.status,
    duration_ms: data.duration_ms,
    output_summary: "", // SSE 事件不携带 summary，完整 trace 从 diagnose 接口拿
    tools_called: data.tools || [],
    recommended_skills: data.recommended_skills || [],
    evidence: [],
  };
}
