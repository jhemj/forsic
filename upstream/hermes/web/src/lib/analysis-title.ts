/** Stable within a case: activity reordering never changes a conversation's ordinal. */
export function analysisTitle(item: {session_ids:string[];conversations?:{id:string;session_ids:string[]}[]}, session:string): string {
  const ordered=[...(item.conversations || [])].sort((a,b)=>item.session_ids.indexOf(a.id)-item.session_ids.indexOf(b.id));
  const index=ordered.findIndex(c=>c.session_ids.includes(session));
  if(index<0)return "분석";
  return `${["첫", "두", "세", "네", "다섯", "여섯", "일곱", "여덟", "아홉", "열"][index] || index+1} 번째 분석`;
}
