import type { Round } from "../types";
import StatusBadge from "./StatusBadge";

interface Props {
  rounds: Round[];
  totalDurationMs: number;
  streaming: boolean;
}

function roundStatusClass(round: Round): string {
  const statuses = round.agents.map((a) => a.status);
  if (statuses.some((s) => s === "error" || s === "timeout")) return "error";
  if (statuses.some((s) => s === "degraded")) return "degraded";
  if (statuses.every((s) => s === "success")) return "success";
  return "";
}

export default function AgentTimeline({ rounds, totalDurationMs, streaming }: Props) {
  return (
    <div className="card">
      <h2>Agent 追踪面板</h2>
      {rounds.length === 0 ? (
        <div className="empty-state">
          {streaming ? "等待 trace 事件..." : "暂无 trace"}
        </div>
      ) : (
        <div className="timeline">
          {rounds.map((round) => (
            <div key={round.round_num} className="round-block">
              <div className={`round-dot ${roundStatusClass(round)}`} />
              <div className="round-line" />
              <div>
                <strong>Round {round.round_num}</strong>
                <span className="muted" style={{ marginLeft: 8 }}>
                  {round.intent} · {round.duration_ms}ms · 决策：{round.decision || "-"}
                </span>
              </div>
              {round.agents.map((agent, idx) => (
                <div
                  key={`${agent.agent_name}-${idx}`}
                  className={`agent-node ${agent.status}`}
                >
                  <div className="agent-header">
                    <span className="agent-name">{agent.agent_name}</span>
                    <span className="agent-meta">
                      <StatusBadge status={agent.status} /> · {agent.duration_ms}ms
                    </span>
                  </div>
                  <div className="muted">{agent.output_summary || "执行中..."}</div>
                  {agent.tools_called.length > 0 && (
                    <div className="tools-list">
                      {agent.tools_called.map((tool) => (
                        <span key={tool} className="tool-tag">{tool}</span>
                      ))}
                    </div>
                  )}
                </div>
              ))}
            </div>
          ))}
          <div className="muted" style={{ marginTop: 12 }}>
            总耗时：{totalDurationMs}ms
          </div>
        </div>
      )}
    </div>
  );
}
