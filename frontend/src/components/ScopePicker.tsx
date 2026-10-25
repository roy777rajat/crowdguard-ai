import type { Scope } from '../hooks/useScope';
import InfoTip from './InfoTip';

export default function ScopePicker({ scope, compact = false }: { scope: Scope; compact?: boolean }) {
  return (
    <div className={`scope-picker form${compact ? ' compact' : ''}`}>
      <label className="field">
        <span>Location
          <InfoTip title="Location">Part of Kolkata (South, North, East, West). Choose it first; the pandal list then shows only pandals in that part of the city.</InfoTip>
        </span>
        <select value={scope.locationId ?? ''} onChange={(e) => scope.setLocation(Number(e.target.value))}>
          {scope.locations.map((l) => (
            <option key={l.id} value={l.id}>{l.name} ({l.pandal_count})</option>
          ))}
        </select>
      </label>
      <label className="field">
        <span>Pandal (association)
          <InfoTip title="Pandal">One Puja committee. Each pandal has exactly 3 zones and one camera per zone. Everything on this page is for the chosen pandal only.</InfoTip>
        </span>
        <select value={scope.pandalId ?? ''} onChange={(e) => scope.setPandal(Number(e.target.value))} disabled={scope.pandals.length === 0}>
          {scope.pandals.length === 0 && <option value="">No pandal in this location</option>}
          {scope.pandals.map((p) => (
            <option key={p.id} value={p.id}>{p.name}</option>
          ))}
        </select>
      </label>
    </div>
  );
}
