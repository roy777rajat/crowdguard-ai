export interface ChartSeries {
  name: string;
  color: string;
  values: number[];
}

export interface HLine {
  y: number;
  label: string;
  color: string;
}

interface Props {
  title: string;
  series: ChartSeries[];
  hlines?: HLine[];
  xLabels?: Array<string | number | null>;
  height?: number;
}

export default function TrendChart({ title, series, hlines = [], xLabels = [], height = 200 }: Props) {
  const W = 640;
  const H = height;
  const P = { l: 46, r: 12, t: 12, b: 24 };
  const n = Math.max(0, ...series.map((s) => s.values.length));
  if (n < 2) return <div className="chart-empty">Not enough data yet for “{title}”.</div>;

  const all = series.flatMap((s) => s.values).concat(hlines.map((h) => h.y));
  const min = Math.min(0, ...all);
  let max = Math.max(...all);
  if (max === min) max = min + 1;
  max *= 1.05;

  const sx = (i: number) => P.l + (i / (n - 1)) * (W - P.l - P.r);
  const sy = (v: number) => P.t + (1 - (v - min) / (max - min)) * (H - P.t - P.b);
  const path = (vals: number[]) =>
    vals.map((v, i) => `${i === 0 ? 'M' : 'L'}${sx(i).toFixed(1)},${sy(v).toFixed(1)}`).join(' ');
  const yTicks = [0, 1, 2, 3].map((i) => min + ((max - min) * i) / 3);
  const xIdx = [0, Math.floor((n - 1) / 2), n - 1];

  return (
    <div>
      <svg className="chart" viewBox={`0 0 ${W} ${H}`} role="img" aria-label={title}>
        {yTicks.map((t) => (
          <g key={t}>
            <line className="grid-line" x1={P.l} x2={W - P.r} y1={sy(t)} y2={sy(t)} />
            <text x={P.l - 6} y={sy(t) + 4} textAnchor="end">
              {Math.round(t)}
            </text>
          </g>
        ))}
        {hlines.map((h) => (
          <g key={h.label}>
            <line x1={P.l} x2={W - P.r} y1={sy(h.y)} y2={sy(h.y)} stroke={h.color} strokeWidth="1.5" strokeDasharray="5 4" />
            <text x={W - P.r - 2} y={sy(h.y) - 4} textAnchor="end" style={{ fill: h.color }}>
              {h.label}
            </text>
          </g>
        ))}
        {series.map((s) => (
          <path key={s.name} d={path(s.values)} fill="none" stroke={s.color} strokeWidth="2" strokeLinejoin="round" />
        ))}
        {xIdx.map((i) =>
          xLabels[i] !== undefined && xLabels[i] !== null ? (
            <text key={i} x={sx(i)} y={H - 6} textAnchor={i === 0 ? 'start' : i === n - 1 ? 'end' : 'middle'}>
              {xLabels[i]}
            </text>
          ) : null,
        )}
      </svg>
      <div className="legend">
        {series.map((s) => (
          <span key={s.name}>
            <i style={{ background: s.color }} />
            {s.name}
          </span>
        ))}
      </div>
    </div>
  );
}
