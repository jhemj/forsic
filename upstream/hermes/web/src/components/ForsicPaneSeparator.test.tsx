// @vitest-environment jsdom
import {act, useRef} from 'react';
import {createRoot, type Root} from 'react-dom/client';
import {afterEach, beforeEach, expect, it, vi} from 'vitest';
import {ForsicPaneSeparator} from './ForsicPaneSeparator';
import {useForsicAvailableWidth, useForsicPaneWidth} from '@/hooks/useForsicPaneWidth';

let root: Root;
let container: HTMLDivElement;
let capture: number | null;
function Harness({pane = 'left', max = 640, defaultWidth = 432}: {pane?: 'left' | 'right'; max?: number; defaultWidth?: number}) {
  const state = useForsicPaneWidth({storageKey: 'test-pane', defaultWidth, min: 240, max, storedMin: 240, storedMax: 900});
  return <ForsicPaneSeparator label="Pane width" controls="pane" pane={pane} {...state} onResize={state.setWidth} onReset={state.reset}/>;
}
function separator() {return container.querySelector('[role=separator]') as HTMLDivElement;}
function width() {return Number(separator().getAttribute('aria-valuenow'));}
async function pointer(type: string, x: number, id = 1) {
  const event = new Event(type, {bubbles: true});
  Object.assign(event, {pointerId: id, clientX: x, button: 0});
  await act(async () => separator().dispatchEvent(event));
}
async function key(key: string, shiftKey = false) {
  await act(async () => separator().dispatchEvent(new KeyboardEvent('keydown', {key, shiftKey, bubbles: true})));
}
beforeEach(() => {
  Object.assign(globalThis, {IS_REACT_ACT_ENVIRONMENT: true});
  vi.useFakeTimers(); localStorage.clear(); capture = null;
  Object.defineProperties(HTMLElement.prototype, {
    setPointerCapture: {configurable: true, value: (id: number) => {capture = id;}},
    hasPointerCapture: {configurable: true, value: (id: number) => capture === id},
    releasePointerCapture: {configurable: true, value: () => {capture = null;}},
  });
  container = document.createElement('div'); document.body.append(container); root = createRoot(container);
});
afterEach(async () => {await act(async () => root.unmount()); container.remove(); vi.restoreAllMocks(); vi.unstubAllGlobals(); vi.useRealTimers();});

it.each(['left', 'right'] as const)('drags the %s pane in its own direction, clamps bounds and stops on cancellation', async pane => {
  await act(async () => root.render(<Harness pane={pane}/>));
  expect(separator().getAttribute('aria-orientation')).toBe('vertical');
  expect(separator().getAttribute('aria-valuemin')).toBe('240');
  expect(separator().getAttribute('aria-valuemax')).toBe('640');
  await pointer('pointerdown', 500); expect(capture).toBe(1);
  await pointer('pointermove', 550); expect(width()).toBe(pane === 'left' ? 482 : 382);
  await pointer('pointermove', pane === 'left' ? 2000 : -2000); expect(width()).toBe(640);
  await pointer('pointermove', pane === 'left' ? -2000 : 2000); expect(width()).toBe(240);
  await pointer('pointercancel', 500); expect(capture).toBeNull();
  await pointer('pointermove', 550); expect(width()).toBe(240);
});
it('supports keyboard direction, larger steps and reset through Home or double click', async () => {
  await act(async () => root.render(<Harness pane="right"/>));
  await key('ArrowLeft'); expect(width()).toBe(442);
  await key('ArrowRight', true); expect(width()).toBe(402);
  await key('Home'); expect(width()).toBe(432);
  await key('ArrowLeft');
  await act(async () => separator().dispatchEvent(new MouseEvent('dblclick', {bubbles: true})));
  expect(width()).toBe(432);
});
it.each(['"500"', 'null', '{}', '-20', '999999', 'broken'])('ignores unsafe stored width %s', async stored => {
  localStorage.setItem('test-pane', stored);
  await act(async () => root.render(<Harness/>)); expect(width()).toBe(432);
});
it('retains preference across viewport clamps, persists interaction and restores on reload', async () => {
  localStorage.setItem('test-pane', '580');
  await act(async () => root.render(<Harness max={350}/>)); expect(width()).toBe(350);
  await act(async () => vi.advanceTimersByTime(250)); expect(localStorage.getItem('test-pane')).toBe('580');
  await act(async () => root.render(<Harness max={640}/>)); expect(width()).toBe(580);
  await key('ArrowLeft'); expect(width()).toBe(570);
  expect(localStorage.getItem('test-pane')).toBe('580');
  await act(async () => vi.advanceTimersByTime(250)); expect(localStorage.getItem('test-pane')).toBe('570');
  await act(async () => root.render(null));
  await act(async () => root.render(<Harness/>)); expect(width()).toBe(570);
  await key('Home');
  await act(async () => vi.advanceTimersByTime(250)); expect(localStorage.getItem('test-pane')).toBeNull();
});
it('stops dragging on viewport change, lost capture and unmount', async () => {
  await act(async () => root.render(<Harness/>));
  await pointer('pointerdown', 500);
  await act(async () => window.dispatchEvent(new Event('resize')));
  await pointer('pointermove', 600); expect(width()).toBe(432);
  await pointer('pointerdown', 500); await pointer('lostpointercapture', 500);
  await pointer('pointermove', 600); expect(width()).toBe(432);
  await pointer('pointerdown', 500);
  await act(async () => root.render(null)); expect(capture).toBeNull();
});
it('remeasures workspace changes, preserves chat minimum and disconnects on unmount', async () => {
  let measured = 1100;
  let notify: () => void = () => {};
  const disconnect = vi.fn();
  vi.stubGlobal('ResizeObserver', class {constructor(callback: () => void) {notify = callback;} observe() {} disconnect = disconnect;});
  vi.spyOn(HTMLElement.prototype, 'getBoundingClientRect').mockImplementation(() => ({width: measured}) as DOMRect);
  function MeasuredPane() {
    const ref = useRef<HTMLDivElement>(null);
    const available = useForsicAvailableWidth(ref);
    return <div ref={ref}><Harness max={Math.min(900, available - 366)} defaultWidth={(available - 6) / 2}/></div>;
  }
  await act(async () => root.render(<MeasuredPane/>)); expect(width()).toBe(547);
  measured = 660; await act(async () => notify()); expect(width()).toBe(294);
  expect(measured - width() - 6).toBe(360);
  await act(async () => root.render(null)); expect(disconnect).toHaveBeenCalled();
});
