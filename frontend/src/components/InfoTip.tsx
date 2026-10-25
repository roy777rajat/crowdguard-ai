import { useId, useState, type ReactNode } from 'react';
import '../styles/infotip.css';

/** Small "i" button that explains what something is and why it matters. Works with hover, focus, click and Escape. */
export default function InfoTip({ title, children, align = 'left' }: { title: string; children: ReactNode; align?: 'left' | 'right' }) {
  const [open, setOpen] = useState(false);
  const id = useId();
  return (
    <span className="infotip" onMouseEnter={() => setOpen(true)} onMouseLeave={() => setOpen(false)}>
      <button
        type="button"
        className="infotip-btn"
        aria-label={`About: ${title}`}
        aria-expanded={open}
        aria-describedby={open ? id : undefined}
        onClick={(e) => { e.preventDefault(); e.stopPropagation(); setOpen(!open); }}
        onFocus={() => setOpen(true)}
        onBlur={() => setOpen(false)}
        onKeyDown={(e) => { if (e.key === 'Escape') setOpen(false); }}
      >
        i
      </button>
      {open && (
        <span role="tooltip" id={id} className={`infotip-pop ${align}`}>
          <strong>{title}</strong>
          <span className="infotip-body">{children}</span>
        </span>
      )}
    </span>
  );
}
