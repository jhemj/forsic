"""Explicit Word download from immutable reader inputs, without case reanalysis."""
from contextlib import contextmanager
import fcntl
import hashlib
import io
import json
import os
from pathlib import Path
import re
import shutil
import stat
import tempfile
import zipfile

SCHEMA='forsic-word-export-1'
RENDERER_CONTRACT='forsic-retained-docx-1'
FORMATS='html-with-lazy-word-v1'
INPUT_NAMES={'view':'.word-view.json','template':'.word-template.docx','style':'.word-style.json'}
INPUT_LIMITS={'view':64*1024*1024,'template':16*1024*1024,'style':1024*1024}


def _encoded(value):
    return json.dumps(value,ensure_ascii=False,sort_keys=True,separators=(',',':')).encode()


def _sha(raw):return hashlib.sha256(raw).hexdigest()


def _renderer_hash():return _sha(Path(__file__).with_name('render_views.py').read_bytes())


def _directory(path, *, create=False):
    path=Path(path)
    if create:path.mkdir(mode=0o700,exist_ok=True)
    if path.is_symlink() or not path.is_dir():raise ValueError('Word export directory is not a regular directory')
    return path


def _read(path,limit):
    fd=os.open(path,os.O_RDONLY|os.O_NOFOLLOW|os.O_CLOEXEC)
    with os.fdopen(fd,'rb') as source:
        info=os.fstat(source.fileno())
        if not stat.S_ISREG(info.st_mode) or info.st_size>limit:raise ValueError('Invalid or oversized Word export file')
        raw=source.read(limit+1)
        if len(raw)>limit:raise ValueError('Oversized Word export file')
        return raw


def _write(path,raw):
    fd=os.open(path,os.O_WRONLY|os.O_CREAT|os.O_EXCL|os.O_NOFOLLOW,0o600)
    with os.fdopen(fd,'wb') as target:
        target.write(raw);target.flush();os.fsync(target.fileno())


def stage_inputs(directory,view):
    """Retain the final masked/local-only reader view, never source Python code."""
    from .render_views import STYLE,TEMPLATE_PATH
    root=_directory(directory)
    values={'view':_encoded(view),'template':TEMPLATE_PATH.read_bytes(),'style':_encoded(STYLE)}
    records={}
    for kind,raw in values.items():
        if len(raw)>INPUT_LIMITS[kind]:raise ValueError('Word render input exceeds retained limit')
        _write(root/INPUT_NAMES[kind],raw)
        records[kind]={'name':INPUT_NAMES[kind],'sha256':_sha(raw),'bytes':len(raw)}
    return {'schema':SCHEMA,'status':'available','renderer_contract':RENDERER_CONTRACT,
            'renderer_sha256':_renderer_hash(),'inputs':records}


def validate_inputs(directory,descriptor):
    """Shared immutable-input validation; does not render or read current state."""
    root=_directory(directory)
    if (not isinstance(descriptor,dict) or descriptor.get('schema')!=SCHEMA or
            descriptor.get('status')!='available' or descriptor.get('renderer_contract')!=RENDERER_CONTRACT or
            not re.fullmatch('[a-f0-9]{64}',str(descriptor.get('renderer_sha256','')))):
        raise ValueError('Unsupported retained Word renderer contract')
    records=descriptor.get('inputs')
    if not isinstance(records,dict) or set(records)!=set(INPUT_NAMES):raise ValueError('Invalid Word render input set')
    values={}
    for kind,name in INPUT_NAMES.items():
        record=records[kind]
        if not isinstance(record,dict) or set(record)!={'name','sha256','bytes'} or record.get('name')!=name:
            raise ValueError('Invalid Word render input identity')
        raw=_read(root/name,INPUT_LIMITS[kind])
        if len(raw)!=record.get('bytes') or _sha(raw)!=record.get('sha256'):
            raise ValueError('Retained Word render input changed')
        values[kind]=raw
    view=json.loads(values['view']);style=json.loads(values['style'])
    if not isinstance(view,dict) or view.get('reader')!='integrated' or not isinstance(style,dict):
        raise ValueError('Unsupported retained reader view or style')
    return {'view':view,'template_bytes':values['template'],'style_profile':style,
            'inputs_sha256':_sha(_encoded(records))}


def _snapshot(case,manifest):
    sid=manifest['snapshot_id']
    raw=_read(case.output/('report-state-'+sid+'.json'),64*1024*1024)
    if _sha(raw)!=manifest.get('state_sha256'):raise ValueError('Report snapshot changed')
    saved=json.loads(raw)
    if saved['meta']['case_id']!=case.config['case_id'] or saved['meta']['snapshot_id']!=sid:
        raise ValueError('Word export case or snapshot does not match')
    ids=manifest.get('question_ids');saved_ids={q['id'] for q in saved['questions']}
    if not isinstance(ids,list) or not ids or len(ids)!=len(set(ids)) or not set(ids)<=saved_ids:
        raise ValueError('Invalid Word export question selection')
    if manifest.get('selection_mode')!='selected_questions' and set(ids)!=saved_ids:
        raise ValueError('Word export selection differs from the retained snapshot')
    return saved


