/* Development fixture only: actual production view with native DOM and fake ctl. */
'use strict';
function fallbackE(tag, attrs = {}, children = []) {
  let el;
  if (tag.startsWith('<')) { const template = document.createElement('template'); template.innerHTML = tag; el = template.content.firstChild; }
  else el = document.createElement(tag);
  for (const [key, value] of Object.entries(attrs)) {
    if (value == null) continue;
    if (typeof value === 'function') el.addEventListener(key, value); else el.setAttribute(key, value);
  }
  if (Array.isArray(children)) for (const child of children) el.append(child instanceof Node ? child : document.createTextNode(String(child)));
  else if (children instanceof Node) el.append(children);
  else if (children != null) el.innerHTML = String(children);
  return el;
}
const baseline = {
  policy: {allowed:[],blocked:[],pause:null}, state: 'configured_unverified', managed: true, configured: true, backend_enabled: true,
  resolver: 'dnsmasq.servers', dns_mode_safe: true, force_dns: 'off', tls_download: 'strict', dnsmasq_port_hint: 'default', client_dns_verified: false,
  backend: { state: 'ready', domains: 66425, errors: 0, warnings: 1 }, adapter: { plugin: 'nailink', stage: 'configured_unverified', domains: 66425 },
  compatibility: { platform: { distribution: 'OpenWrt', version: '25.12.5', cpu: 'aarch64', target: 'rockchip/armv8', declared_arch: 'aarch64_generic', package_arch: 'aarch64', package_manager: 'apk' },
    plugins: ['nailink', 'passwall', 'passwall2', 'openclash'].map((id, i) => ({ id, installed: true, enabled: i === 0, runtime: i === 0 ? 'running' : 'unknown' })), dns: { active_proxy_count: 1 } }
};
let state = structuredClone(baseline), app, calls = 0, statusDelay = 700;
const api = { exec: async (_program, args) => {
  if (args[0] === 'status') { await new Promise(resolve => setTimeout(resolve, statusDelay)); if (state.state === 'error') throw new Error('模拟：连接暂时中断'); return { code: 0, stdout: JSON.stringify(state) }; }
  throw Error('Only status uses file.exec');
} };
const previewDNS={domestic:{servers:['223.5.5.5','119.29.29.29'],active:'223.5.5.5'},foreign:{servers:['1.1.1.1','8.8.8.8'],active:'1.1.1.1'}};
let sourceData={dns:structuredClone(previewDNS),revision:'a'.repeat(64),items:[],catalog:[]},jobTick=0;
let observerFixture={available:true,active:true,queries:8420,blocked:1154,answered:8403,verification:{state:'idle'},latency:{scope:'lan_observed',normal:{samples:600,average_ms:3.42,maximum_ms:615,slow:2},blocked:{samples:100,average_ms:0.21,maximum_ms:4,slow:0},unanswered:{udp:1,tcp:0}}};
let enhanceFixture={config:{enabled:false,cname:true,failover:false,proxy_port:0,devices:[]},runtime:{state:'waiting',reason:'disabled',counts:{cname:0,device:0}},tests:null};
const rpc = {getSessionID:()=> 'b'.repeat(32),declare: spec => async (...args) => {
  await new Promise(r => setTimeout(r,700));
  if (document.getElementById('fail').checked) return {ok:false,message:'模拟：操作未完成，请重新检测。'};
  if(spec.method==='enhance_status')return {ok:true,data:structuredClone({...enhanceFixture,profile:sourceData.dns})};
  if(spec.method==='enhance_save'){enhanceFixture.config=JSON.parse(args[0]).after;return {ok:true,job:'11111111-1111-1111-1111-111111111111'};}
  if(spec.method==='dns_test'){let port=sourceData.dns.proxy_port??enhanceFixture.config.proxy_port;enhanceFixture.tests={profile:structuredClone(sourceData.dns),proxy_port:port,groups:{domestic:sourceData.dns.domestic.servers.map((address,i)=>({address,ok:true,ms:4+i*3})),foreign:sourceData.dns.foreign.servers.map((address,i)=>({address,ok:!!port,skipped:!port,ms:85+i*10}))}};return {ok:true,job:'11111111-1111-1111-1111-111111111111'};}
  if(spec.method==='temporary'){state.policy.allowed.push(args[1]);state.policy.temporary=[{scope:args[1],duration:args[2],remaining:args[2]}];return {ok:true,job:'11111111-1111-1111-1111-111111111111'};}
  if(spec.method==='observation')return {ok:true,data:document.getElementById('scenario').value==='observer-pending'?{available:true,active:false,queries:0,blocked:0,reason:'adapter_pending',verification:{state:'idle'}}:{...structuredClone(observerFixture),active:state.state==='configured_unverified'}};
  if(spec.method==='verification_start'){
    observerFixture.verification={state:'waiting',remaining:120,marker:false,blocked:false,normal:false};
    setTimeout(()=>{observerFixture.verification={state:'passed',client:'192.168.2.10',at:Math.floor(Date.now()/1000)};},6000);
    return {ok:true,commands:'nslookup -type=A example-marker.adzero.invalid\nnslookup -type=A doubleclick.net\nnslookup -type=A www.baidu.com'};
  }
  if (spec.method === 'source_list') return {ok:true,data:structuredClone(sourceData)};
  if (spec.method === 'job_status') {
    if(document.getElementById('slow-job').checked){
      jobTick++;
      if(jobTick===3)throw Error('模拟：代理重载造成连接重置');
      if(jobTick<5)return {ok:true,pending:true,progress:{stage:jobTick===1?'checking':jobTick===2?'validating':'restarting',elapsed:jobTick*6}};
    }
    return {ok:true,pending:false};
  }
  jobTick=0;
  document.getElementById('calls').textContent = '操作次数：' + (++calls);
  if(spec.method==='settings_apply'){
    const p=JSON.parse(args[0]);
    sourceData.items=p.items.map((i,n)=>({...i,id:i.id||'batch_'+n,https:true,domains:i.enabled?66425:null,updated:Math.floor(Date.now()/1000)}));
    if(p.dns)sourceData.dns=structuredClone(p.dns);sourceData.revision='d'.repeat(64);state.policy.routing_enabled=p.routing_enabled;state.adapter.routing={mode:p.routing_enabled?'proxy':'inactive'};state.adapter.dns_policy={strict:!!sourceData.dns.strict,guard:sourceData.dns.strict?'active':'off',domestic:sourceData.dns.domestic.mode||'single',foreign:sourceData.dns.foreign.mode||'single'};
    return {ok:true,job:'11111111-1111-1111-1111-111111111111'};
  }
  if (['enable','disable','refresh','reload'].includes(spec.method)) {
    state.state = spec.method === 'disable' ? 'disabled' : 'configured_unverified';
    return {ok:true,job:'11111111-1111-1111-1111-111111111111'};
  }
  if (spec.method.startsWith('source_')) {
    if (spec.method === 'source_add') {
      const rule=sourceData.catalog.find(r=>r.id===args[0]);
      sourceData.items.push({id:'source'+Date.now(),name:rule?rule.name:args[1],url:rule?rule.url:args[2],format:rule?rule.format:args[3],catalog_id:rule?rule.id:null,enabled:true,https:true,domains:rule?66425:12500,updated:Math.floor(Date.now()/1000)});
    }
    if (spec.method === 'source_toggle') sourceData.items.find(r=>r.id===args[0]).enabled=args[2];
    if (spec.method === 'source_remove') sourceData.items=sourceData.items.filter(r=>r.id!==args[0]);
    sourceData.last={at:Math.floor(Date.now()/1000),ok:true};
    return {ok:true,job:'11111111-1111-1111-1111-111111111111'};
  }
  if (spec.method === 'lookup') {
    const domain = args[0].replace(/^https?:\/\//,'').split('/')[0].toLowerCase();
    if (!/^[a-z0-9.-]+\.[a-z]+$/.test(domain)) return {ok:false,message:'请输入有效域名。'};
    const allowed = state.policy.allowed.some(d => domain===d || domain.endsWith('.'+d));
    const hit = domain==='ads.example' || domain.endsWith('.ads.example');
    return {ok:true,data:{available:true,domain,match:allowed?'allowed':hit?'parent':'none',rule:hit?'ads.example':null,scope:hit?'ads.example':null}};
  }
  if (spec.method==='allow') state.policy.allowed.push(args[1]);
  if (spec.method==='revoke') state.policy.allowed=state.policy.allowed.filter(x=>x!==args[1]);
  if (spec.method==='routing_on'||spec.method==='routing_off') { state.policy.routing_enabled=spec.method==='routing_on';state.adapter.routing={mode:state.policy.routing_enabled?'proxy':'inactive'}; }
  state.state=spec.method==='pause'?'paused':'configured_unverified';
  state.policy.pause=spec.method==='pause'?{remaining:args[0],stage:'paused'}:null;
  return {ok:true,job:'11111111-1111-1111-1111-111111111111'};
}};
const previewTimers=new Map();
const poll = {add(fn,interval){fn();previewTimers.set(fn,setInterval(fn,interval*1000));},remove(fn){clearInterval(previewTimers.get(fn));previewTimers.delete(fn);}};
function mount() { app.busy = app.notice = null; app.lastChecked = new Date(); document.getElementById('preview').replaceChildren(app.render(state)); }
async function initialMount(delay) {
  statusDelay = delay; app.busy = app.notice = app.lastChecked = null;
  const start = performance.now();
  document.getElementById('preview').replaceChildren(app.render(await app.load()));
  requestAnimationFrame(() => { document.getElementById('timing').textContent = '界面已显示 · 状态读取中（模拟延迟 ' + delay + ' ms）'; });
  await app.initialRequest;
  document.getElementById('timing').textContent = '状态填入：' + Math.round(performance.now() - start) + ' ms（含模拟延迟）';
}
document.getElementById('reload').addEventListener('click', () => initialMount(5000));
document.getElementById('expire-pause').addEventListener('click', () => {
  if (state.state === 'paused') { state.state = 'configured_unverified'; state.policy.pause = null; }
});
document.getElementById('scenario').addEventListener('change', ev => {
  state = structuredClone(baseline);
  if (ev.target.value === 'off') state.state = 'disabled';
  if (ev.target.value === 'multi') { state.state = 'disabled'; state.compatibility.dns.active_proxy_count = 2; }
  if (ev.target.value === 'recovery') state.state = 'inconsistent';
  if (ev.target.value === 'error') { state.state = 'error'; state.error = '模拟：连接暂时中断'; }
  if(['nailink-alias','openclash-alias','alias-degraded'].includes(ev.target.value)){
    const plugin=ev.target.value==='nailink-alias'?'nailink':'openclash';state.adapter.plugin=plugin;
    enhanceFixture.config.enabled=true;enhanceFixture.runtime={state:'active',plugin,counts:{cname:12,device:3},inspection:{checked:85,failed:ev.target.value==='alias-degraded'?2:0,degraded:ev.target.value==='alias-degraded'}};
    app.enhanceData=null;app.enhanceDraft=null;
  }
  mount();
});
document.getElementById('appearance').addEventListener('change', ev => { document.documentElement.dataset.theme = ev.target.value; });
document.getElementById('sidebar').addEventListener('change', ev => { document.body.classList.toggle('with-sidebar', ev.target.checked); });
document.getElementById('host-frame').addEventListener('change', ev => {
  const host = document.getElementById('preview-host');
  host.classList.toggle('main-right', ev.target.checked); host.querySelector('header').hidden = !ev.target.checked;
});
Promise.all([fetch('/luci-static/resources/view/adzero/overview.js').then(r => r.text()),fetch('/source_catalog.json').then(r=>r.json())]).then(([source,catalog]) => {
  // Mixed, explicitly simulated rows exercise truncation, missing counts and
  // exclusions as well as both library states. Never fetched from a router.
  sourceData.catalog=catalog; sourceData.items=catalog.slice(0,2).map((rule,index)=>({...rule,id:'preview_'+index,catalog_id:rule.id,enabled:index===0,https:true,domains:index===0?66425:129400,updated:Math.floor(Date.now()/1000)})).concat([
    {id:'preview_cname',name:'AdguardTeam - CNAME Trackers',url:'https://raw.githubusercontent.com/AdguardTeam/cname-trackers/master/data/combined_disguised_trackers_justdomains.txt',format:'domains',enabled:false,https:true,domains:245052,skipped:26,updated:Math.floor(Date.now()/1000)},
    {id:'preview_oisd',name:'OISD - Big',url:'https://big.oisd.nl/',format:'unknown',enabled:false,https:true},
    {id:'preview_hosts',name:'StevenBlack - Unified hosts',url:'https://cdn.jsdelivr.net/gh/StevenBlack/hosts/hosts',format:'hosts',enabled:false,https:true}
  ]);
  app = Function('view', 'fs', 'dom', '_', 'E', 'document', 'L', 'rpc', 'poll', source)({ extend: x => x }, api,
    window.previewDom || { content: (el, content) => el.replaceChildren(...(content == null ? [] : Array.isArray(content) ? content : [content])) }, x => x, window.E || fallbackE, document, { resource: x => '/luci-static/resources/' + x }, rpc, poll); initialMount(700);
});
