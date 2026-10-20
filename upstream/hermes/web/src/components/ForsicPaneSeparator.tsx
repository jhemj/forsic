import {useEffect, useRef} from 'react';
import './forsic-pane-separator.css';

interface Props {
  label: string;
  controls: string;
  pane: 'left' | 'right';
  width: number;
  min: number;
  max: number;
  onResize: (width: number) => void;
  onReset: () => void;
}

export function ForsicPaneSeparator({label, controls, pane, width, min, max, onResize, onReset}: Props) {
  const drag = useRef<{id: number; x: number; width: number; target: HTMLDivElement} | null>(null);
  const stop = () => {
    const current = drag.current;
    drag.current = null;
    if (current?.target.hasPointerCapture?.(current.id)) current.target.releasePointerCapture(current.id);
  };
  useEffect(() => {
    window.addEventListener('blur', stop);
    window.addEventListener('resize', stop);
    return () => {window.removeEventListener('blur', stop); window.removeEventListener('resize', stop); stop();};
  }, []);
  return <div className="forsic-pane-separator" data-pane={pane} role="separator" tabIndex={0}
    aria-label={label} aria-controls={controls} aria-orientation="vertical" aria-valuenow={width} aria-valuemin={min} aria-valuemax={max}
    title={`${label} · 방향키로 조절 · Home 또는 두 번 클릭으로 초기화`}
    onPointerDown={event => {
      if (event.button !== 0 || drag.current) return;
      event.preventDefault();
      event.currentTarget.focus();
      event.currentTarget.setPointerCapture(event.pointerId);
      drag.current = {id: event.pointerId, x: event.clientX, width, target: event.currentTarget};
    }}
    onPointerMove={event => {
      const current = drag.current;
      if (current?.id === event.pointerId) onResize(current.width + (event.clientX - current.x) * (pane === 'left' ? 1 : -1));
    }}
    onPointerUp={stop} onPointerCancel={stop} onLostPointerCapture={stop} onDoubleClick={onReset}
    onKeyDown={event => {
      if (event.key === 'Home') {event.preventDefault(); onReset();}
      else if (event.key === 'ArrowLeft' || event.key === 'ArrowRight') {
        event.preventDefault();
        onResize(width + (event.key === 'ArrowRight' ? 1 : -1) * (pane === 'left' ? 1 : -1) * (event.shiftKey ? 40 : 10));
      }
    }} />;
}
