import assert from 'node:assert/strict';
import {readFile} from 'node:fs/promises';
import {createRequire} from 'node:module';
import test from 'node:test';
import vm from 'node:vm';

const ts=createRequire(new URL('../web/package.json',import.meta.url))('typescript');
const source=await readFile(new URL('../web/src/pages/ActivityRegistrationsPage.tsx',import.meta.url),'utf8');
class ApiError extends Error {constructor(status,message){super(message);this.status=status;}}
function workspace() {
 let identity=1;
 const queries=[],reads=[],exports={};
 const api={ApiError,captureSessionIdentityGuard:()=>{const original=identity;return()=>identity===original;},apiFetch:async(path,init={})=>{reads.push({path,signal:init.signal});return[];}};
 const modules={
  react:{useState:value=>[typeof value==='function'?value():value,()=>{}],useRef:value=>({current:value}),useEffect:()=>{}},
  'react/jsx-runtime':{jsx:(type,props)=>({type,props}),jsxs:(type,props)=>({type,props})},
  '@tanstack/react-router':{useSearch:()=>({attempt_id:'attempt',payment:'confirming'}),Link:()=>{}},
  '@tanstack/react-query':{useQuery:options=>{queries.push(options);return{data:options.queryKey[0]==='activity-payment-refresh'?undefined:[],isPending:false,isError:false,isFetching:false};},useQueryClient:()=>({invalidateQueries:()=>{}})},
  '../lib/api':api,
  '../lib/commerce':{refreshPaymentAttempt:async(id,signal)=>{reads.push({path:`refresh:${id}`,signal});return{status:'pending'};}},
  '../lib/activities':{activityAccessDenied:error=>error instanceof ApiError&&[401,403].includes(error.status),retryActivityRead:()=>false,filterActivityRegistrations:rows=>rows},
 };
 const compiled=ts.transpileModule(source.replace('function RegistrationWorkspace(', 'export function RegistrationWorkspace('),{compilerOptions:{module:ts.ModuleKind.CommonJS,target:ts.ScriptTarget.ES2022,jsx:ts.JsxEmit.ReactJSX}}).outputText;
 vm.runInNewContext(compiled,{exports,require:path=>modules[path]||{},Date});
 exports.RegistrationWorkspace({userId:'same-user'});
 return{queries,reads,changeIdentity:()=>{identity+=1;}};
}
for(const kind of ['activity-registrations','activity-charges','activity-payment-refresh']) {
 test(`舊活動workspace失效後 ${kind} 不重建私人讀取或輪詢`,async()=>{
  const fixture=workspace(),query=fixture.queries.find(item=>item.queryKey[0]===kind);
  fixture.changeIdentity();
  await assert.rejects(query.queryFn({signal:new AbortController().signal}),error=>error instanceof ApiError&&error.status===401);
  assert.equal(fixture.reads.length,0);
  assert.equal(query.refetchInterval({state:{error:null,data:{status:'pending'}}}),false);
 });
}
test('同一身分正常續期維持三個活動讀取，原signal傳到transport',async()=>{
 const fixture=workspace(),signal=new AbortController().signal;
 for(const query of fixture.queries) {
  await query.queryFn({signal});
  assert.equal(fixture.reads.at(-1).signal,signal);
  assert.ok(query.refetchInterval({state:{error:null,data:{status:'pending'}}})>0);
 }
 assert.equal(fixture.reads.length,3);
});
test('舊workspace失效後新workspace可用相同userId重新取得新權限',async()=>{
 const old=workspace();old.changeIdentity();
 await assert.rejects(old.queries[0].queryFn({signal:new AbortController().signal}),error=>error.status===401);
 const current=workspace();await current.queries[0].queryFn({signal:new AbortController().signal});
 assert.equal(old.reads.length,0);assert.equal(current.reads.length,1);
});
