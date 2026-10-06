"""Send explicitly finalized report copies through the existing case-topic bot.

No evidence, attachments named in chat, old bundles or drafts are discovered.
The durable local receipts suppress retries after unknown network outcomes.
"""
import hashlib
import json
from pathlib import Path
import time
from types import SimpleNamespace

from forsic_plugin.intake import write_json
from forsic_plugin.investigations import readonly
from forsic_plugin.report_driven.finalization import REPORT_NAMES, report_files

MAX_DOCUMENT_BYTES = 49_000_000


class ReportDelivery:
    def __init__(self, path, output, case_id, chat_id, thread_id, since, manifest=None):
        self.path, self.output = Path(path), Path(output)
        self.binding = {'case_id': case_id, 'chat_id': str(chat_id), 'thread_id': int(thread_id)}
        config=json.loads(Path(manifest or self.output.parent/'case.json').read_text())
        if config.get('case_id')!=case_id or Path(config['output_root']).resolve()!=self.output.resolve():
            raise ValueError('Report state does not belong to the bound case')
        # current()/bundle_stale only need these fields and read SQLite in RO mode.
        # Do not construct Case here: its constructor creates the results table.
        self.case=SimpleNamespace(config=config,output=self.output,db=self.output/'activity.sqlite3')
        self.state = json.loads(self.path.read_text()) if self.path.exists() else {
            **self.binding, 'since': since, 'seen_events': [], 'jobs': {}}
        if any(self.state.get(k) != v for k, v in self.binding.items()):
            raise ValueError('Report delivery target differs from the configured case topic')
        # A lost process cannot prove whether Telegram accepted its request.
        for job in self.state['jobs'].values():
            if job['status'] == 'sending':
                job['status'] = 'delivery_unconfirmed'
        self.save()

    def save(self):
        write_json(self.path, self.state)

    def discover(self):
        with readonly(self.output / 'activity.sqlite3') as db:
            rows = db.execute("SELECT id,time,data FROM events WHERE kind='report_finalized' AND time>=? ORDER BY rowid",
                              (self.state['since'],)).fetchall()
        for row in rows:
            if row['id'] in self.state['seen_events']:
                continue
            data = json.loads(row['data'])
            try:
                if data.get('case_id') != self.binding['case_id'] or data.get('selection') != 'final_delivery_copy':
                    raise ValueError('Final report identity is invalid')
                for job in self.state['jobs'].values():
                    if job['status']=='queued' and job['finalization_receipt']!=row['id']:
                        job.update(status='superseded',superseded_by=row['id'])
                self.state['latest_finalization_receipt']=row['id']
                # A newer selected copy replaces the old delivery intent even
                # if the new files have since become unavailable or corrupted.
                _, files = report_files(self.output, data['bundle_id'], data['case_id'], data['snapshot_id'])
                if files != data['files']:
                    raise ValueError('Final report files differ from the selection receipt')
                for file in files:
                    # Re-finalizing the same logical file/hash does not resend it.
                    key = hashlib.sha256((file['name'] + ':' + file['sha256']).encode()).hexdigest()
                    if key not in self.state['jobs'] or self.state['jobs'][key]['status'] in ('queued','stale','superseded'):
                        self.state['jobs'][key] = {**file, 'bundle_id': data['bundle_id'],
                        'snapshot_id': data['snapshot_id'], 'finalization_receipt': row['id'],
                        'status': 'queued', 'queued_at': time.time()}
            except (ValueError, KeyError, OSError, TypeError) as exc:
                self.state.setdefault('rejected_events', {})[row['id']] = {'error_type': type(exc).__name__}
            self.state['seen_events'].append(row['id'])
            self.save()

    async def tick(self, send_document):
        self.discover()
        for key in list(self.state['jobs']):
            # A correction/new finalization during the previous upload must stop
            # remaining old files. Already sent/unknown attempts remain untouched.
            self.discover()
            job=self.state['jobs'][key]
            if job['status'] != 'queued':
                continue
            try:
                if job['name'] not in REPORT_NAMES or job['bytes'] > MAX_DOCUMENT_BYTES:
                    raise ValueError('Report file is not supported by this delivery channel')
                manifest, files = report_files(self.output, job['bundle_id'], self.binding['case_id'], job['snapshot_id'])
                from forsic_plugin.report_driven.views import bundle_stale
                if bundle_stale(self.case,manifest):
                    job.update(status='stale',reason='report_material_changed')
                    self.save()
                    continue
                current = next(f for f in files if f['name'] == job['name'])
                if any(current[k] != job[k] for k in ('sha256', 'bytes')):
                    raise ValueError('Queued report file changed')
                path = self.output / ('report-bundle-' + job['bundle_id']) / job['name']
                payload = path.read_bytes()
                if len(payload) != job['bytes'] or hashlib.sha256(payload).hexdigest() != job['sha256']:
                    raise ValueError('Report changed before upload')
            except (ValueError, KeyError, OSError, TypeError, StopIteration) as exc:
                job.update(status='blocked', error_type=type(exc).__name__)
                self.save()
                continue
            label = '임원용' if job['name'].startswith('executive.') else '실무자용'
            caption = '📄 조사 보고서 · ' + label + ' · ' + job['name'].rsplit('.', 1)[1].upper()
            job.update(status='sending', began_at=time.time())
            self.save()
            try:
                # Upload these checked bytes, never reopen a model-provided path.
                message_id = await send_document(job['name'], payload, caption)
            except Exception as exc:
                job.update(status='rejected' if type(exc).__name__ in ('BadRequest', 'Forbidden')
                           else 'delivery_unconfirmed', error_type=type(exc).__name__)
            else:
                job.update(status='sent', message_id=message_id, sent_at=time.time())
            self.save()
        return {status: sum(j['status'] == status for j in self.state['jobs'].values())
                for status in ('queued', 'sent', 'blocked', 'rejected', 'delivery_unconfirmed', 'stale', 'superseded')}
