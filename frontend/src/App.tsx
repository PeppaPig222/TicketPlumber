import { useCallback, useEffect, useRef, useState } from "react";
import { diagnose, streamTrace, agentUpdateToNode } from "./api";
import type { DiagnosisResult, Round, SSEEvent } from "./types";
import AgentTimeline from "./components/AgentTimeline";
import DiagnosisConclusion from "./components/DiagnosisConclusion";
import DiagnosisInput from "./components/DiagnosisInput";

const EVENT_DELAY_MS = 250;

function sleep(ms: number): Promise<void> {
  return new Promise((resolve) => setTimeout(resolve, ms));
}

export default function App() {
  const [query, setQuery] = useState("请诊断工单 WO-20260815-0421");
  const [loading, setLoading] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const [result, setResult] = useState<DiagnosisResult | null>(null);
  const [liveRounds, setLiveRounds] = useState<Round[]>([]);
  const [streaming, setStreaming] = useState(false);
  const cleanupRef = useRef<(() => void) | null>(null);

  // 组件卸载时关闭 SSE 连接
  useEffect(() => {
    return () => {
      if (cleanupRef.current) {
        cleanupRef.current();
        cleanupRef.current = null;
      }
    };
  }, []);

  const reset = useCallback(() => {
    setError(null);
    setResult(null);
    setLiveRounds([]);
    if (cleanupRef.current) {
      cleanupRef.current();
      cleanupRef.current = null;
    }
  }, []);

  const applyEvent = useCallback(async (event: SSEEvent) => {
    await sleep(EVENT_DELAY_MS);
    setLiveRounds((prev) => {
      const next = prev.map((r) => ({ ...r, agents: [...r.agents] }));
      if (event.event === "agent_update") {
        const node = agentUpdateToNode(event);
        if (!node) return next;
        const round = next.find((r) => r.round_num === event.data.round);
        if (round) {
          round.agents.push(node);
        } else {
          next.push({
            round_num: event.data.round,
            intent: "",
            duration_ms: 0,
            decision: "",
            agents: [node],
          });
        }
      } else if (event.event === "round_complete") {
        const round = next.find((r) => r.round_num === event.data.round);
        if (round) {
          round.duration_ms = event.data.duration_ms;
          round.decision = event.data.decision;
        }
      }
      return next;
    });
  }, []);

  const handleSubmit = useCallback(async () => {
    reset();
    setLoading(true);
    setStreaming(true);

    try {
      const data = await diagnose(query);
      setResult(data);

      const traceId = data.trace_id || "";
      if (!traceId) {
        // 没有 trace_id 时直接展示完整 trace
        setLiveRounds(data.trace?.rounds || []);
        setStreaming(false);
        setLoading(false);
        return;
      }

      const cleanup = streamTrace(
        traceId,
        async (event) => {
          await applyEvent(event);
        },
        () => {
          // SSE 正常结束（收到 diagnosis_complete 后主动关闭）
          setStreaming(false);
          setLoading(false);
        },
        (err) => {
          console.error("SSE 错误:", err);
          setError(err.message);
          setStreaming(false);
          setLoading(false);
        }
      );
      cleanupRef.current = cleanup;
    } catch (err) {
      setError(err instanceof Error ? err.message : String(err));
      setStreaming(false);
      setLoading(false);
    }
  }, [query, reset, applyEvent]);

  const displayRounds = result?.trace?.rounds?.length
    ? result.trace.rounds
    : liveRounds;
  const totalDurationMs = result?.trace?.total_duration_ms || 0;

  return (
    <div className="page">
      <DiagnosisInput
        query={query}
        loading={loading}
        onChange={setQuery}
        onSubmit={handleSubmit}
      />

      {error && (
        <div className="card" style={{ background: "#fee2e2", color: "#b91c1c" }}>
          {error}
        </div>
      )}

      <div className="grid">
        <AgentTimeline
          rounds={displayRounds}
          totalDurationMs={totalDurationMs}
          streaming={streaming}
        />
        <DiagnosisConclusion
          status={result?.status || "-"}
          scenario={result?.scenario || "-"}
          diagnosis={result?.diagnosis || null}
        />
      </div>
    </div>
  );
}
