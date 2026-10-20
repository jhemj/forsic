import {describe, expect, it} from 'vitest';
import {forsicSkillCopy, type ForsicSkillLocale} from './forsic-skill-copy';
const locale: ForsicSkillLocale = {schema: 'forsic.ui.skill-copy.v1', locale: 'ko', skills: {
  'scan-sensitive-data': {display_name: '민감정보 탐지', description: '자격증명과 개인정보를 찾습니다.', category: '탐지', search_terms: ['비밀번호']},
}};
describe('Forsic UI skill copy', () => {
  const item = {name: 'scan-sensitive-data', description: 'Find sensitive data'};
  it('preserves exact native ID and indexes Korean and English', () => {
    const copy = forsicSkillCopy(item, locale, 'forsic');
    expect(copy.id).toBe(item.name);
    expect(copy.label).toBe('민감정보 탐지');
    for (const term of ['비밀번호', '자격증명', item.name, item.description]) expect(copy.searchText).toContain(term);
  });
  it('never translates an unverified bare ID or another namespace', () => {
    expect(forsicSkillCopy(item, locale).translated).toBe(false);
    expect(forsicSkillCopy({...item, name: 'external:scan-sensitive-data'}, locale, 'forsic').translated).toBe(false);
    expect(forsicSkillCopy({...item, name: 'forsic:scan-sensitive-data'}, locale).translated).toBe(true);
  });
  it('uses explicit Korean fallback without automatic translation', () => {
    const copy = forsicSkillCopy(item, null, 'forsic');
    expect(copy.label).toBe(item.name);
    expect(copy.description).toBe('한국어 설명 미등록');
    expect(copy.description).not.toContain(item.description);
  });
});
