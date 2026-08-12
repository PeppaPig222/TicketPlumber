import type { DiagnosisData, ResponsibilityMatrixItem } from "../types";

interface Props {
  status: string;
  scenario: string;
  diagnosis: DiagnosisData | null;
}

function scoreClass(score: number): string {
  if (score >= 0.8) return "pill-red";
  if (score >= 0.5) return "pill-yellow";
  return "pill-gray";
}

function ResponsibilityMatrix({ items }: { items: ResponsibilityMatrixItem[] }) {
  if (!items || items.length === 0) return null;
  const sorted = [...items].sort((a, b) => b.score - a.score);
  return (
    <div style={{ marginTop: 16 }}>
      <strong>归属方判定矩阵：</strong>
      <div style={{ marginTop: 8 }}>
        {sorted.map((item, idx) => (
          <div key={idx} className="matrix-row">
            <span className="matrix-party">{item.party}</span>
            <span className={`pill ${scoreClass(item.score)}`}>
              {(item.score * 100).toFixed(0)}%
            </span>
            <span className="muted" style={{ marginLeft: 8, fontSize: 13 }}>
              {item.reasons?.join("；") || "-"}
            </span>
          </div>
        ))}
      </div>
    </div>
  );
}

export default function DiagnosisConclusion({ status, scenario, diagnosis }: Props) {
  return (
    <div className="card">
      <h2>诊断结论</h2>
      {!diagnosis ? (
        <div className="empty-state">等待执行...</div>
      ) : (
        <div className="conclusion-section">
          <div style={{ marginBottom: 12 }}>
            <span className="pill pill-blue">{scenario || "unknown"}</span>
            <span className="pill pill-green">{status || "-"}</span>
            <span className="pill pill-yellow">{diagnosis.responsible_party || "待判定"}</span>
          </div>
          <p>
            <strong>摘要：</strong>
            {diagnosis.summary || "-"}
          </p>
          <p>
            <strong>根因：</strong>
            {diagnosis.root_cause || "-"}
          </p>
          <ResponsibilityMatrix items={diagnosis.responsible_party_matrix || []} />
          <p style={{ marginTop: 16 }}>
            <strong>建议：</strong>
          </p>
          <ul>
            {(diagnosis.recommendations || []).length === 0 ? (
              <li>无</li>
            ) : (
              diagnosis.recommendations.map((item, idx) => <li key={idx}>{item}</li>)
            )}
          </ul>
        </div>
      )}
    </div>
  );
}