@contextmanager
def _lock(root,key):
    fd=os.open(root/('.'+key+'.lock'),os.O_RDWR|os.O_CREAT|os.O_NOFOLLOW,0o600)
    with os.fdopen(fd,'a') as lock:
        if not stat.S_ISREG(os.fstat(lock.fileno()).st_mode):raise ValueError('Invalid Word cache lock')
        fcntl.flock(lock,fcntl.LOCK_EX)
        yield


def _normalized(path):
    raw=io.BytesIO()
    with zipfile.ZipFile(path) as source,zipfile.ZipFile(raw,'w') as target:
        for item in source.infolist():
            item.date_time=(1980,1,1,0,0,0);target.writestr(item,source.read(item.filename))
    return raw.getvalue()


def export_word(case,bundle_id):
    """Create/cache only on explicit download, or return a verified legacy DOCX.

    Stale judgments do not rewrite a historical document. All text, sensitive
    appendices, masking, template bytes and style come from its retained bundle.
    """
    from .finalization import report_files
    _directory(case.output)
    manifest,documents=report_files(case.output,bundle_id,case.config['case_id'])
    root=_directory(case.output/('report-bundle-'+bundle_id))
    saved=_snapshot(case,manifest)
    base={'bundle_id':bundle_id,'snapshot_id':manifest['snapshot_id'],
          'local_only':bool(manifest.get('local_only'))}
    if manifest.get('delivery_policy'):base['delivery_policy']=manifest['delivery_policy']
    existing=next((record for record in documents if record['name']=='integrated.docx'),None)
    if existing is None:existing=next((record for record in documents if record['name']=='practitioner.docx'),None)
    if existing:
        return {**base,**existing,'path':root/existing['name'],'cached':True,'legacy':True}
    if manifest.get('document_formats')!=FORMATS:raise ValueError('This report has no retained Word export inputs')
    descriptor=manifest['lazy_word'];inputs=validate_inputs(root,descriptor)
    view=inputs['view']
    if view.get('snapshot_revision')!=manifest['snapshot_id'] or view.get('report_id')!=saved['meta']['report_id']:
        raise ValueError('Retained Word view does not match the report snapshot')
    binding={'schema':SCHEMA,'case_id':case.config['case_id'],'bundle_id':bundle_id,
             'snapshot_id':manifest['snapshot_id'],'renderer_contract':descriptor['renderer_contract'],
             'inputs_sha256':inputs['inputs_sha256']}
    key=_sha(_encoded(binding));cache_root=_directory(case.output/'.word-exports',create=True)
    cache=cache_root/key
    with _lock(cache_root,key):
        if cache.exists() or cache.is_symlink():
            _directory(cache)
            receipt=json.loads(_read(cache/'receipt.json',64*1024))
            if any(receipt.get(field)!=value for field,value in binding.items()):raise ValueError('Word cache identity changed')
            if (receipt.get('original_renderer_sha256')!=descriptor['renderer_sha256'] or
                    not re.fullmatch('[a-f0-9]{64}',str(receipt.get('actual_renderer_sha256',''))) or
                    receipt.get('receipt_sha256')!=_sha(_encoded({k:v for k,v in receipt.items() if k!='receipt_sha256'}))):
                raise ValueError('Word cache receipt changed')
            raw=_read(cache/'integrated.docx',128*1024*1024)
            if len(raw)!=receipt.get('bytes') or _sha(raw)!=receipt.get('sha256'):raise ValueError('Cached Word document changed')
            return {**base,'path':cache/'integrated.docx','name':'integrated.docx',
                    'sha256':receipt['sha256'],'bytes':receipt['bytes'],'cached':True,'receipt':receipt}
        staging=Path(tempfile.mkdtemp(prefix='.'+key+'-',dir=cache_root))
        try:
            from .render_views import render_docx
            output=staging/'integrated.docx'
            render_docx(view,output,template_bytes=inputs['template_bytes'],style_profile=inputs['style_profile'])
            raw=_normalized(output);output.unlink();_write(output,raw)
            # Recheck inputs before publication, without rebuilding the view.
            report_files(case.output,bundle_id,case.config['case_id'])
            validate_inputs(root,descriptor)
            receipt={**binding,'original_renderer_sha256':descriptor['renderer_sha256'],
                     'actual_renderer_sha256':_renderer_hash(),'sha256':_sha(raw),'bytes':len(raw)}
            receipt['receipt_sha256']=_sha(_encoded(receipt))
            _write(staging/'receipt.json',_encoded(receipt))
            os.rename(staging,cache)
            return {**base,'path':cache/'integrated.docx','name':'integrated.docx',
                    'sha256':receipt['sha256'],'bytes':receipt['bytes'],'cached':False,'receipt':receipt}
        finally:
            if staging.exists():shutil.rmtree(staging)
