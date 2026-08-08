import type { DiagnosisResult, SSEEvent, AgentNode } from "./types";

const API_BASE = "/api/v1";

export async function diagnose(query: string): Promise<DiagnosisResult> {
  const response = await fetch(`${API_BASE}/diagnose`, {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify({ query }),
  });
  if (!response.ok) {
    throw new Error(`诊断请求失败: ${response.status}`);
  }
  return response.json();
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

  // SSE 规范：带 event: 字段的命名事件不会触发 onmessage，
  // 必须用 addEventListener 按事件名分别监听
  const EVENT_TYPES = ["agent_update", "round_complete", "diagnosis_complete"];

  EVENT_TYPES.forEach((eventType) => {
    eventSource.addEventListener(eventType, (e: MessageEvent) => {
      try {
        const payload = JSON.parse(e.data);
        onEvent({ event: eventType as SSEEvent["event"], data: payload });

        // 收到 diagnosis_complete 后，主动关闭连接避免浏览器误报 onerror
        if (eventType === "diagnosis_complete") {
          completed = true;
          eventSource.close();
          onDone();
        }
      } catch (err) {
        // 单条事件解析失败不中断流
        console.error("SSE 事件解析失败:", err);
      }
    });
  });

  eventSource.onerror = () => {
    eventSource.close();
    // 如果已经收到 diagnosis_complete，说明是正常结束，不报错
    if (!completed) {
      onError(new Error("SSE 连接异常"));
    }
  };

  return () => eventSource.close();
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
