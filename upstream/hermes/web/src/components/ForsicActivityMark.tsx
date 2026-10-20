import './forsic-activity-mark.css';

export interface ForsicActivityMarkProps {
  state: 'idle' | 'working' | 'waiting' | 'error';
  size?: number;
  className?: string;
}

// The surrounding status text supplies the accessible name.
export function ForsicActivityMark({ state, size = 32, className = '' }: ForsicActivityMarkProps) {
  return (
    <svg
      className={`forsic-activity-mark ${className}`.trim()}
      data-state={state}
      width={size}
      height={size}
      viewBox="0 0 40 40"
      fill="none"
      aria-hidden="true"
      focusable="false"
    >
      <path
        className="forsic-activity-mark__corners"
        d="M10 5H7a2 2 0 0 0-2 2v3m25-5h3a2 2 0 0 1 2 2v3M5 30v3a2 2 0 0 0 2 2h3m20 0h3a2 2 0 0 0 2-2v-3"
        strokeWidth="1.6"
        strokeLinecap="round"
      />
      <circle className="forsic-activity-mark__track" cx="20" cy="20" r="13" strokeWidth="1" />
      {state === 'working' && (
        <g className="forsic-activity-mark__scan">
          <path d="M9.7 12.1A13 13 0 0 1 28 9.8" stroke="currentColor" strokeWidth="1.6" strokeLinecap="round" />
          <circle cx="29.8" cy="11.5" r="1.8" fill="currentColor" />
        </g>
      )}
      <g className="forsic-activity-mark__companion">
        <rect className="forsic-activity-mark__shell" x="10.5" y="12" width="19" height="17" rx="7" strokeWidth="1.25" />
        <path className="forsic-activity-mark__gleam" d="M14.5 16.5a4 4 0 0 1 3-1.2" strokeWidth="1.2" strokeLinecap="round" />
        {state === 'waiting' ? (
          <path className="forsic-activity-mark__face" d="M17.5 18v5m5-5v5" strokeWidth="2" strokeLinecap="round" />
        ) : state === 'error' ? (
          <g className="forsic-activity-mark__face">
            <path d="M20 16.5v4.2" strokeWidth="2" strokeLinecap="round" />
            <circle cx="20" cy="24" r="1" fill="currentColor" stroke="none" />
          </g>
        ) : (
          <g className="forsic-activity-mark__face">
            <path d="M16 19v1.5m8-1.5v1.5" strokeWidth="2" strokeLinecap="round" />
            <path d="M18 24q2 1.5 4 0" strokeWidth="1.25" strokeLinecap="round" />
          </g>
        )}
      </g>
    </svg>
  );
}
