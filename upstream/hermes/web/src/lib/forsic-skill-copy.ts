/** UI-only copy. Never pass translated labels to the native skill loader. */
export interface ForsicSkillLocale {
  schema: 'forsic.ui.skill-copy.v1'; locale: 'ko';
  skills: Record<string, {display_name: string; description: string; category: string; search_terms: string[]}>;
}
export function forsicSkillCopy(item: {name: string; description?: string}, locale?: ForsicSkillLocale | null, namespace = '') {
  const prefix = 'forsic:';
  const id = item.name.startsWith(prefix) ? item.name.slice(prefix.length) : item.name;
  const trusted = item.name.startsWith(prefix) || namespace === 'forsic' && !item.name.includes(':');
  const copy = trusted && locale?.schema === 'forsic.ui.skill-copy.v1' && locale.locale === 'ko' ? locale.skills[id] : undefined;
  return {
    id: item.name, label: copy?.display_name ?? item.name,
    description: copy?.description ?? '한국어 설명 미등록', category: copy?.category ?? '',
    searchText: [item.name, item.description, copy?.display_name, copy?.description, copy?.category, ...(copy?.search_terms ?? [])].filter(Boolean).join(' '),
    translated: Boolean(copy),
  };
}
