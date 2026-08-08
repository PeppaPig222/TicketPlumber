import type { DiagnosisData } from "../types";

interface Props {
  status: string;
  scenario: string;
  diagnosis: DiagnosisData | null;
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
          <p>
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
