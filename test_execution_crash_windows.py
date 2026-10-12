"""Fault injection at existing Case.invoke commit boundaries; never resumes work."""
import json
import unittest
from unittest.mock import patch
import test_mission_native_tools as native_tests
from forsic_plugin.report_driven.host import current, find

class AbruptExit(BaseException):
    pass

class ExecutionCrashWindowTests(unittest.TestCase):
    def setUp(self):
        self.x=native_tests.MissionNativeToolsTests();self.x.setUp();self.addCleanup(self.x.tearDown)
        self.m=self.x.plan()
        self.args={'path':'request.log','mission_id':self.m['id'],'mission_version':self.m['version']}

    def check(self,state,result_count):
        s=current(self.x.case);m=find(s,'missions',self.m['id'])
        self.assertEqual(m['state'],state);self.assertEqual(len(m['result_ids']),result_count)
        self.assertEqual(len(m['execution_start_ids']),1)
        self.assertEqual(s['tests'][0]['assessment_state'],'unassessed')
        with patch.object(self.x.case,'read',side_effect=AssertionError('must not execute duplicate')):
            retry=json.loads(self.x.case.invoke('forsic_read',self.args,'synthetic-session',request_context={'goal_id':'synthetic-goal'}))
        self.assertIn('error',retry);self.assertTrue(retry['execution_rejected'])

    def test_exit_after_start_commit_before_handler(self):
        with patch.object(self.x.case,'read',side_effect=AbruptExit):
            with self.assertRaises(AbruptExit):self.x.call('forsic_read',self.args)
        self.check('running',0)

    def test_exit_after_handler_before_result_commit(self):
        with patch.object(self.x.case,'record',side_effect=AbruptExit):
            with self.assertRaises(AbruptExit):self.x.call('forsic_read',self.args)
        self.check('running',0)

    def test_exit_after_result_commit_before_delivery(self):
        original=self.x.case.record
        def record(*args,**kwargs):
            original(*args,**kwargs)
            raise AbruptExit
        with patch.object(self.x.case,'record',side_effect=record):
            with self.assertRaises(AbruptExit):self.x.call('forsic_read',self.args)
        self.check('unassessed',1)
