import {useEffect, useId, useMemo, useState} from 'react'
import {ChevronLeft, ChevronRight, Minus, Plus, RotateCcw} from 'lucide-react'
import type {InvestigationTime} from '@/lib/forsic-investigation'
import {displayTitle, recordKey, timelineMark, type TimelineMark} from './forsic-monitor'

interface Props {rows: InvestigationTime[]; selectedKey?: string; onSelect: (row: InvestigationTime) => void; onVisibleRows: (keys: string[] | null) => void}
const label = (instant: number, localClock = false, dayOnly = false) => {const iso = new Date(instant).toISOString(); return dayOnly ? iso.slice(0, 10) : iso.replace('T', ' ').replace('.000Z', localClock ? '' : 'Z')}
function extent(marks: TimelineMark[]) {
  const times = marks.flatMap(mark => mark.spans.flatMap(span => [span.start, span.end].filter((value): value is number => value !== null)))
  const min = Math.min(...times), max = Math.max(...times)
  return min === max ? [min - 60_000, max + 60_000] : [min, max]
}
function intersects(mark: TimelineMark, start: number, end: number) {return mark.spans.some(span => (span.end ?? Infinity) >= start && (span.start ?? -Infinity) <= end)}
export function ForsicTimeline({rows, selectedKey, onSelect, onVisibleRows}: Props) {
  const hatch = useId().replace(/:/g, '')
  const marks = useMemo(() => rows.map(timelineMark).filter((mark): mark is TimelineMark => mark !== null), [rows])
  const domains = [...new Set(marks.map(mark => mark.domain))]
  const lanes = [...new Set(marks.map(mark => mark.lane))]
  const [range, setRange] = useState<[number, number]>([0, 100])
  const [hidden, setHidden] = useState<string[]>([])
  const filtering = range[0] !== 0 || range[1] !== 100 || hidden.length > 0
  const visible = marks.filter(mark => {
    const [min, max] = extent(marks.filter(item => item.domain === mark.domain))
    return !hidden.includes(mark.lane) && intersects(mark, min + (max - min) * range[0] / 100, min + (max - min) * range[1] / 100)
  })
  const signature = JSON.stringify(filtering ? visible.map(mark => recordKey(mark.row)) : null)
  useEffect(() => {onVisibleRows(JSON.parse(signature) as string[] | null)}, [signature, onVisibleRows])
  const zoom = (factor: number) => setRange(([a, b]) => {const width = Math.min(100, Math.max(1, (b - a) * factor)); const start = Math.max(0, Math.min(100 - width, (a + b - width) / 2)); return [start, start + width]})
  const pan = (direction: number) => setRange(([a, b]) => {const width = b - a, start = Math.max(0, Math.min(100 - width, a + width * .25 * direction)); return [start, start + width]})
  return <div className="fi-timeline-workspace">
    <div className="fi-chart-toolbar"><div><span className="fi-eyebrow">시각 비교</span><strong>근거가 남긴 시간</strong></div><div className="fi-chart-actions"><button type="button" aria-label="이전 시간 범위" onClick={() => pan(-1)} disabled={range[0] === 0}><ChevronLeft size={14}/></button><button type="button" aria-label="시간축 확대" onClick={() => zoom(.5)}><Plus size={14}/></button><button type="button" aria-label="시간축 축소" onClick={() => zoom(2)}><Minus size={14}/></button><button type="button" aria-label="다음 시간 범위" onClick={() => pan(1)} disabled={range[1] === 100}><ChevronRight size={14}/></button><button type="button" aria-label="시간 필터 초기화" onClick={() => {setRange([0, 100]); setHidden([])}}><RotateCcw size={13}/></button></div></div>
    <p className="fi-caption">명시된 시각만 배치합니다. 기록 시각의 관측은 사건 발생의 확정을 뜻하지 않아요. 출처 내부 달력과 서로 다른 시계는 축을 분리합니다.</p>
    {marks.length > 0 ? <><div className="fi-lane-filters" aria-label="시간 레인 선택">{lanes.map(lane => <button type="button" key={lane} aria-pressed={!hidden.includes(lane)} onClick={() => setHidden(previous => previous.includes(lane) ? previous.filter(item => item !== lane) : [...previous, lane])}><span className={lane.startsWith('추정') ? 'fi-legend-interval' : 'fi-legend-dot'}/>{lane}</button>)}</div>
    <div className="fi-range-controls"><label>범위 시작<input aria-label="시간 범위 시작" type="range" min={0} max={99} value={range[0]} onChange={event => setRange([Math.min(Number(event.target.value), range[1] - 1), range[1]])}/></label><label>범위 끝<input aria-label="시간 범위 끝" type="range" min={1} max={100} value={range[1]} onChange={event => setRange([range[0], Math.max(Number(event.target.value), range[0] + 1)])}/></label><output>{Math.round(range[1] - range[0])}% 범위</output></div>
    {domains.map(domain => {
      const domainMarks = marks.filter(mark => mark.domain === domain), [min, max] = extent(domainMarks)
      const domainLabel = domainMarks[0].domainLabel
      const localClock = domainMarks[0].localClock, dayOnly = domainMarks.every(mark => mark.precision === 'day')
      const start = min + (max - min) * range[0] / 100, end = min + (max - min) * range[1] / 100
      const domainLanes = lanes.filter(lane => !hidden.includes(lane) && domainMarks.some(mark => mark.lane === lane))
      const x = (time: number) => 144 + (time - start) / (end - start) * 620
      const height = 62 + domainLanes.length * 55
      return <section className="fi-clock-domain" key={domain} aria-label={`시간축 · ${domainLabel}`}><header><span>{domainLabel}</span><small>{visible.filter(mark => mark.domain === domain).length} / {domainMarks.length}건</small></header><div className="fi-chart-scroll"><svg className="fi-time-chart" viewBox={`0 0 790 ${height}`} role="group" aria-label={`${domainLabel}의 선택 가능한 시간 기록`}>
        <defs><pattern id={`${hatch}-${domains.indexOf(domain)}`} width="6" height="6" patternUnits="userSpaceOnUse" patternTransform="rotate(45)"><line x1="0" x2="0" y1="0" y2="6" stroke="currentColor" strokeWidth="2"/></pattern></defs>
        {(dayOnly && end - start <= 86_400_000 ? [0, 1] : [0, .25, .5, .75, 1]).map(tick => <g key={tick} className="fi-time-grid"><line x1={x(start + (end - start) * tick)} x2={x(start + (end - start) * tick)} y1={14} y2={height - 32}/><text x={x(start + (end - start) * tick)} y={height - 12} textAnchor={tick === 0 ? 'start' : tick === 1 ? 'end' : 'middle'}>{dayOnly ? label(start + (end - start) * tick, localClock, true) : new Date(start + (end - start) * tick).toISOString().slice(end - start >= 86_400_000 ? 5 : 11, end - start >= 86_400_000 ? 16 : 19).replace('T', ' ')}</text></g>)}
        {domainLanes.map((lane, index) => <g key={lane}><text className="fi-lane-label" x={10} y={38 + index * 55}>{lane}</text><line className="fi-lane-line" x1={144} x2={764} y1={34 + index * 55} y2={34 + index * 55}/></g>)}
        {visible.filter(mark => mark.domain === domain).map(mark => {const y = 34 + domainLanes.indexOf(mark.lane) * 55; const selected = recordKey(mark.row) === selectedKey; return <g key={recordKey(mark.row)} role="button" tabIndex={0} aria-label={`시간 기록 선택 · ${displayTitle(mark.row, mark.row.explanation)}`} aria-pressed={selected} data-lane={mark.row.time_kind} data-local-clock={mark.localClock || undefined} data-precision={mark.precision} className={`fi-chart-mark${selected ? ' is-selected' : ''}`} data-time-id={mark.row.id} onClick={() => onSelect(mark.row)} onKeyDown={event => {if (event.key === 'Enter' || event.key === ' ') {event.preventDefault(); onSelect(mark.row)}}}><title>{displayTitle(mark.row, mark.row.explanation)} · {mark.localClock ? '시간대 미상 · 출처 내부 비교' : '명시된 시간대'}{mark.precision === 'day' ? ' · 날짜 단위' : ''}</title>{mark.spans.filter(span => (span.end ?? Infinity) >= start && (span.start ?? -Infinity) <= end).map((span, index) => (mark.inferred || mark.precision === 'day') ? <g key={index} data-inferred-range={mark.inferred || undefined} data-day-precision={mark.precision === 'day' || undefined}><rect x={x(Math.max(start, span.start ?? start))} y={y - 9 + index * 3} width={Math.max(3, x(Math.min(end, span.end ?? end)) - x(Math.max(start, span.start ?? start)))} height={18} fill={mark.inferred ? `url(#${hatch}-${domains.indexOf(domain)})` : 'currentColor'} rx={2}/><path d={`M${x(Math.max(start, span.start ?? start))},${y - 11}v22`} strokeDasharray={span.start === null ? '2 3' : undefined}/><path d={`M${x(Math.min(end, span.end ?? end))},${y - 11}v22`} strokeDasharray={span.end === null ? '2 3' : undefined}/></g> : <g key={index}><circle className="fi-mark-halo" cx={x(span.start!)} cy={y} r={12}/><circle cx={x(span.start!)} cy={y} r={selected ? 6 : 4.5}/></g>)}</g>})}
      </svg></div><footer><time>{label(start, localClock, dayOnly)}</time><time>{label(end, localClock, dayOnly)}</time></footer></section>
    })}<p className="fi-chart-status" role="status">축 위 {visible.length} / {marks.length}건 · 축 밖 {rows.length - marks.length}건{filtering ? ' · 범위 필터 적용' : ''}</p></> : <div className="fi-empty"><p>축에 배치할 수 있는 시간 기록이 없어요.</p><small>연도·시간대·시계 기준이 부족한 기록은 아래에 보존해요. 자료에 시각이 없는 것과는 다릅니다.</small></div>}
  </div>
}
