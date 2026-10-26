import type { ReactNode } from 'react';

/**
 * Small, safe Markdown renderer for assistant answers. It builds React elements only (no innerHTML), supports
 * headings, paragraphs, bullet/numbered lists, tables, code fences, bold, italics, inline code, and http(s) links.
 * Anything else is shown as plain text. Model output is never interpreted as HTML.
 */
const INLINE = /(\*\*[^*]+\*\*|`[^`]+`|\*[^*\s][^*]*\*|\[[^\]]+\]\(https?:\/\/[^\s)]+\))/g;

function inline(text: string, keyBase: string): ReactNode[] {
  return text.split(INLINE).map((part, i) => {
    const key = `${keyBase}-${i}`;
    if (part.startsWith('**') && part.endsWith('**') && part.length > 4) return <strong key={key}>{part.slice(2, -2)}</strong>;
    if (part.startsWith('`') && part.endsWith('`') && part.length > 2) return <code key={key}>{part.slice(1, -1)}</code>;
    if (part.startsWith('*') && part.endsWith('*') && part.length > 2) return <em key={key}>{part.slice(1, -1)}</em>;
    const m = /^\[([^\]]+)\]\((https?:\/\/[^\s)]+)\)$/.exec(part);
    if (m) {
      return (
        <a key={key} href={m[2]} target="_blank" rel="noopener noreferrer nofollow">
          {m[1]}
        </a>
      );
    }
    return part;
  });
}

const isTableRow = (l: string) => /^\s*\|.*\|\s*$/.test(l);
const isSep = (l: string) => /^\s*\|?[\s:|-]+\|[\s:|-]*$/.test(l) && l.includes('-');
const cells = (l: string) => l.trim().replace(/^\|/, '').replace(/\|$/, '').split('|').map((c) => c.trim());

export default function Markdown({ text }: { text: string }) {
  const lines = text.replace(/\r\n/g, '\n').split('\n');
  const out: ReactNode[] = [];
  let i = 0;
  let k = 0;
  while (i < lines.length) {
    const line = lines[i];
    const key = `b${k++}`;
    if (!line.trim()) {
      i++;
    } else if (line.trim().startsWith('```')) {
      const body: string[] = [];
      i++;
      while (i < lines.length && !lines[i].trim().startsWith('```')) body.push(lines[i++]);
      i++;
      out.push(<pre key={key}><code>{body.join('\n')}</code></pre>);
    } else if (/^#{1,4}\s/.test(line)) {
      out.push(<h4 key={key}>{inline(line.replace(/^#{1,4}\s+/, ''), key)}</h4>);
      i++;
    } else if (isTableRow(line) && i + 1 < lines.length && isSep(lines[i + 1])) {
      const head = cells(line);
      i += 2;
      const rows: string[][] = [];
      while (i < lines.length && isTableRow(lines[i])) rows.push(cells(lines[i++]));
      out.push(
        <div className="md-table" key={key}>
          <table>
            <thead><tr>{head.map((h, c) => <th key={c}>{inline(h, `${key}h${c}`)}</th>)}</tr></thead>
            <tbody>{rows.map((r, ri) => <tr key={ri}>{r.map((c, ci) => <td key={ci}>{inline(c, `${key}r${ri}${ci}`)}</td>)}</tr>)}</tbody>
          </table>
        </div>,
      );
    } else if (/^\s*[-*]\s+/.test(line)) {
      const items: string[] = [];
      while (i < lines.length && /^\s*[-*]\s+/.test(lines[i])) items.push(lines[i++].replace(/^\s*[-*]\s+/, ''));
      out.push(<ul key={key}>{items.map((t, n) => <li key={n}>{inline(t, `${key}${n}`)}</li>)}</ul>);
    } else if (/^\s*\d+[.)]\s+/.test(line)) {
      const items: string[] = [];
      while (i < lines.length && /^\s*\d+[.)]\s+/.test(lines[i])) items.push(lines[i++].replace(/^\s*\d+[.)]\s+/, ''));
      out.push(<ol key={key}>{items.map((t, n) => <li key={n}>{inline(t, `${key}${n}`)}</li>)}</ol>);
    } else {
      const para: string[] = [];
      while (i < lines.length && lines[i].trim() && !/^(#{1,4}\s|\s*[-*]\s+|\s*\d+[.)]\s+|```)/.test(lines[i]) && !isTableRow(lines[i])) {
        para.push(lines[i++]);
      }
      if (para.length === 0) {
        para.push(lines[i++]);
      }
      out.push(<p key={key}>{inline(para.join(' '), key)}</p>);
    }
  }
  return <div className="md">{out}</div>;
}
