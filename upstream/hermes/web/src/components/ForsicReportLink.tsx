import {useEffect, useRef, useState} from 'react';
import {createPortal} from 'react-dom';
import {Download, ExternalLink, X} from 'lucide-react';
import {authedFetch} from '@/lib/api';
import type {InvestigationReportFile} from '@/lib/forsic-investigation';
import './ForsicReportLink.css';

interface Props {
  file: InvestigationReportFile;
  profile?: string;
  label?: string;
}

function downloadName(disposition: string | null, fallback: string): string {
  const encoded = disposition?.match(/filename\*=UTF-8''([^;]+)/i)?.[1];
  let name = disposition?.match(/filename="([^"]+)"|filename=([^;]+)/i)?.slice(1).find(Boolean)?.trim() || fallback;
  if (encoded) {
    try {name = decodeURIComponent(encoded);} catch { /* A malformed name does not prevent downloading the file. */ }
  }
  return Array.from(name, character => /[\\/]/.test(character) || character.charCodeAt(0) < 32 || character.charCodeAt(0) === 127 ? '_' : character).join('');
}

export function ForsicReportLink({file, profile, label}: Props) {
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState('');
  const [preview, setPreview] = useState<{html: string; downloadUrl: string; name: string} | null>(null);
  const trigger = useRef<HTMLButtonElement>(null);
  const close = useRef<HTMLButtonElement>(null);
  const request = useRef<AbortController | null>(null);
  const blobUrl = useRef('');
  const pending = useRef(false);
  const word = file.method === 'POST';
  const html = !word && file.name.toLowerCase().endsWith('.html');

  useEffect(() => {
    setBusy(false); setError(''); setPreview(null); pending.current = false;
    return () => {
      request.current?.abort(); request.current = null; pending.current = false;
      if (blobUrl.current) URL.revokeObjectURL(blobUrl.current);
      blobUrl.current = '';
    };
  }, [file.url, file.name, file.method, profile]);

  useEffect(() => {
    if (!preview) return;
    close.current?.focus();
    const button = trigger.current;
    const overflow = document.body.style.overflow;
    document.body.style.overflow = 'hidden';
    return () => {document.body.style.overflow = overflow; button?.focus();};
  }, [preview]);

  function closePreview() {
    setPreview(null);
    if (blobUrl.current) URL.revokeObjectURL(blobUrl.current);
    blobUrl.current = '';
  }

  async function open() {
    if (pending.current) return;
    pending.current = true; setBusy(true); setError('');
    const controller = new AbortController(); request.current = controller;
    try {
      if (word) {
        if (file.name !== 'integrated.docx' || !/^\/api\/plugins\/forsic\/report-bundles\/[a-f0-9]{64}\/word(?:\?[^#]*)?$/.test(file.url)) throw new Error('Invalid Word export descriptor');
      } else if (file.method !== undefined || !/^\/api\/plugins\/forsic\/(?:report-bundles|download|reports)\//.test(file.url)) throw new Error('Invalid report URL');
      const [path, query = ''] = file.url.split('?');
      const params = new URLSearchParams(query);
      if (profile) params.set('profile', profile);
      const response = await authedFetch(`${path}?${params}`, {signal: controller.signal, ...(word ? {method: 'POST'} : {})});
      if (controller.signal.aborted || request.current !== controller) return;
      if (!response.ok) {
        setError(response.status === 401 ? '보고서 접근 인증이 만료됐어요. 화면을 새로고침한 뒤 다시 열어 주세요.' : word ? 'Word 변환에 실패했어요. 다시 눌러 시도해 주세요.' : '보고서를 열지 못했어요. 잠시 뒤 다시 시도해 주세요.');
        return;
      }
      if (word && !/^application\/(?:vnd\.openxmlformats-officedocument\.wordprocessingml\.document|octet-stream)(?:;|$)/i.test(response.headers.get('Content-Type') || '')) throw new Error('Invalid Word response');
      const blob = await response.blob();
      if (word && blob.size === 0) throw new Error('Empty Word response');
      if (controller.signal.aborted) return;
      const content = html ? await blob.text() : '';
      if (controller.signal.aborted) return;
      if (blobUrl.current) URL.revokeObjectURL(blobUrl.current);
      blobUrl.current = URL.createObjectURL(blob);
      const name = downloadName(response.headers.get('Content-Disposition'), file.name);
      if (html) {
        // srcDoc needs its own restrictive policy; the original Blob remains the download.
        const policy = `<meta http-equiv="Content-Security-Policy" content="default-src 'none'; style-src 'unsafe-inline'; img-src data: blob:; base-uri 'none'; form-action 'none'">`;
        setPreview({html: policy + content, downloadUrl: blobUrl.current, name});
      }
      else {
        const anchor = document.createElement('a');
        anchor.href = blobUrl.current;
        anchor.download = name;
        document.body.append(anchor); anchor.click(); anchor.remove();
      }
    } catch {
      if (!controller.signal.aborted && request.current === controller) setError(word ? 'Word 변환에 실패했어요. 연결을 확인하고 다시 눌러 시도해 주세요.' : '보고서를 열지 못했어요. 연결을 확인하고 다시 시도해 주세요.');
    } finally {
      if (request.current === controller) {request.current = null; pending.current = false; if (!controller.signal.aborted) setBusy(false);}
    }
  }

  return <>
    <button ref={trigger} type="button" className="forsic-report-file" disabled={busy} onClick={() => void open()}><span>{busy ? word ? 'Word 변환 중…' : '보고서를 불러오고 있어요…' : label || file.name}</span>{html ? <ExternalLink size={13} aria-hidden="true"/> : <Download size={13} aria-hidden="true"/>}</button>
    {error && <p className="forsic-report-file-error" role="status">{error}</p>}
    {preview && createPortal(<div className="forsic-report-reader" role="dialog" aria-modal="true" aria-label={file.name} onKeyDown={event => {if (event.key === 'Escape') {event.stopPropagation(); closePreview();}}}>
      <header><strong>{file.name}</strong><a href={preview.downloadUrl} download={preview.name}>HTML 내려받기</a><button ref={close} type="button" onClick={closePreview} aria-label="보고서 닫기"><X size={18}/></button></header>
      <iframe title={file.name} srcDoc={preview.html} sandbox=""/>
    </div>, document.body)}
  </>;
}
