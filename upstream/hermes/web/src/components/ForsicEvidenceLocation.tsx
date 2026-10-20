import {useEffect, useState} from 'react'
import {FolderOpen} from 'lucide-react'
import {fetchJSON} from '@/lib/api'

interface EvidenceLocation {display_path?: string; available?: boolean; restricted?: boolean}

/** Present the configured share name; server filesystem paths stay in API transport. */
export function ForsicEvidenceLocation() {
  const [location, setLocation] = useState<EvidenceLocation | null>(null)
  useEffect(() => {
    let active = true
    void fetchJSON<EvidenceLocation>('/api/plugins/forsic/evidence-sources')
      .then(result => {if (active) setLocation(result)})
      .catch(() => {if (active) setLocation(null)})
    return () => {active = false}
  }, [])
  if (!location?.display_path) return null
  return <aside className="forsic-evidence-location" aria-label="기본 조사 자료 위치">
    <FolderOpen size={20} aria-hidden="true" />
    <div><strong>기본 자료 위치</strong><span>{location.display_path}</span>
      {location.available === false ? <p role="status">공유 폴더에 연결할 수 없습니다. 서버의 자료 연결 상태를 확인해 주세요.</p> : location.restricted && <p>이 공유 폴더 안의 자료를 선택할 수 있습니다.</p>}
    </div>
  </aside>
}
