import InfoTip from './InfoTip';

/** Delete-selected / delete-all buttons with a confirmation. Shown to administrators only (the server enforces it too). */
export default function DeleteBar({ noun, selected, total, onDeleteSelected, onDeleteAll, info }: {
  noun: string; selected: number; total: number; info: string;
  onDeleteSelected: () => void; onDeleteAll: () => void;
}) {
  return (
    <div className="row delete-bar">
      <button className="btn small danger" disabled={selected === 0}
        onClick={() => { if (window.confirm(`Permanently delete ${selected} selected ${noun}? This cannot be undone.`)) onDeleteSelected(); }}>
        Delete selected ({selected})
      </button>
      <button className="btn small danger" disabled={total === 0}
        onClick={() => { if (window.confirm(`Permanently delete ALL ${noun} (${total} shown or more)? This cannot be undone.`)) onDeleteAll(); }}>
        Delete all
      </button>
      <InfoTip title={`Deleting ${noun}`}>{info}</InfoTip>
    </div>
  );
}

export function useToggle(selected: Set<string>, setSelected: (s: Set<string>) => void) {
  return {
    toggle: (id: string) => {
      const n = new Set(selected);
      if (n.has(id)) n.delete(id); else n.add(id);
      setSelected(n);
    },
    toggleAll: (ids: string[]) => setSelected(selected.size === ids.length ? new Set() : new Set(ids)),
  };
}
