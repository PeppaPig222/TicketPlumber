interface Props {
  query: string;
  loading: boolean;
  onChange: (value: string) => void;
  onSubmit: () => void;
}

const examples = [
  "请诊断工单 WO-20260815-0421",
  "帮我看下工单 WO-20260816-0532 为什么资产分配失败",
  "请排查工单 WO-20260817-0611 的结算金额不符问题",
];

export default function DiagnosisInput({ query, loading, onChange, onSubmit }: Props) {
  return (
    <div className="card">
      <h1>小哈工单智能诊断助手</h1>
      <p className="muted">支持工单诊断、多轮 Loop、多 Agent 并行排查与 trace 实时回放。</p>
      <textarea
        value={query}
        onChange={(e) => onChange(e.target.value)}
        placeholder="输入工单描述或工单编号..."
      />
      <div style={{ marginTop: 12 }}>
        <button onClick={onSubmit} disabled={loading || !query.trim()}>
          {loading ? "诊断中..." : "开始诊断"}
        </button>
      </div>
      <div className="quick-tags">
        {examples.map((text) => (
          <button
            key={text}
            onClick={() => onChange(text)}
            disabled={loading}
            type="button"
          >
            {text}
          </button>
        ))}
      </div>
    </div>
  );
}
