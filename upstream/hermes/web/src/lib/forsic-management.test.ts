// @vitest-environment jsdom
import {afterEach,expect,it,vi} from 'vitest';
import {downloadManagementFile} from './forsic-management';
const authedFetch=vi.hoisted(()=>vi.fn());
vi.mock('@/lib/api',()=>({authedFetch,fetchJSON:vi.fn()}));
afterEach(()=>{authedFetch.mockReset();Reflect.deleteProperty(window,'showSaveFilePicker');});
it('opens the save picker in the click gesture and streams the authenticated response to disk',async()=>{
 const writes:Uint8Array[]=[];const writer=new WritableStream<Uint8Array>({write(chunk){writes.push(chunk);}});
 const picker=vi.fn(async()=>({createWritable:async()=>writer}));Object.defineProperty(window,'showSaveFilePicker',{value:picker,configurable:true});
 authedFetch.mockImplementation(async()=>{expect(picker).toHaveBeenCalledOnce();return new Response(new Uint8Array([1,2,3]),{headers:{'Content-Length':'3'}});});
 await downloadManagementFile('token','sample');expect(authedFetch).toHaveBeenCalledWith('/api/plugins/forsic/management/files/token?profile=sample');expect([...writes[0]]).toEqual([1,2,3]);
});
it('does not buffer a large image into a blob when streaming file save is unsupported',async()=>{
 const blob=vi.fn();const cancel=vi.fn(async()=>{});authedFetch.mockResolvedValue({ok:true,headers:new Headers({'Content-Length':String(2*1024**3)}),body:{cancel},blob});
 await expect(downloadManagementFile('token')).rejects.toThrow('파일 저장 대화상자');expect(blob).not.toHaveBeenCalled();expect(cancel).toHaveBeenCalledOnce();
});
it('does not download anything when the user cancels the save picker',async()=>{
 Object.defineProperty(window,'showSaveFilePicker',{value:vi.fn(async()=>{throw new DOMException('cancelled','AbortError');}),configurable:true});await downloadManagementFile('token');expect(authedFetch).not.toHaveBeenCalled();
});
