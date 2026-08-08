import type { AgentStatus } from "../types";

interface Props {
  status: AgentStatus;
}

const labelMap: Record<AgentStatus, string> = {
  success: "成功",
  degraded: "降级",
  error: "失败",
  timeout: "超时",
  unknown: "未知",
};

const classMap: Record<AgentStatus, string> = {
  success: "pill-green",
  degraded: "pill-yellow",
  error: "pill-red",
  timeout: "pill-red",
  unknown: "pill-gray",
};

export default function StatusBadge({ status }: Props) {
  return <span className={`pill ${classMap[status] || "pill-gray"}`}>{labelMap[status] || status}</span>;
}
