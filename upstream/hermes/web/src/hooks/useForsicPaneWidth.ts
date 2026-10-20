import {useEffect, useLayoutEffect, useState, type RefObject} from 'react';

export function clampPaneWidth(value: number, min: number, max: number) {
  return Math.round(Math.max(min, Math.min(Math.max(min, max), value)));
}

/** Measure the available workspace, including changes caused by the other pane. */
export function useForsicAvailableWidth(ref: RefObject<HTMLElement | null>) {
  const [width, setWidth] = useState(0);
  useLayoutEffect(() => {
    const element = ref.current;
    if (!element) return;
    const measure = () => setWidth(element.getBoundingClientRect().width);
    measure();
    const observer = typeof ResizeObserver === 'undefined' ? null : new ResizeObserver(measure);
    observer?.observe(element);
    window.addEventListener('resize', measure);
    return () => {observer?.disconnect(); window.removeEventListener('resize', measure);};
  }, [ref]);
  return width;
}

interface PaneWidthOptions {
  storageKey: string;
  defaultWidth: number;
  min: number;
  max: number;
  storedMin: number;
  storedMax: number;
}
export function useForsicPaneWidth({storageKey, defaultWidth, min, max, storedMin, storedMax}: PaneWidthOptions) {
  const [preferred, setPreferred] = useState<number | null>(() => {
    try {
      const raw = localStorage.getItem(storageKey);
      const value: unknown = raw === null ? null : JSON.parse(raw);
      return typeof value === 'number' && Number.isFinite(value) && value >= storedMin && value <= storedMax ? value : null;
    } catch {return null;}
  });
  useEffect(() => {
    const timer = window.setTimeout(() => {
      try {
        if (preferred === null) localStorage.removeItem(storageKey);
        else localStorage.setItem(storageKey, JSON.stringify(preferred));
      } catch { /* Resizing still works when browser storage is unavailable. */ }
    }, 200);
    return () => window.clearTimeout(timer);
  }, [preferred, storageKey]);
  return {
    width: clampPaneWidth(preferred ?? defaultWidth, min, max),
    min,
    max: Math.max(min, max),
    setWidth: (value: number) => setPreferred(clampPaneWidth(value, min, max)),
    reset: () => setPreferred(null),
  };
}
