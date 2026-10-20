"""Selected forensic declarations reach actual reports without changing answers."""
from copy import deepcopy
import json
from pathlib import Path
import unittest
from zipfile import ZipFile

import test_forensic_workbench_writer as fixtures
from forsic_plugin.report_driven import host
from forsic_plugin.report_driven.views import bundle
from forsic_plugin.report_driven.word_export import export_word


class WorkbenchViewsTests(unittest.TestCase):
    setUp=fixtures.ForensicWorkbenchWriterTests.setUp
    call=fixtures.ForensicWorkbenchWriterTests.call
    save=fixtures.ForensicWorkbenchWriterTests.save
    judgment=fixtures.ForensicWorkbenchWriterTests.judgment
    revise=fixtures.ForensicWorkbenchWriterTests.revise
    grounded=fixtures.ForensicWorkbenchWriterTests.grounded

    def test_absence_scope_lineage_and_selected_check_reach_saved_html_and_word(self):
        q=self.grounded();state=host.current(self.case)
        basis=next(r for r in q['forensic']['basis'] if r['kind']=='source')
        forensic={'absence':[{'declared_scope':'선택한 요청 기록에서만 완료 결과가 미확인',
            'source_refs':[basis],'query_or_decoder':'반환된 요청 줄을 읽음',
            'excluded_scope':['다른 계정과 미수집 로그'],'limitation':'전체 시스템 부재를 뜻하지 않음'}],
            'checks':[{'selected_link':'요청과 완료의 연결','competing_explanation':'실행 전에 취소됨',
                'discriminating_observation':'같은 실행 번호의 완료 기록','feasible_next_check':'선택한 처리 로그 대조',
                'expected_decision_change':'완료 기록이 연결되면 실행 여부 판단을 갱신','disposition':'planned'}]}
        q=self.revise(q,'selected-workbench-report',forensic=forensic)
        state=host.current(self.case);before=deepcopy(state)
        rendered=bundle(self.case,state,question_ids=[q['id']])
        original_manifest=Path(rendered['manifest']).read_bytes()
        manifest=json.loads(original_manifest)
        self.assertFalse(any(Path(entry).suffix=='.docx' for entry in rendered['files']))
        html=next(Path(entry) for entry in rendered['files'] if Path(entry).name=='integrated.html')
        word=export_word(self.case,rendered['bundle_id'])
        self.assertEqual(word['snapshot_id'],manifest['snapshot_id'])
        self.assertEqual(Path(rendered['manifest']).read_bytes(),original_manifest)
        for path in (html,Path(word['path'])):
            if path.suffix=='.html':text=path.read_text()
            else:
                with ZipFile(path) as archive:text=archive.read('word/document.xml').decode()
            for phrase in ('부재 판단의 선언 범위','실제 관측 범위','전체 시스템 부재를 뜻하지 않음',
                           '관측된 부모','요청과 완료의 연결','계획'):
                self.assertIn(phrase,text)
        self.assertEqual(host.current(self.case)['questions'],before['questions'])
        self.assertEqual(q['answer'],host.find(before,'questions',q['id'])['answer'])
        self.assertFalse(q['forensic_context']['semantic_approval'])


if __name__=='__main__':unittest.main()
