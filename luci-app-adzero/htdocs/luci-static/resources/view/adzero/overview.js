'use strict';
'require view';
'require fs';
'require dom';
'require rpc';
'require poll';

var policyCalls = {};
var updateCalls = {
	check: rpc.declare({object:'adzero',method:'update_check',params:[]}),
	start: rpc.declare({object:'adzero',method:'update_start',params:['version']})
};
// Static help links stay available while the router is loading or applying changes.
var helpLinks = [
	['https://slobys.github.io/AdZero/', _('在线文档'), 'book', _('在线使用文档（新窗口）')],
	['https://www.youtube.com/@naiyou', 'YouTube', 'youtube', _('YouTube 频道（新窗口）')],
	['https://t.me/naiyous', 'Telegram', 'telegram', _('Telegram 群组（新窗口）')]
];
var observationCalls = {
	read:rpc.declare({object:'adzero',method:'observation',params:[]}),
	test:rpc.declare({object:'adzero',method:'verification_start',params:[]}),
	recent:rpc.declare({object:'adzero',method:'recent_set',params:['enabled']}),
	reset:rpc.declare({object:'adzero',method:'stats_reset',params:[]})
};
[ ['enable', []], ['disable', []], ['refresh', []], ['reload', []], ['routing_on', []], ['routing_off', []], ['lookup', ['domain']], ['allow', ['domain', 'scope']], ['revoke', ['domain', 'scope']], ['pause', ['seconds']], ['resume', []] ].forEach(function(method) {
	policyCalls[method[0]] = rpc.declare({ object: 'adzero', method: method[0], params: method[1] });
});
policyCalls.temporary=rpc.declare({object:'adzero',method:'temporary',params:['domain','scope','seconds']});
var enhanceCalls={read:rpc.declare({object:'adzero',method:'enhance_status',params:[]}),save:rpc.declare({object:'adzero',method:'enhance_save',params:['payload']}),test:rpc.declare({object:'adzero',method:'dns_test',params:[]})};
var sourceCalls = {};
var controlsApply=rpc.declare({object:'adzero',method:'controls_apply',params:['payload']});
[ ['source_list', []], ['source_add', ['catalog','name','url','format']], ['source_toggle', ['id','expected_url','enabled']], ['source_remove', ['id','expected_url']], ['source_refresh', []], ['source_verify', []], ['settings_apply', ['payload']], ['job_status', ['job']] ].forEach(function(method) {
	sourceCalls[method[0]] = rpc.declare({object:'adzero',method:method[0],params:method[1]});
});
function sessionValue(kind, value) {
	try {
		var sid = typeof rpc.getSessionID === 'function' ? rpc.getSessionID() : '';
		if (!/^[a-f0-9]{32}$/.test(sid) || /^0+$/.test(sid) || typeof sessionStorage === 'undefined') return null;
		var key = 'adzero.v0.8.' + sid + '.' + kind;
		if (value === null) sessionStorage.removeItem(key);
		else if (value !== undefined) sessionStorage.setItem(key,JSON.stringify(value));
		else return JSON.parse(sessionStorage.getItem(key) || 'null');
	} catch (e) {} // Storage can be disabled; live state still works normally.
	return null;
}
function saveSnapshot(s) {
	// Presentation only. Never cache rule URLs, private tokens or permissions.
	var snapshot = {};
	['state','managed','configured','backend_enabled','dns_mode_safe','adguardhome_running','backend','adapter','client_dns_verified'].forEach(function(k) { snapshot[k]=s[k]; });
	snapshot.compatibility = {dns:{active_proxy_count:((s.compatibility || {}).dns || {}).active_proxy_count}};
	snapshot.policy = {pause:(s.policy || {}).pause || null,routing_enabled:(s.policy || {}).routing_enabled===true};
	sessionValue('snapshot',{at:Date.now(),status:snapshot});
}
function readSnapshot() {
	var saved=sessionValue('snapshot');
	if (!saved || typeof saved.at !== 'number' || Date.now()-saved.at < 0 || Date.now()-saved.at > 300000 || !saved.status || typeof saved.status.state !== 'string') return null;
	return Object.assign({},saved.status,{stale:true,cached_at:saved.at});
}
var phaseText = {
 queued:_('任务已提交…'), waiting_lock:_('正在等待上一项操作完成…'), checking:_('正在检查配置…'),
 downloading:_('正在下载规则，首次下载可能较慢…'), validating:_('正在校验规则…'),
 processing:_('正在处理规则并重载 DNS…'), adapting:_('正在配置当前代理接入…'),
 restarting:_('正在重载代理，连接可能短暂中断…'), waiting_proxy:_('关闭已排队，正在等待 NaiLink 内核切换结束…'), confirming_proxy:_('重载仍在进行，正在确认完成状态；不会重复重载…'), recovering_proxy:_('正在恢复 NaiLink 服务，连接可能短暂中断；无需重启路由器…'), restoring:_('正在恢复原有配置…'),
 verifying:_('正在核对运行状态…'), reconnecting:_('连接暂时中断，正在继续追踪原任务…')
};
function delay(ms) { return new Promise(function(resolve) { setTimeout(resolve,ms); }); }
function taskError(reply, fallback) {
 var message=reply&&reply.message||fallback, source=reply&&reply.progress&&reply.progress.source;
 if(reply&&/^source_/.test(reply.reason||'')&&source&&Number.isInteger(source.index)&&Number.isInteger(source.total)&&source.index>=1&&source.index<=source.total&&source.total<=30)message+=_('（本批第 ')+source.index+' / '+source.total+_(' 个启用订阅；按页面顺序）');
 var error=new Error(message);error.reason=reply&&reply.reason;error.recovery=reply&&reply.recovery;return error;
}
function operationActive(s) {return !s.stale&&s.operation&&s.operation.active===true;}
function lockNotice(action) {
 return {kind:'warning',text:action==='disable'?_('上次关闭未执行：等待其他操作超时。'):_('上次请求未执行：等待其他操作超时。'),detail:_('本次请求未取得操作锁，未修改配置。当前保护状态见下方；同步结束后若仍需操作，请再点击一次。页面不会自动重试原请求。')};
}
function showProgress(self, value) {
 if (!self || !self.busy) return;
 var old=self.jobProgress || {};
 self.jobProgress=value;
 if (old.stage!==value.stage || JSON.stringify(old.source)!==JSON.stringify(value.source) || Math.floor((old.elapsed || 0)/10)!==Math.floor((value.elapsed || 0)/10)) self.paint();
}
function waitJob(reply,self) {
 if (!reply || !reply.ok) { if(self)self.jobUncertain=false;return Promise.reject(taskError(reply,_('操作未完成，请重新检测。'))); }
 if (!reply.job) return Promise.resolve();
 var attempts=0, failures=0;
 function check() {
  return sourceCalls.job_status(reply.job).then(function(result) {
   failures=0;
   if (!result || !result.ok) { if(self)self.jobUncertain=false;throw taskError(result,_('任务结果未知，请重新检测。')); }
   showProgress(self,result.progress || {stage:'checking'});
   if (!result.pending) { if(self)self.jobUncertain=false;return result; }
   if (++attempts>1200) throw new Error(_('任务仍在后台，点击重新检测继续核对，勿重复提交。'));
   return delay(2000).then(check);
  },function(error) {
   showProgress(self,{stage:'reconnecting',elapsed:(self && self.jobProgress || {}).elapsed || 0});
   if (++failures>15) throw new Error(_('连接尚未恢复，任务回执已保留。请重新检测继续核对。'));
   return delay(2000).then(check);
  });
 }
 return check();
}
function trackJob(reply,kind,action,self) {
 if (reply && reply.ok && !/^[a-f0-9]{8}-[a-f0-9]{4}-[a-f0-9]{4}-[a-f0-9]{4}-[a-f0-9]{12}$/.test(reply.job || '')) return Promise.reject(new Error(_('未收到有效任务回执，请重新检测。')));
 if (reply && reply.job) {
  var receipt={job:reply.job,kind:kind,action:action};sessionValue('job',receipt);
  if(self){self.currentJob=receipt;self.jobUncertain=true;}
 }
 return waitJob(reply,self);
}
function taskStatus(self, expected) {
 var retries=0, settling=0;
 function read() {return getStatus().then(function(s){
  if(expected && s.state==='inconsistent' && settling++<4){showProgress(self,{stage:'verifying'});return delay(1000).then(read);}
  return s;
 },function(error){
  showProgress(self,{stage:'reconnecting'});
  if(++retries<=5)return delay(1000).then(read);
  self.jobUncertain=!!self.currentJob;throw error;
 });}
 return read();
}
function actionSuccess(action) {
 return {kind:'success',text:action==='enable'?_('广告过滤已开启，规则配置已同步。'):action==='disable'?_('广告过滤已关闭，原有设置已恢复。'):action==='reload'?_('当前配置已重新加载，未强制更新订阅。'):_('规则已更新，当前配置已同步。')};
}
function actionConfirmation(action) {
 return {kind:'warning',text:_('任务已结束，正在继续核对配置状态。'),detail:_('页面会继续自动检查，不重复关闭、启用或重载。仍未确认时可点击重新检测。'),expectedState:action==='disable'?'disabled':'configured_unverified',action:action};
}
function confirmationNotice(notice, s) {
 if(notice.expectedState===s.state)return actionSuccess(notice.action);
 if(s.state==='inconsistent'||s.state==='closing')return notice.kind==='warning'?notice:actionConfirmation(notice.action);
 return {kind:'error',text:_('当前状态与请求结果不一致，请重新检测。'),detail:_('页面没有重复提交关闭、启用或重载；请以当前保护状态为准。')};
}
function confirmingAction(self) {return self.notice&&self.notice.kind==='warning'&&self.notice.expectedState&&['inconsistent','closing'].indexOf((self.currentStatus||{}).state)>=0;}
function statusKey(s) {
 return JSON.stringify([s.state,!!s.stale,s.managed,s.configured,s.backend_enabled,s.dns_mode_safe,s.adguardhome_running,s.backend,s.adapter,s.policy,(s.compatibility||{}).dns,(s.compatibility||{}).plugins,s.operation]);
}
function needsStatusRecheck(self) {
 var s=self.currentStatus||{},o=self.observation||{},c=o.connection,a=s.adapter||{};
 return operationActive(s)||s.state==='inconsistent'||s.state==='closing'||!!a.reload_pending||flags(s).enabled&&o.available===true&&(
  ['protection_disabled','backend_disabled','not_managed'].indexOf(o.reason)>=0||c&&(c.plugin!==a.plugin||c.stage!==a.stage));
}
function finishJob(self) {
 if(self.jobUncertain){
  self.currentStatus=Object.assign({},self.currentStatus,{stale:true});
  self.notice={kind:'error',text:_('连接或任务结果尚未确认，回执已保留；点击重新检测继续核对。'),detail:(self.notice || {}).detail};
 }else{
  var saved=sessionValue('job');
  if(!saved || !self.currentJob || saved.job===self.currentJob.job)sessionValue('job',null);
  self.currentJob=null;
 }
 self.busy=null;self.jobProgress=null;
}

// Phosphor Icons (MIT), license: /usr/share/licenses/luci-app-adzero/.
var icons = {
	plus: 'M120,48h16v72h72v16H136v72H120V136H48V120h72Z',
	edit: 'M184,24l48,48L88,216H40V168Zm0,24-24,24,24,24,24-24ZM56,176v24H80L172,108,148,84Z',
	trash: 'M96,24h64V48h56V64H40V48H96Zm16,16v8h32V40ZM56,80H72V208H184V80h16V208a16,16,0,0,1-16,16H72a16,16,0,0,1-16-16Zm40,16h16v96H96Zm48,0h16v96H144Z',
	book: 'M32,40H96c13,0,24,5,32,14,8-9,19-14,32-14h64v168h-64c-10,0-18,4-24,12h-16c-6-8-14-12-24-12H32Zm16,16v136h48c9,0,17,2,24,6V76c-5-13-13-20-24-20Zm88,142c7-4,15-6,24-6h48V56h-48c-11,0-19,7-24,20Z',
	youtube: 'M224,64c-5-13-13-17-26-19-23-3-117-3-140,0C45,47,37,51,32,64c-8,22-8,106,0,128,5,13,13,17,26,19,23,3,117,3,140,0,13-2,21-6,26-19,8-22,8-106,0-128ZM48,70c2-5,5-7,12-9,22-3,114-3,136,0,7,2,10,4,12,9,7,19,7,97,0,116-2,5-5,7-12,9-22,3-114,3-136,0-7-2-10-4-12-9-7-19-7-97,0-116Zm56,18v80l64-40Z',
	telegram: 'M232,24,24,108c-10,4-9,17,1,20l53,16,23,69c3,10,15,12,21,4l30-32,48,35c8,6,18,1,20-8L248,42c2-12-5-22-16-18ZM43,116,221,44,88,130Zm57,27,101-77-84,96-9,30Zm31,29,94-108-20,139Z',
	shield: 'M208,40H48A16,16,0,0,0,32,56v56c0,52.72,25.52,84.67,46.93,102.19,23.06,18.86,46,25.26,47,25.53a8,8,0,0,0,4.2,0c1-.27,23.91-6.67,47-25.53C198.48,196.67,224,164.72,224,112V56A16,16,0,0,0,208,40Zm0,72c0,37.07-13.66,67.16-40.6,89.42A129.3,129.3,0,0,1,128,223.62a128.25,128.25,0,0,1-38.92-21.81C61.82,179.51,48,149.3,48,112l0-56,160,0ZM82.34,141.66a8,8,0,0,1,11.32-11.32L112,148.69l50.34-50.35a8,8,0,0,1,11.32,11.32l-56,56a8,8,0,0,1-11.32,0Z',
	refresh: 'M224,48V96a8,8,0,0,1-8,8H168a8,8,0,0,1,0-16h28.69L182.06,73.37a79.56,79.56,0,0,0-56.13-23.43h-.45A79.52,79.52,0,0,0,69.59,72.71,8,8,0,0,1,58.41,61.27a96,96,0,0,1,135,.79L208,76.69V48a8,8,0,0,1,16,0ZM186.41,183.29a80,80,0,0,1-112.47-.66L59.31,168H88a8,8,0,0,0,0-16H40a8,8,0,0,0-8,8v48a8,8,0,0,0,16,0V179.31l14.63,14.63A95.43,95.43,0,0,0,130,222.06h.53a95.36,95.36,0,0,0,67.07-27.33,8,8,0,0,0-11.18-11.44Z',
	settings: 'M40,88H73a32,32,0,0,0,62,0h81a8,8,0,0,0,0-16H135a32,32,0,0,0-62,0H40a8,8,0,0,0,0,16Zm64-24A16,16,0,1,1,88,80,16,16,0,0,1,104,64ZM216,168H199a32,32,0,0,0-62,0H40a8,8,0,0,0,0,16h97a32,32,0,0,0,62,0h17a8,8,0,0,0,0-16Zm-48,24a16,16,0,1,1,16-16A16,16,0,0,1,168,192Z',
	check: 'M229.66,77.66l-128,128a8,8,0,0,1-11.32,0l-56-56a8,8,0,0,1,11.32-11.32L96,188.69,218.34,66.34a8,8,0,0,1,11.32,11.32Z',
	info: 'M128,24A104,104,0,1,0,232,128,104.11,104.11,0,0,0,128,24Zm0,192a88,88,0,1,1,88-88A88.1,88.1,0,0,1,128,216Zm16-40a8,8,0,0,1-8,8,16,16,0,0,1-16-16V128a8,8,0,0,1,0-16,16,16,0,0,1,16,16v40A8,8,0,0,1,144,176ZM112,84a12,12,0,1,1,12,12A12,12,0,0,1,112,84Z'
};
var names = { nailink: 'NaiLink', passwall: 'PassWall', passwall2: 'PassWall 2', openclash: 'OpenClash', nikki: 'Nikki', ssrplus:'SSR Plus+', homeproxy:'HomeProxy' };
function el(tag, attrs, children) {
	attrs=attrs||{};
	if(tag==='input'||tag==='textarea'){
		if(!attrs.autocomplete)attrs.autocomplete='off';
		if(attrs.spellcheck==null)attrs.spellcheck='false';
		if(attrs.id&&!attrs.name)attrs.name=attrs.id;
	}
	// Native LuCI stringifies null inside arrays and treats scalar strings as
	// HTML. Always give it a flat, non-null array so dynamic strings are text.
	var safe = [];
	function append(value) {
		if (Array.isArray(value)) value.forEach(append);
		else if (value != null && value !== false) safe.push(value);
	}
	append(children);
	return E(tag, attrs || {}, safe);
}
function icon(name) {
	// Static local paths only. Server text is always inserted as text nodes.
	return el('<svg xmlns="http://www.w3.org/2000/svg" viewBox="0 0 256 256"><path d="' + icons[name] + '"/></svg>',
		{ 'class': 'az-icon', 'aria-hidden': 'true', 'focusable': 'false', 'width': '20', 'height': '20' });
}
function getStatus(cache) {
	return fs.exec('/usr/libexec/adzero/ctl', [ 'status' ]).then(function(result) {
		if (Number(result.code) !== 0) throw new Error(result.stderr || _('无法读取 AdZero 状态'));
		var status = JSON.parse(result.stdout);
		if (!status || typeof status.state !== 'string') throw new Error(_('返回的状态数据无法识别'));
		if(cache!==false)saveSnapshot(status);
		return status;
	});
}
function proxyBlocker(s) {
	var inv=s.compatibility||{},dns=inv.dns||{},active=dns.active_proxies||[],uncertain=dns.uncertain_proxies||[];
	if(s.adguardhome_running)return _('AdGuard Home 正在运行，请先关闭对应服务。');
	if(dns.active_proxy_count>1)return _('检测到多个代理：')+(active.map(function(id){return names[id]||id;}).join('、')||_('请检查正在运行的插件'))+_('。请保留一个正在使用的代理后重新检测。');
	if(uncertain.length)return _('暂时无法确认 ')+uncertain.map(function(id){return id==='nailink'?'NaiLink':names[id]||id;}).join('、')+_(' 内核状态，请检查对应插件后重新检测。');
	var unsupported=(inv.plugins||[]).filter(function(p){return p.installed&&p.adapter_supported===false&&(p.active===true||active.indexOf(p.id)>=0);})[0];
	return unsupported?(_('已识别 ')+(names[unsupported.id]||unsupported.id)+_('，当前版本尚未支持安全接入。请保留该插件的原生 DNS 设置。')):null;
}
function pluginRunning(p){return p&&(typeof p.active==='boolean'?p.active:p.runtime==='running');}
function connectionSelection(s) {
	var a=s.adapter||{},dns=(s.compatibility||{}).dns||{},active=dns.active_proxies;
	if(s.stale||s.state!=='configured_unverified'||!Array.isArray(active))return {known:false,plugin:a.plugin,pending:false};
	if(active.length>1||(dns.uncertain_proxies||[]).length)return {known:true,plugin:null,pending:true,detail:proxyBlocker(s)||_('代理运行状态待确认，暂未同步。')};
	var plugin=active[0]||'none',policy=a.dns_policy||{},incompatible=plugin==='passwall'&&policy.foreign==='parallel'&&policy.passwall_dns_mode&&policy.passwall_dns_mode!=='tcp';
	var current=((s.compatibility||{}).plugins||[]).filter(function(p){return p.id===plugin;})[0],coreChanged=plugin==='nailink'&&current&&current.core&&(a.core?current.core!==a.core:current.core!=='dae');
	return {known:true,plugin:plugin,pending:plugin!==(a.plugin||'none')||!!incompatible||!!coreChanged,
		detail:incompatible?_('已应用的国外 DNS 仍为并发，与当前 PassWall DNS 引擎不兼容。请将国外组改为“单一＋备用”后统一应用；国内组可保留并发。'):_('已识别新的运行路径，后台尚未完成接入同步；旧接入记录不代表当前代理已加载规则。')};
}
function flags(s) {
	var enabled = s.state === 'configured_unverified';
	return { enabled: enabled, paused: s.state === 'paused', canDisable: !s.stale && (!operationActive(s)||queueDisable(s)) && (enabled || s.state === 'paused' || s.state==='closing' || (s.state === 'inconsistent' && s.managed === true)),
		canEnable: !s.stale && !operationActive(s) && s.state === 'disabled' && s.dns_mode_safe === true && !proxyBlocker(s) };
}
function queueDisable(s){return !s.stale && s.managed===true && operationActive(s) && s.operation.action==='reconcile';}
function pagePolling(self, panel) {
	if (self.stopPagePolling) self.stopPagePolling();
	var mounted=false,expires=Date.now()+10000,ticks=0,stopped=false,statusReading=false,rechecks=0,revision=self.statusRecheckRevision||0;
	function attached() {
		if(stopped)return false;
		if(document.contains(panel)){mounted=true;return true;}
		// LuCI may run add() immediately, before render() returns to its caller.
		if(mounted || Date.now()>expires)stop();
		return false;
	}
	function read() {
		if(!attached() || document.hidden)return Promise.resolve();
		var pending=[];
		if((self.enhanceOpen||self.pageKey()==='overview'&&self.dnsOpen)&&ticks%5===0&&!self.busy)pending.push(self.loadEnhance(true));
		if(!self.rulesOpen)pending.push(self.refreshObservation());
		var previous=self.currentStatus||{},recovering=needsStatusRecheck(self);
		if(!recovering || revision!==(self.statusRecheckRevision||0)){rechecks=0;revision=self.statusRecheckRevision||0;}
		if(ticks++%5===0 && !self.busy && !previous.stale && (recovering||flags(previous).paused||((previous.policy||{}).temporary||[]).length) && !statusReading){
			if(recovering&&rechecks>=(operationActive(previous)||previous.state==='closing'?60:12)){
				if(operationActive(previous)&&!self.notice){self.notice={kind:'warning',text:_('当前操作耗时较长，请重新检测。'),detail:_('操作进程仍持有锁，页面已暂停自动核对。请保留当前状态，不要手动删除锁。')};self.paint();}
				if(self.notice&&self.notice.expectedState&&self.notice.kind==='warning'){self.notice=Object.assign({},self.notice,{kind:'error',text:_('配置状态仍未确认，请重新检测；不要重复提交原操作。')});self.paint();}
			}else{
				if(recovering)rechecks++;statusReading=true;var readRevision=revision;
				pending.push(getStatus(false).then(function(s){
					// A manual read, action or navigation may have overtaken this RPC.
					if(stopped)return;if(!document.contains(panel)){stop();return;}
					if(self.busy||self.currentStatus!==previous||readRevision!==(self.statusRecheckRevision||0))return;
					var notice=self.notice,changed=statusKey(previous)!==statusKey(s);
					if(previous.state==='paused'&&s.state!=='paused')self.notice=null;
					if(self.notice&&self.notice.expectedState)self.notice=confirmationNotice(self.notice,s);
					if(operationActive(previous)&&!operationActive(s))rechecks=0;
					self.currentStatus=s;self.lastChecked=new Date();saveSnapshot(s);
					if(changed||notice!==self.notice)self.paint(document.activeElement&&document.activeElement.id);
				}).catch(function(){}).then(function(){statusReading=false;}));
			}
		}
		return Promise.all(pending);
	}
	function resume(){if(!document.hidden)read();}
	function stop(){
		if(stopped)return;stopped=true;poll.remove(read);
		if(document.removeEventListener)document.removeEventListener('visibilitychange',resume);
		if(typeof window!=='undefined')window.removeEventListener('focus',resume);
		if(typeof window!=='undefined'&&self.draftLeaveHandler){window.removeEventListener('beforeunload',self.draftLeaveHandler);self.draftLeaveHandler=null;}
		if(typeof window!=='undefined'&&self.pageNavigationHandler){window.removeEventListener('popstate',self.pageNavigationHandler);window.removeEventListener('hashchange',self.pageNavigationHandler);self.pageNavigationHandler=null;}
		self.pageClosed=true;
		if(self.stopPagePolling===stop)self.stopPagePolling=null;
	}
	self.stopPagePolling=stop;
	if(document.addEventListener)document.addEventListener('visibilitychange',resume);
	if(typeof window!=='undefined')window.addEventListener('focus',resume);
	poll.add(read,1);
}
function proxyName(id, source) {
 var core=source&&source.core,plugins=source&&source.compatibility&&source.compatibility.plugins||[];
 if(!core){var current=plugins.filter(function(p){return p.id===id;})[0];core=current&&current.core;}
 return id==='nailink'&&['dae','sing-box','mihomo'].indexOf(core)>=0?'NaiLink / '+core:names[id];
}
function pill(text, tone) { return el('span', { 'class': 'az-pill az-' + (tone || 'neutral') }, text); }
function number(value) { return typeof value === 'number' && Number.isFinite(value) && value >= 0 ? new Intl.NumberFormat().format(value) : '—'; }
function step(n, title, description, done) {
	return el('li', { 'class': 'az-step' + (done ? ' is-done' : '') }, [
		el('span', { 'class': 'az-step-mark', 'aria-hidden': 'true' }, done ? icon('check') : String(n)),
		el('div', {}, [ el('strong', {}, title), el('p', {}, description) ]) ]);
}
function sourceChoice(i) { return {id:i._new?null:i.id,name:i.name,url:i.url,format:i.format||'unknown',enabled:i.enabled===true}; }
function sourceKey(i) { return i._key || i.id; }
function defaultDNS() {return {domestic:{servers:['223.5.5.5','119.29.29.29'],active:'223.5.5.5'},foreign:{servers:['1.1.1.1','8.8.8.8'],active:'1.1.1.1'}};}
function sameDNS(a,b) {return JSON.stringify(a||defaultDNS())===JSON.stringify(b||defaultDNS());}
function observationHint(o,s) {
	if(operationActive(s))return _('后台操作进行中，统计稍后更新');
	if(!o.available)return _('统计组件尚未就绪');
	if(!flags(s).enabled)return flags(s).paused?_('保护已暂停，统计已暂停'):_('开启或恢复保护后可检测');
	var reasons={protection_disabled:_('保护状态待同步，统计未开始'),backend_disabled:_('过滤后端尚未就绪，统计未开始'),not_managed:_('过滤接入尚未确认，统计未开始'),unsupported_resolver:_('当前 DNS 模式暂不支持统计'),rules_unavailable:_('等待有效规则，统计未开始'),adapter_pending:_('代理接入尚未完成，统计暂未开始'),adapter_state_invalid:_('接入状态读取异常，统计暂未开始'),adapter_changing:_('接入正在更新，统计稍后自动开始')};
	return reasons[o.reason]||_('过滤已开启，统计状态待确认');
}
function verificationKey(self) {
	var o=self.observation||{},s=self.currentStatus||{};
	return JSON.stringify([o.available,o.active,o.reason,o.verification,s.state,s.stale,!!self.busy,self.observationBusy,self.verificationCommands,self.verificationNotice]);
}
function recentKey(self) {
	var o=self.observation||{},r=o.recent||{};
	return JSON.stringify([o.available,o.active,r.enabled,r.items,!!self.recentBusy,!!self.recentIntent,self.recentNotice,self.controlDraft,self.recentSearch]);
}
function controlState(s) {var p=(s||{}).policy||{};return {allowed:(p.allowed||[]).slice().sort(),blocked:(p.blocked||[]).slice().sort()};}
function sameControls(a,b) {return JSON.stringify(a)===JSON.stringify(b);}
function domainName(value) {
	var name=String(value||'').trim().toLowerCase();
	if(/^https?:\/\//.test(name)){var link=new URL(name);if(link.username||link.password)throw Error(_('网址不能包含账号密码。'));name=link.hostname;}
	name=name.replace(/\.$/,'');
	if(name.length>253||name.indexOf('.')<1||/^[0-9.]+$/.test(name)||!/^[a-z0-9][a-z0-9.-]*[a-z0-9]$/.test(name)||name.indexOf('..')>=0||name.split('.').some(function(v){return v.length>63||/^-|-$/.test(v);}))throw Error(_('请输入有效域名；不支持 IP 或通配符。'));
	return name;
}
function coversDomain(a,b){return a===b||b.endsWith('.'+a);}
return view.extend({
	disclosure:function(key,title,body,kind){
		var self=this;self.disclosures=self.disclosures||{};
		return el('details',{id:'az-detail-'+key,'class':kind||'az-help-details',open:self.disclosures[key]?true:null,toggle:function(e){self.disclosures[key]=e.target.open;}},[
			el('summary',{},[title,el('span',{'class':'az-chevron','aria-hidden':'true'})]),el('div',{'class':'az-help-body'},body)]);
	},
	pageKey:function(){return this.enhanceOpen?'enhance':this.controlsOpen?'controls':this.rulesOpen?'rules':'overview';},
	unstagedEdits:function(){return !!(this.dnsForm&&this.dnsFormSaved&&JSON.stringify(this.dnsForm)!==this.dnsFormSaved||this.customSource&&(this.customSource.name||this.customSource.url));},
	selectPage:function(page,fromHistory){
		var self=this;if(['overview','rules','controls','enhance'].indexOf(page)<0)return Promise.resolve();
		self.enhanceOpen=page==='enhance';self.rulesOpen=page==='rules';self.controlsOpen=page==='controls';
		if(!fromHistory&&typeof window!=='undefined'&&window.history&&window.location&&window.location.hash!=='#adzero/'+page)window.history.pushState(null,'','#adzero/'+page);
		self.paint('az-page-title');
		if(page==='rules'&&!self.sourcesData)return self.loadSources();
		if(page==='enhance')return self.loadEnhance();
		return Promise.resolve();
	},
	renderConnection:function(s){
		var self=this,inv=s.compatibility||{},list=(inv.plugins||[]).filter(function(p){return p.installed;}),a=s.adapter||{},f=flags(s),selection=connectionSelection(s),current=list.filter(function(p){return p.id===a.plugin;})[0];
		return el('details',{'class':'az-card az-connection',id:'az-connection',open:self.connectionOpen?true:null,toggle:function(e){self.connectionOpen=e.target.open;}},[
			el('summary',{},[el('div',{'class':'az-summary-title'},[icon('shield'),el('div',{},[el('strong',{},_('代理接入')),el('p',{},selection.pending?(proxyName(selection.plugin,s)||(selection.plugin==='none'?_('路由器 DNS'):_('接入待确认')))+_(' · 待同步'):a.plugin&&a.plugin!=='none'?(proxyName(a.plugin,s))+_(' · ')+(f.enabled&&pluginRunning(current)?_('配置已应用'):_('接入待确认')):_('自动识别当前代理，保留原生 DNS 路径'))])]),el('span',{'class':'az-chevron','aria-hidden':'true'})]),
			el('div',{'class':'az-disclosure-body'},[
				list.length?list.map(function(p){var running=pluginRunning(p),selected=f.enabled&&!selection.pending&&p.id===a.plugin&&running;return el('div',{'class':'az-connection-row'},[
					el('div',{},[el('strong',{},proxyName(p.id,p)),el('p',{},p.adapter_supported===false?_('已识别；当前仅检测，尚不接管。'):p.core_checked===false?_('内核状态待确认，请检查原插件。'):selected?_('已接入；实际过滤效果可在电脑检测。'):p.runtime==='stopped'?_('内核已停止，保留原有设置。'):running?_('已识别正在运行的代理。'):_('尚未确认运行状态。'))]),
					pill(selected?_('当前接入'):running?_('运行中'):p.runtime==='stopped'?_('已停止'):_('待确认'),selected?'positive':p.core_checked===false?'attention':'neutral')]);}):el('p',{'class':'az-empty'},_('未检测到代理插件。确认系统 DNS 可用后，可使用路由器 DNS 过滤。')),
				selection.pending?el('p',{'class':'az-inline-warning'},selection.detail):null,
				el('p',{'class':'az-field-hint'},_('切换时先停止旧代理，再启动新代理。多个代理或未知内核状态下暂停自动接管，避免改变正在工作的网络。'))
			])]);
	},
	busy: null, currentStatus: null, lastChecked: null, notice: null, domainInput: '', domainResult: null, pauseSeconds: 300,
	pauseButtonText:function(){return _('暂停 ')+this.pauseSeconds/60+_(' 分钟');},
	observation:null, observationBusy:false, verificationCommands:'', verificationNotice:null, networkOpen:false,
	draft: null, applyingDraft: null,
	controlsOpen:false,controlDraft:null,controlSearch:'',controlFilter:'all',controlInput:'',controlKind:'blocked',recentSearch:'',statsBusy:false,statsIntent:null,statsNotice:null,
	openControls:function(){this.enhanceOpen=false;this.rulesOpen=false;this.controlsOpen=true;this.notice=null;this.paint();return Promise.resolve();},
	loadEnhance:function(quiet){
		var self=this,epoch=self.pageEpoch;
		if(self.enhanceRequest&&self.enhanceRequestEpoch===epoch)return self.enhanceRequest;
		self.enhanceRequestEpoch=epoch;
		if(!quiet){self.enhanceError=null;self.paint();}
		function current(){return !self.pageClosed&&epoch===self.pageEpoch;}
		function paintRuntime(){var node=document.getElementById('az-enhance-runtime'),results=document.getElementById('az-enhance-dns-results');if(node)dom.content(node,[self.renderEnhanceRuntime()]);if(results&&self.enhanceResultsKey!==self.dnsResultsKey())dom.content(results,[self.renderDNSTests()]);self.updateDNSTestActions();}
		var request=enhanceCalls.read().then(function(r){
			if(!current())return;
			if(!r||!r.ok||!r.data)throw Error(_('DNS 增强状态读取失败。'));
			var hadDraft=!!self.enhanceDraft,refreshPristine=hadDraft&&!self.enhanceDirty()&&JSON.stringify(self.enhanceDraft.before)!==JSON.stringify(r.data.config);
			self.enhanceData=r.data;self.enhanceError=null;
			if(!hadDraft||refreshPristine)self.enhanceDraft={before:JSON.parse(JSON.stringify(r.data.config)),after:JSON.parse(JSON.stringify(r.data.config))};
			// A background retry after the initial read failed must also expose
			// the newly available editor, not only look for a missing status node.
			if(!quiet||!hadDraft||refreshPristine)self.paint();else paintRuntime();
		}).catch(function(e){
			if(!current())return;
			self.enhanceError=String(e.message||e);
			if(!quiet||!self.enhanceDraft)self.paint();else paintRuntime();
		}).then(function(){if(self.enhanceRequest===request)self.enhanceRequest=null;});
		self.enhanceRequest=request;return request;
	},
	enhanceDirty:function(){return this.enhanceDraft&&JSON.stringify(this.enhanceDraft.before)!==JSON.stringify(this.enhanceDraft.after);},
	dnsEditing:function(){return !!(this.dnsForm&&this.dnsFormSaved&&JSON.stringify(this.dnsForm)!==this.dnsFormSaved);},
	dnsTestHint:function(){
		if(this.draftCount()||this.controlCount())return _('先应用上方待应用更改，再测试 DNS。');
		if(this.dnsEditing())return _('DNS 地址尚未暂存，请先加入待应用并应用。');
		if(this.enhanceDirty())return _('先保存增强设置，再测试 DNS。');
		return _('测试统一配置的地址；并发组无需切换备用。');
	},
	dnsTestBlocked:function(){return !!(this.enhanceDirty()||this.draftCount()||this.controlCount()||this.dnsEditing());},
	dnsResultsKey:function(){var d=this.enhanceData||{},s=this.currentStatus||{};return JSON.stringify([d.profile,d.tests,(this.draft||{}).dns,(this.enhanceDraft||{}).before,((s.compatibility||{}).dns||{}).active_proxies,(s.adapter||{}).plugin,((s.adapter||{}).dns_policy||{}).passwall_dns_mode,(s.policy||{}).routing_enabled]);},
	dnsPolicyError:function(profile){
		if(!profile)return null;
		var s=this.currentStatus||{},active=((s.compatibility||{}).dns||{}).active_proxies||[],plugin=active.length===1?active[0]:(s.adapter||{}).plugin,mode=((s.adapter||{}).dns_policy||{}).passwall_dns_mode,parallel=(profile.foreign||{}).mode==='parallel'||profile.foreign_mode==='parallel';
		if(plugin==='passwall'&&parallel&&mode&&mode!=='tcp')return _('当前 PassWall DNS 模式为 ')+mode+_('，不支持国外并发。请改为“单一 + 备用”；国内仍可并发，无需更换原引擎。');
		if(!profile.proxy_port&&(plugin==='passwall'&&(parallel||profile.strict&&mode==='tcp')||plugin==='nailink'&&(parallel||profile.strict)))return _('此组合需要当前代理的本机无认证 SOCKS5 端口。请填写真实监听端口，或关闭国外并发／严格模式。');
		return null;
	},
	updateDNSTestActions:function(){
		var test=document.getElementById('az-dns-test'),hint=document.getElementById('az-enhance-test-hint'),dirty=document.getElementById('az-enhance-dirty'),s=this.currentStatus||{};
		var validation=document.getElementById('az-dns-validation'),stage=document.getElementById('az-dns-stage'),issue=this.dnsPolicyError(this.dnsForm);if(validation)dom.content(validation,[issue||'']);if(stage)stage.disabled=!!(issue||!this.canStage()||this.sourcesLoading||!(this.sourcesData)||this.sourcesData.pending);
		if(test)test.disabled=!!(this.busy||s.stale||operationActive(s)||this.dnsTestBlocked());
		if(hint)dom.content(hint,[this.dnsTestHint()]);
		if(dirty)dom.content(dirty,[this.enhanceDirty()?_('增强设置尚未保存'):_('增强设置已保存')]);
	},
	renderDNSTests:function(){
		var self=this,d=self.enhanceData||{},result=d.tests||{},saved=d.profile||(self.sourcesData||{}).dns,profile=self.draft&&self.draft.dns||saved,pending=!!(self.draft&&self.draft.dns&&!sameDNS(self.draft.dns,self.draft.dns_before));
		self.enhanceResultsKey=self.dnsResultsKey();
		var s=self.currentStatus||{},active=((s.compatibility||{}).dns||{}).active_proxies||[],plugin=active.length===1?active[0]:(s.adapter||{}).plugin,mode=((s.adapter||{}).dns_policy||{}).passwall_dns_mode;
		var current=!!(saved&&result.profile&&sameDNS(saved,result.profile)&&result.plugin===plugin&&result.routing_enabled===!!(s.policy||{}).routing_enabled&&(plugin!=='passwall'||result.passwall_mode===(mode||'tcp'))),port={proxy_port:saved&&saved.proxy_port!=null?saved.proxy_port:(d.config||(self.enhanceDraft||{}).before||{}).proxy_port||0},rows=result.groups||{};
		return el('div',{'class':'az-dns-test-grid'},['domestic','foreign'].map(function(g){var group=profile&&profile[g];return el('section',{'class':'az-dns-test-group','aria-label':g==='domestic'?_('国内 DNS 地址与测试结果'):_('国外 DNS 地址与测试结果')},[
			el('div',{'class':'az-dns-group-heading'},[el('h4',{},g==='domestic'?_('国内组'):_('国外组')),el('small',{},pending?_('待应用'):(group&&group.mode==='parallel'?_('并发 · '):'')+(g==='domestic'?((rows[g]||[]).some(function(r){return r.path==='direct_tcp';})?'TCP':current?'UDP':_('按接入路径')):_('经 SOCKS5')))]),
			group&&Array.isArray(group.servers)&&group.servers.length?group.servers.map(function(address){var sample=(rows[g]||[]).find(function(t){return t.address===address;}),valid=current&&(g==='domestic'||result.proxy_port===port.proxy_port),status=pending?_('待应用'):!valid||!sample?sample?_('需重测'):_('尚未测试'):sample.skipped?_('已跳过'):sample.ok?number(sample.ms)+' ms':_('未通过');return el('div',{'class':'az-dns-test-result'},[
				el('span',{'class':'az-dns-address',translate:'no'},[address,group.mode==='parallel'?el('small',{},_('并发')):address===group.active?el('small',{},pending?_('首选'):_('当前')):null]),el('span',{'class':!pending&&valid&&sample&&!sample.skipped?(sample.ok?'az-test-passed':'az-test-failed'):'az-test-skipped'},status)]);}):el('p',{'class':'az-test-empty'},_('DNS 地址尚未读取')),
			g==='foreign'&&!port.proxy_port?el('p',{'class':'az-dns-group-hint'},_('填入本机 SOCKS5 端口后可测试')):null]);}));
	},
	renderEnhanceRuntime:function(){var d=this.enhanceData||{},r=d.runtime||{},counts=r.counts||{},recovery=r.state==='recovery_required';if(this.enhanceError)return el('div',{'class':'az-enhance-status'},[pill(_('增强状态未更新'),'attention'),el('p',{'class':'az-inline-warning'},_('暂时无法取得新状态。编辑已保留，后台将继续读取。'))]);return el('div',{'class':'az-enhance-status'},[
		el('div',{'class':'az-enhance-status-line'},[pill(r.state==='active'?(r.inspection&&r.inspection.degraded?_('部分别名核对未完成'):_('增强入口运行中')):recovery?_('增强入口退出待确认'):_('增强入口未接入'),r.state==='active'&&!(r.inspection&&r.inspection.degraded)?'positive':recovery||r.inspection&&r.inspection.degraded?'attention':'neutral'),el('span',{'class':'az-field-hint'},r.state==='active'?({passwall:'PassWall',nailink:'NaiLink',openclash:'OpenClash'}[r.plugin]||_('路由器 DNS')):_('原生 DNS 路径保留'))]),
		r.state==='active'&&r.inspection&&r.inspection.degraded?el('p',{'class':'az-inline-warning'},_('部分请求未完成别名核对，已沿用 OpenClash 原生应答。可检查本机控制接口与下方适用范围。')):null,
		recovery?el('p',{'class':'az-inline-warning'},_('旧 DNS 会话清理尚未确认，恢复记录已保留。请查看系统日志，不要手动删除记录。')):null,
		el('div',{'class':'az-enhance-counters','aria-label':_('本次增强入口统计')},[el('span',{},[_('别名拦截'),el('strong',{},number(counts.cname))]),el('span',{},[_('设备附加拦截'),el('strong',{},number(counts.device))])])]);},
	renderEnhance:function(){
		var self=this,d=self.enhanceDraft,editable=!self.busy&&!operationActive(self.currentStatus||{})&&!(self.currentStatus||{}).stale;
		if(!d)return el('section',{'class':'az-card az-enhance-loading','aria-busy':self.enhanceError?'false':'true',role:self.enhanceError?'alert':'status'},[el('h3',{},self.enhanceError?_('增强设置读取失败'):_('正在读取增强设置…')),el('p',{},self.enhanceError||_('设置读取完成后即可编辑，当前网络继续使用原有配置。')),el('button',{type:'button','class':'az-button',disabled:!self.enhanceError?true:null,click:function(){return self.loadEnhance();}},self.enhanceError?_('重新读取'):_('读取中…'))]);
		var s=d.after;
		function updateDirty(){self.updateDNSTestActions();}
		function toggle(key,label,hint){return el('label',{'class':'az-setting-row'},[el('span',{'class':'az-setting-copy'},[el('strong',{},label),hint?el('small',{},hint):null]),el('span',{'class':'az-setting-switch'},[el('input',{id:'az-enhance-'+key,type:'checkbox',role:'switch',checked:s[key]?true:null,disabled:!editable?true:null,change:function(e){s[key]=e.target.checked;updateDirty();}}),el('span',{'class':'az-toggle-track','aria-hidden':'true'},el('span',{'class':'az-toggle-thumb'}))])]);}
		return el('div',{'class':'az-enhance-page'+(s.devices.length?' has-devices':'')},[
			el('div',{'class':'az-enhance-workbench'},[
			el('section',{'class':'az-card az-enhance-entry'},[el('div',{'class':'az-section-heading'},[el('h3',{},_('增强过滤')),el('span',{'class':'az-section-caption'},_('可选功能'))]),el('p',{'class':'az-section-intro'},_('补充检查别名与设备规则，沿用原生上游。')),
				el('div',{id:'az-enhance-runtime','aria-live':'polite'},[self.renderEnhanceRuntime()]),
				toggle('enabled',_('增强 DNS 入口'),(self.enhanceData.profile||{}).strict?_('严格模式期间保留此选择，暂不接入'):_('仅接入默认 IPv4 局域网 DNS')),
				toggle('cname',_('CNAME 别名过滤'),_('检查 CNAME 与 HTTPS/SVCB AliasMode，入口启用后生效')),
				self.disclosure('enhance-scope',_('适用范围'),[el('p',{},_('支持确认可用的默认 IPv4 LAN、PassWall、NaiLink 原生监听及 OpenClash 常规 DNS 路径。OpenClash 保留 Fake-IP，通过本机原生解析接口核对别名。')),el('p',{},_('继续使用原生上游和缓存。严格模式、OpenClash TUN、已有 NaiLink 自定义监听、未知 ACL 或代理切换期间不叠加入口。独立加密 DNS、IPv6 等未接入路径不受附加规则控制。'))])]),
			]),
			el('section',{'class':'az-card az-enhance-devices'},[el('div',{'class':'az-section-heading'},[el('div',{},[el('h3',{},_('设备附加规则')),el('p',{'class':'az-section-intro'},_('为固定 IPv4 设备补充拦截或豁免。'))]),el('button',{id:'az-device-add',type:'button','class':'az-button az-button-compact',disabled:!editable||s.devices.length>=32?true:null,click:function(){s.devices.push({ip:'',name:'',enabled:true,allowed:[],blocked:[]});self.paint('az-device-'+(s.devices.length-1)+'-name');}},_('添加设备'))]),
				self.disclosure('device-scope',_('规则作用范围'),el('p',{},_('放行只豁免该设备的附加拦截和 CNAME 检查，不能撤销全局规则或其他插件的拦截；全局误杀请到“访问控制”处理。'))),
				!s.devices.length?el('p',{'class':'az-device-empty'},_('暂无设备规则，所有设备沿用全局过滤。')):null,
				el('div',{},s.devices.map(function(device,index){
					function field(key,label,area){return el('label',{'class':'az-enhance-field'},[el('span',{},label),el(area?'textarea':'input',{id:'az-device-'+index+'-'+key,type:area?null:'text',rows:area?3:null,value:area?device[key].join('\n'):device[key],disabled:!editable?true:null,input:function(e){device[key]=area?e.target.value.split(/\r?\n/).map(function(v){return v.trim().toLowerCase();}).filter(Boolean):e.target.value;updateDirty();}},area?device[key].join('\n'):null)]);}
					return el('div',{'class':'az-device-card'},[el('div',{'class':'az-enhance-grid'},[field('name',_('设备名称')),field('ip',_('固定 IPv4 地址')),field('blocked',_('该设备附加拦截（每行一个域名）'),true),field('allowed',_('该设备附加放行（每行一个域名）'),true)]),
						el('div',{'class':'az-action-row'},[el('label',{'class':'az-enhance-check'},[el('input',{type:'checkbox',checked:device.enabled?true:null,disabled:!editable?true:null,change:function(e){device.enabled=e.target.checked;updateDirty();}}),el('span',{},_('启用设备附加规则'))]),el('button',{type:'button','class':'az-button az-button-quiet',disabled:!editable?true:null,click:function(){s.devices.splice(index,1);self.paint();}},_('移除设备'))])]);
				}))]),
			el('div',{'class':'az-enhance-savebar'},[el('span',{id:'az-enhance-dirty','class':'az-field-hint'},self.enhanceDirty()?_('增强设置尚未保存'):_('增强设置已保存')),el('div',{'class':'az-action-row'},[el('button',{type:'button','class':'az-button az-button-quiet',disabled:!editable?true:null,click:function(){self.enhanceDraft=null;return self.loadEnhance();}},_('撤销编辑')),el('button',{id:'az-enhance-save',type:'button','class':'az-button az-button-primary',disabled:!editable?true:null,click:function(){return self.runEnhance('save');}},_('保存增强设置'))])])]);
	},
	runEnhance:function(action,receipt){
		var self=this;if(self.busy||!receipt&&(operationActive(self.currentStatus||{})||action==='test'&&self.dnsTestBlocked()))return Promise.resolve();self.busy=action==='save'?'enhance':'dns-test';self.notice=null;self.paint();
		return (receipt?Promise.resolve(receipt):action==='save'?enhanceCalls.save(JSON.stringify(self.enhanceDraft)):enhanceCalls.test()).then(function(r){return trackJob(r,'enhance',action,self);}).then(function(){if(action==='save')self.enhanceDraft=null;self.notice={kind:'success',text:action==='save'?_('增强设置已保存；是否接入以入口运行状态为准。'):_('DNS 连通性测试完成。')};}).catch(function(e){self.notice={kind:'error',text:_('DNS 增强操作未完成。'),detail:String(e.message||e)};}).then(function(){finishJob(self);return self.loadEnhance();});
	},
	controlCount:function(){var d=this.controlDraft;if(!d)return 0;return ['allowed','blocked'].reduce(function(n,k){return n+d.before[k].filter(function(v){return d.after[k].indexOf(v)<0;}).length+d.after[k].filter(function(v){return d.before[k].indexOf(v)<0;}).length;},0);},
	canControl:function(){var s=this.currentStatus||{};return !this.busy&&!s.stale&&!operationActive(s)&&!this.draftCount()&&!(s.policy||{}).pending&&(flags(s).enabled||s.state==='disabled'&&!s.managed);},
	stageControls:function(kind,raw,remove){
		if(!this.canControl())return Promise.resolve();
		try{
			var d=this.controlDraft?JSON.parse(JSON.stringify(this.controlDraft)):{before:controlState(this.currentStatus),after:controlState(this.currentStatus)};
			var names=String(raw||'').split(/\r?\n/).filter(function(v){return v.trim();}).map(domainName);if(!names.length)throw Error(_('请填写域名，每行一个。'));
			var other=kind==='allowed'?'blocked':'allowed';
			names.forEach(function(name){
				if(remove)d.after[kind]=d.after[kind].filter(function(v){return v!==name;});
				else {d.after[other]=d.after[other].filter(function(v){return v!==name;});if(d.after[kind].indexOf(name)<0)d.after[kind].push(name);}
			});
			['allowed','blocked'].forEach(function(k){d.after[k].sort();if(d.after[k].length>100)throw Error(_('每份自定义名单最多 100 项。'));});
			if(d.after.allowed.some(function(a){return d.after.blocked.some(function(b){return coversDomain(a,b)||coversDomain(b,a);});}))throw Error(_('放行与拦截范围重叠，请先移除冲突规则。'));
			this.controlDraft=sameControls(d.before,d.after)?null:d;this.controlInput='';this.notice=null;this.paint();
		}catch(e){this.notice={kind:'error',text:String(e.message||e)};this.paint();}
		return Promise.resolve();
	},
	renderControlDraft:function(){
		var self=this;return el('section',{'class':'az-draft-bar',role:'region','aria-label':_('访问控制待应用')},[
			el('div',{},[el('strong',{},self.controlCount()+_(' 项访问控制更改待应用')),el('p',{},_('当前网络继续使用原名单。改好后统一应用，只同步一次。'))]),
			el('div',{'class':'az-action-row'},[
				el('button',{id:'az-controls-discard',type:'button','class':'az-button az-button-quiet',disabled:self.busy||self.jobUncertain?true:null,click:function(){self.controlDraft=null;self.notice=null;self.paint();}},_('撤销更改')),
				el('button',{id:'az-controls-apply',type:'button','class':'az-button az-button-primary',disabled:!self.canControl()?true:null,click:function(){return self.applyControls();}},_('应用访问控制'))])]);
	},
	applyControls:function(receipt){
		var self=this;if(!receipt&&(!self.canControl()||!self.controlCount()))return Promise.resolve();
		var expected=self.controlDraft?JSON.parse(JSON.stringify(self.controlDraft)):null,failure=null;
		self.busy='controls';self.notice=null;self.jobUncertain=false;self.paint();
		return (receipt?Promise.resolve(receipt):controlsApply(JSON.stringify(expected))).then(function(reply){return trackJob(reply,'controls',null,self);}).catch(function(e){failure=String(e.message||e);}).then(function(){return taskStatus(self);}).then(function(s){
			self.currentStatus=s;self.lastChecked=new Date();self.domainResult=null;
			if(failure||['disabled','configured_unverified'].indexOf(s.state)<0||(s.policy||{}).pending||expected&&!sameControls(controlState(s),expected.after))throw Error(failure||_('名单尚未核对一致，待应用选择已保留。'));
			self.controlDraft=null;self.notice={kind:'success',text:s.state==='disabled'?_('访问控制已保存，开启保护后生效。'):_('访问控制已统一应用。')};
		}).catch(function(e){self.notice={kind:'error',text:_('访问控制未确认应用。'),detail:String(e.message||e)};}).then(function(){finishJob(self);self.paint();return self.refreshObservation();});
	},
	renderControlList:function(){
		var self=this,policy=self.controlDraft?self.controlDraft.after:controlState(self.currentStatus),before=self.controlDraft?self.controlDraft.before:policy,items=[];
		['blocked','allowed'].forEach(function(k){policy[k].forEach(function(name){items.push({kind:k,domain:name,pending:before[k].indexOf(name)<0});});});
		items=items.filter(function(i){return (self.controlFilter==='all'||self.controlFilter===i.kind)&&i.domain.indexOf((self.controlSearch||'').toLowerCase())>=0;});
		if(!items.length)return el('div',{'class':'az-empty'},[el('strong',{},_('没有符合条件的自定义规则')),el('p',{},_('可在下方添加域名，也可从最近拦截中检查并放行。'))]);
		return el('div',{'class':'az-control-list'},items.map(function(i){return el('div',{'class':'az-control-row'},[
			el('span',{'class':'az-control-kind '+(i.kind==='blocked'?'is-blocked':'is-allowed')},i.kind==='blocked'?_('拦截'):_('放行')),
			el('div',{'class':'az-control-domain'},[el('strong',{translate:'no'},i.domain),el('small',{},i.pending?_('待应用 · 包含子域'):(function(){var t=(((self.currentStatus||{}).policy||{}).temporary||[]).find(function(t){return t.scope===i.domain;});return t?_('临时放行 · 剩余约 ')+Math.ceil(t.remaining/60)+_(' 分钟，到期自动恢复'):_('已保存 · 包含子域');})())]),
			el('button',{type:'button','class':'az-button az-button-quiet','aria-label':_('移除规则 ')+i.domain,disabled:!self.canControl()?true:null,click:function(){return self.stageControls(i.kind,i.domain,true);}},_('移除'))
		]);}));
	},
	renderControls:function(s){
		var self=this,f=flags(s),editable=self.canControl();
		return el('div',{'class':'az-controls-page'},[
			self.draftCount()?el('p',{'class':'az-inline-warning'},_('过滤规则或 DNS 有待应用更改，请先应用或撤销，再修改访问控制。')):null,
			el('div',{id:'az-live-recent'},[self.renderRecent()]),
			el('section',{'class':'az-card az-domain-card'},[
				el('div',{'class':'az-section-heading'},[el('h3',{},_('域名查询')),pill(_('当前生效规则'))]),
				el('label',{'for':'az-domain','class':'az-form-label',id:'az-domain-help'},_('域名或网址')),
				el('div',{'class':'az-domain-form'},[el('input',{id:'az-domain',type:'text',value:self.domainInput,placeholder:'example.com',maxlength:2048,autocomplete:'off',spellcheck:'false',disabled:self.busy||s.stale||!f.enabled?true:null,input:function(e){self.domainInput=e.target.value;self.domainResult=null;var r=document.getElementById('az-domain-result');if(r)dom.content(r,[]);},keydown:function(e){if(e.key==='Enter'){e.preventDefault();self.lookupDomain();}}}),
					el('button',{id:'az-lookup',type:'button','class':'az-button az-button-primary',disabled:self.busy||s.stale||!f.enabled?true:null,click:function(){return self.lookupDomain();}},_('查询域名'))]),
				!f.enabled?el('p',{'class':'az-field-hint'},_('开启或恢复保护后可查询当前生效规则；关闭时仍可编辑自定义名单。')):null,
				el('div',{id:'az-domain-result','aria-live':'polite'},self.renderLookup())]),
			el('section',{'class':'az-card az-controls-card'},[
				el('div',{'class':'az-section-heading'},[el('h3',{},_('自定义规则')),pill((self.controlDraft?self.controlDraft.after:controlState(s)).blocked.length+_(' 项拦截')+' · '+(self.controlDraft?self.controlDraft.after:controlState(s)).allowed.length+_(' 项放行'))]),
				el('div',{'class':'az-control-toolbar'},[
					el('label',{'class':'az-control-search'},[el('span',{},_('搜索域名')),el('input',{id:'az-controls-search',type:'search',value:self.controlSearch,placeholder:_('按域名筛选'),input:function(e){self.controlSearch=e.target.value;dom.content(document.getElementById('az-control-list'),[self.renderControlList()]);}})]),
					el('label',{},[el('span',{},_('规则类型')),el('select',{id:'az-control-filter',change:function(e){self.controlFilter=e.target.value;dom.content(document.getElementById('az-control-list'),[self.renderControlList()]);}},[['all',_('全部')],['blocked',_('拦截')],['allowed',_('放行')]].map(function(i){return el('option',{value:i[0],selected:self.controlFilter===i[0]?true:null},i[1]);}))])]),
				el('div',{id:'az-control-list'},[self.renderControlList()]),
				el('div',{'class':'az-control-add'},[
					el('label',{'for':'az-control-input','class':'az-form-label'},_('添加域名 · 每行一个')),
					el('textarea',{id:'az-control-input',rows:3,value:self.controlInput,placeholder:'example.com\nexample.org',disabled:!editable?true:null,spellcheck:'false',input:function(e){self.controlInput=e.target.value;}},self.controlInput),
					el('div',{'class':'az-action-row'},[el('label',{},[el('span',{'class':'az-sr-only'},_('新增规则类型')),el('select',{id:'az-control-kind',disabled:!editable?true:null,change:function(e){self.controlKind=e.target.value;}},[['blocked',_('拦截域名')],['allowed',_('放行域名')]].map(function(i){return el('option',{value:i[0],selected:self.controlKind===i[0]?true:null},i[1]);}))]),
						el('button',{id:'az-control-add',type:'button','class':'az-button az-button-primary',disabled:!editable?true:null,click:function(){return self.stageControls(self.controlKind,self.controlInput);}},_('加入待应用'))]),
					el('p',{'class':'az-field-hint'},_('每条规则包含该域名及子域；放行与拦截不能范围重叠。移除自定义拦截后，订阅规则仍可能拦截该域名。放行只作用于 AdZero。'))])])
		]);
	},
	renderStatsActions:function(){var self=this;return el('div',{'class':'az-stat-actions'},[
		el('span',{id:'az-stats-caption','class':'az-live-caption'},_('累计统计 · 每秒刷新 · 后台页面暂停刷新')),
		el('button',{id:'az-stats-reset',type:'button','class':'az-button az-button-quiet',disabled:self.statsBusy||self.statsIntent||self.busy||!(self.observation||{}).available?true:null,click:function(){return self.resetStats();}},self.statsBusy||self.statsIntent?_('正在清零…'):_('清零统计'))]);},
	resetStats:function(){var self=this;if(self.statsBusy||self.statsIntent||self.busy||!(self.observation||{}).available)return Promise.resolve();self.statsBusy=true;self.statsNotice=null;self.updateStatsActions();
		return observationCalls.reset().then(function(r){if(!r||!r.ok||!/^[a-f0-9]{32}$/.test(r.token||''))throw Error(r&&r.message||_('未收到清零回执。'));self.statsIntent={token:r.token,at:Date.now()};return self.refreshObservation();}).catch(function(e){self.statsNotice=String(e.message||e);}).then(function(){self.statsBusy=false;self.updateStatsActions();});},
	updateStatsActions:function(){var a=document.getElementById('az-stats-actions'),n=document.getElementById('az-stats-notice'),key=JSON.stringify([!!(this.observation||{}).available,!!this.statsBusy,!!this.statsIntent,!!this.busy,this.statsNotice]);if(key===this.statsPaintKey)return;this.statsPaintKey=key;if(a)dom.content(a,[this.renderStatsActions()]);if(n)dom.content(n,[this.statsNotice||'']);},
	renderMetrics:function(s){
		var o=this.observation||{},available=o.available===true,hasCounts=available&&(o.active===true||o.queries>0||!!o.reset_token),ready=flags(s).enabled&&(s.backend||{}).state==='ready';
		var selection=connectionSelection(s),connection=available&&o.connection?o.connection:null;
		if(selection.known&&connection&&connection.plugin!==selection.plugin)connection=null;
		var adapter=connection||s.adapter||{},active=proxyName(adapter.plugin,s)||(adapter.plugin==='none'?_('路由器 DNS'):_('接入待确认')),
			synced=connection?connection.confirmed===true:adapter.stage==='configured_unverified'&&(!adapter.runtime||adapter.runtime.state==='ready'),ratio=available&&o.queries>0?(100*o.blocked/o.queries).toFixed(1)+'%':'—';
		var connectionHint=adapter.runtime&&adapter.runtime.state==='missing'?(adapter.runtime_retry_exhausted?_('DNS 规则未加载，请重新加载'):_('DNS 规则未加载，等待自动同步')):adapter.runtime&&adapter.runtime.state==='unknown'?_('配置已保存，DNS 加载待确认'):synced?_('规则已同步'):_('接入待确认');
		var detected=(((s.compatibility||{}).dns||{}).active_proxies||[]);
		if(adapter.plugin==='none'&&detected.length===1&&names[detected[0]]){active=names[detected[0]];synced=false;}
		if(selection.pending){active=proxyName(selection.plugin,s)||(selection.plugin==='none'?_('路由器 DNS'):_('接入待确认'));synced=false;connectionHint=_('代理接入待同步');}
		return el('div',{'class':'az-stats-grid az-live-grid'},[
			el('section',{'class':'az-card az-stat az-stat-accent'},[el('span',{'class':'az-stat-label'},_('已拦截 DNS 请求')),
				el('strong',{'class':'az-number'},number(hasCounts?o.blocked:null)),el('p',{},available&&o.active?_('DNS请求拦截占比 ')+ratio:observationHint(o,s)),el('small',{'class':'az-stat-hint'},_('已拦截 ÷ 已观察请求，不代表网页广告拦截率。'))]),
			el('section',{'class':'az-card az-stat'},[el('span',{'class':'az-stat-label'},_('已观察 DNS 请求')),
				el('strong',{'class':'az-number'},number(hasCounts?o.queries:null)),el('p',{},o.reset_token?_('自上次清零 · 局域网明文 DNS'):available&&!o.active&&o.queries>0?_('本次开机累计 · 当前统计已暂停'):_('本次开机 · 局域网明文 DNS'))]),
			el('section',{'class':'az-card az-stat'},[el('span',{'class':'az-stat-label'},_('规则域名')),
				el('strong',{'class':'az-number'},number(ready?(connection&&typeof connection.domains==='number'?connection.domains:s.backend.domains):null)),el('p',{},_('当前加载的有效域名'))]),
			el('section',{'class':'az-card az-stat'},[el('span',{'class':'az-stat-label'},_('当前接入')),
				el('strong',{'class':'az-stat-value az-current-adapter',translate:'no'},operationActive(s)?_('同步中'):flags(s).enabled?active:_('未开启')),el('p',{},operationActive(s)?_('完成后自动更新接入'):flags(s).enabled?connectionHint:_('开启后自动识别'))])
		]);
	},
	renderLatency:function(){
		var o=this.observation||{},l=o.latency;
		if(!o.available||!o.active||!l||l.scope!=='lan_observed')return null;
		var normal=l.normal||{},blocked=l.blocked||{},unanswered=l.unanswered||{};
		function duration(v){return typeof v==='number'&&isFinite(v)&&v>=0?v.toFixed(1)+' ms':'—';}
		return el('div',{'class':'az-latency-summary',title:_('当前接入期间采集到的 DNS 请求至应答耗时，包含路由器缓存；不是上游纯耗时或缓存命中率。采集遗漏也可能导致未观察到应答。')},[
			el('span',{},[_('正常应答 '),el('strong',{},duration(normal.samples>0?normal.average_ms:null))]),
			el('span',{},[_('拦截应答 '),el('strong',{},duration(blocked.samples>0?blocked.average_ms:null))]),
			el('span',{},_('慢应答（≥500 ms） ')+number(normal.slow||0)),
			el('span',{},_('15 秒未观察到应答 ')+number((unanswered.udp||0)+(unanswered.tcp||0)))
		]);
	},
	renderRecent:function(){
		var self=this,o=self.observation||{},r=o.recent||{},enabled=r.enabled===true,items=r.items||[];
		self.recentPaintKey=recentKey(self);
		return el('section',{'class':'az-card az-recent-card'},[
			el('div',{'class':'az-section-heading'},[el('div',{},[el('h3',{},_('最近拦截')),el('p',{'class':'az-card-copy'},enabled?_('正在临时记录；重复域名合并显示。'):_('按需查看拦截域名，方便排查误杀。'))]),
				el('button',{id:'az-recent-toggle',type:'button','class':'az-button az-button-quiet',disabled:self.recentBusy||self.recentIntent||!enabled&&(!o.available||!o.active)?true:null,click:function(){return self.setRecent(!enabled);}},self.recentBusy||self.recentIntent?_('处理中…'):enabled?_('停止并清空'):_('查看最近拦截'))]),
			enabled?el('p',{id:'az-recent-expiry','class':'az-live-caption'},_('剩余 ')+number(r.remaining)+_(' 秒 · 最多 30 个域名')):null,
			enabled&&!items.length?el('p',{'class':'az-card-copy'},_('等待新的拦截请求。可查询广告域名测试；命中缓存或没有广告请求时不会新增。')):null,
			enabled?el('label',{'class':'az-recent-search'},[el('span',{},_('搜索拦截域名')),el('input',{id:'az-recent-search',type:'search',value:self.recentSearch,placeholder:_('按域名筛选'),input:function(e){self.recentSearch=e.target.value;dom.content(document.getElementById('az-recent-rows'),[self.renderRecentRows()]);}})]):null,
			enabled?el('div',{id:'az-recent-rows','class':'az-recent-list'},[self.renderRecentRows()]):null,
			self.recentNotice?el('p',{'class':'az-inline-warning',role:'status'},self.recentNotice):null,
			el('p',{'class':'az-field-hint'},_('手动开启后记录 5 分钟，仅保留在路由器内存。到期、停止或配置变化时清空；不记录设备地址，不保存完整浏览日志。'))
		]);
	},
	renderRecentRows:function(){
		var self=this,items=(((self.observation||{}).recent||{}).items||[]).filter(function(i){return i.domain.indexOf((self.recentSearch||'').toLowerCase())>=0;});
		if(!items.length&&self.recentSearch)return el('p',{'class':'az-empty'},_('没有匹配的拦截记录。'));
		return items.map(function(item){return el('div',{'class':'az-recent-row'},[
			el('div',{},[el('strong',{translate:'no'},item.domain),el('p',{'class':'az-card-copy'},(item.qtypes||[]).map(function(t){return t===28?'AAAA':'A';}).join(' / '))]),
			el('div',{'class':'az-recent-count'},[el('strong',{},number(item.count)+_(' 次')),el('small',{},item.at?new Date(item.at*1000).toLocaleTimeString():_('最近拦截'))]),
			el('button',{type:'button','class':'az-button az-button-quiet',disabled:!self.canControl()||!flags(self.currentStatus||{}).enabled?true:null,click:function(){self.domainInput=item.domain;self.paint('az-domain');return self.lookupDomain();}},_('检查并放行'))]);});
	},
	setRecent:function(enabled){
		var self=this;if(self.recentBusy||self.recentIntent)return Promise.resolve();self.recentBusy=true;self.recentNotice=null;
		return observationCalls.recent(enabled).then(function(reply){if(!reply||!reply.ok)throw Error(reply&&reply.message||_('临时记录操作未完成。'));self.recentIntent={enabled:enabled,at:Date.now()};return self.refreshObservation();}).catch(function(e){self.recentIntent=null;self.recentNotice=String(e.message||e);}).then(function(){self.recentBusy=false;var box=document.getElementById('az-live-recent');if(box)dom.content(box,[self.renderRecent()]);});
	},
	renderVerificationSteps:function(v){return el('div',{'class':'az-test-steps','aria-live':'polite'},[pill(v.marker?_('已识别设备'):_('等待设备')),pill(v.blocked?_('拦截符合预期'):_('等待拦截样本')),pill(v.normal?_('正常网站可解析'):_('等待正常样本')),el('span',{},number(v.remaining)+_(' 秒内有效'))]);},
	renderVerification:function(){
		var self=this,o=self.observation||{},v=o.verification||{},selection=connectionSelection(self.currentStatus||{}),enabled=flags(self.currentStatus||{}).enabled,changing=selection.pending||selection.known&&o.connection&&o.connection.plugin!==selection.plugin,passed=o.available&&o.active&&enabled&&!changing&&!(self.currentStatus||{}).stale&&v.state==='passed',waiting=o.available&&o.active&&enabled&&!changing&&!(self.currentStatus||{}).stale&&v.state==='waiting';
		self.verificationPaintKey=verificationKey(self);
		var title=!enabled?_('开启或恢复保护后可检测实际过滤效果。'):changing?_('当前代理接入待同步，完成后可检测。'):passed?_('此设备检测通过'):waiting?_('等待电脑发出检测查询'):!o.available?_('统计组件尚未就绪，广告过滤可继续使用。'):!o.active?observationHint(o,self.currentStatus||{}):v.state==='expired'?_('检测已过期，可重新开始'):_('确认实际过滤效果');
		return el('section',{'class':'az-card az-verification-card'},[
			el('div',{'class':'az-section-heading'},[el('div',{},[el('h3',{},_('效果检测')),el('p',{id:'az-test-description','class':'az-card-copy'},title)]),
				el('button',{id:'az-test-start',type:'button','class':'az-button '+(passed?'az-button-quiet':'az-button-primary'),disabled:self.observationBusy||self.busy||changing||!flags(self.currentStatus||{}).enabled||!o.available||!o.active||waiting?true:null,click:function(){return self.startVerification();}},self.observationBusy?_('准备中…'):passed?_('再次检测'):_('开始检测'))]),
			passed?el('p',{'class':'az-test-success'},[icon('check'),_('已收到此设备的拦截应答和正常解析：'),el('span',{translate:'no'},v.client||'')]):null,
			self.verificationNotice?el('p',{'class':'az-inline-warning',role:'status'},self.verificationNotice):null,
			self.verificationCommands&&!passed?el('div',{'class':'az-test-instructions'},[
				el('p',{},_('在连接此路由器的电脑上打开终端，复制并执行以下三条命令。首条检测标记提示不存在属正常；无需暂停保护，页面会自动核对结果。')),
				el('label',{'for':'az-test-commands','class':'az-sr-only'},_('终端检测命令')),
				el('textarea',{id:'az-test-commands',readonly:true,rows:3,spellcheck:'false',click:function(e){e.target.select();}},self.verificationCommands),
				el('div',{id:'az-test-progress'},waiting?[self.renderVerificationSteps(v)]:[])
			]):null,
			el('details',{'class':'az-stat-scope',open:self.statScopeOpen?true:null,toggle:function(e){self.statScopeOpen=e.target.open;}},[el('summary',{},_('统计范围与隐私')),el('p',{},_('统计记录本次开机或上次清零后的采集期间，局域网 UDP/TCP 53 端口中命中有效规则且收到拦截应答的请求；不代表网页广告个数。加密 DNS、绕过路由器和未观察到的应答不计入。检测仅证明该设备当前 DNS 链路。统计保存在内存，重启路由器清零。默认不记录域名；最近拦截需手动开启，且会自动到期清空。'))])
		]);
	},
	refreshObservation:function(){
		var self=this,panel=self.panelNode,epoch=self.pageEpoch;if(self.pageClosed||self.observationReading)return Promise.resolve();self.observationReading=true;
		function current(){return !self.pageClosed&&epoch===self.pageEpoch;}
		return observationCalls.read().then(function(reply){if(!current())return;if(!reply||!reply.ok)throw Error('unavailable');if(reply.data&&reply.data.available&&(reply.data.verification||{}).state==='idle'&&self.observation&&(self.observation.verification||{}).state!=='idle')self.verificationCommands='';self.observation=reply.data;},function(){if(current())self.observation={available:false,verification:{state:'idle'}};}).catch(function(){if(current())self.observation={available:false,verification:{state:'idle'}};}).then(function(){
			if(!current())return;
			self.observationReading=false;
			if(self.statsIntent){if((self.observation||{}).reset_token===self.statsIntent.token){self.statsIntent=null;self.statsNotice=_('统计已清零，正在重新计数。');}else if(Date.now()-self.statsIntent.at>15000){self.statsIntent=null;self.statsNotice=_('清零完成状态尚未确认，请检查统计组件后再试。');}}
			if(self.recentIntent){if(((self.observation||{}).recent||{}).enabled===self.recentIntent.enabled)self.recentIntent=null;else if(Date.now()-self.recentIntent.at>5000){self.recentIntent=null;self.recentNotice=_('尚未收到临时记录状态，请稍后重试。');}}
			if(panel!==self.panelNode || !document.contains(panel))return;
			self.updateStatsActions();
			var metrics=document.getElementById('az-live-metrics'),verification=document.getElementById('az-live-verification'),recent=document.getElementById('az-live-recent'),health=document.getElementById('az-live-health');
			if(metrics)dom.content(metrics,[self.renderMetrics(self.currentStatus||{})]);
			if(health){var healthContent=self.renderObservationHealth();dom.content(health,healthContent==null?[]:[healthContent]);}
			var latency=document.getElementById('az-live-latency');if(latency){var latencyContent=self.renderLatency();dom.content(latency,latencyContent==null?[]:[latencyContent]);}
			if(recent&&self.recentPaintKey!==recentKey(self)&&(!document.activeElement||!recent.contains(document.activeElement)||!((self.observation||{}).recent||{}).enabled))dom.content(recent,[self.renderRecent()]);
			var expiry=document.getElementById('az-recent-expiry');if(expiry)dom.content(expiry,[_('剩余 ')+number(((self.observation||{}).recent||{}).remaining)+_(' 秒 · 最多 30 个域名')]);
			// Preserve command selection while waiting, but always show the final
			// receipt even if the user leaves that textarea focused after copying.
			if(verification&&self.verificationPaintKey!==verificationKey(self)){
				var v=(self.observation||{}).verification||{},focused=document.activeElement&&document.activeElement.id==='az-test-commands';
				if(focused&&v.state==='waiting'){
					var progress=document.getElementById('az-test-progress'),description=document.getElementById('az-test-description'),start=document.getElementById('az-test-start');
					if(progress)dom.content(progress,[self.renderVerificationSteps(v)]);
					if(description)dom.content(description,[_('等待电脑发出检测查询')]);
					if(start)start.disabled=true;
					self.verificationPaintKey=verificationKey(self);
				}else dom.content(verification,[self.renderVerification()]);
			}
		});
	},
	renderObservationHealth:function(){
		var o=this.observation||{},c=o.current||{};
		if(!o.available||!o.active)return null;
		if(o.connection&&o.connection.runtime_retry_exhausted)return el('p',{'class':'az-inline-warning'},_('自动同步多次未完成，已暂停重试。请检查代理后点击“重新加载”；仍可关闭保护或调整规则。'));
		if(o.connection&&o.connection.runtime&&o.connection.runtime.reason==='dns_sigchld_blocked')return el('p',{'class':'az-inline-warning'},_('代理 DNS 进程的子进程回收信号被阻塞，TCP 查询可能异常。请使用修正版重新加载；广告规则已加载不代表网络链路正常。'));
		if(o.connection&&o.connection.runtime&&o.connection.runtime.state==='missing')return el('p',{'class':'az-inline-warning'},_('代理 DNS 尚未加载当前屏蔽规则，后台将有限重试。也可点击“重新加载”；无需重新下载订阅。'));
		if(c.matched_positive>0||c.matched_error>0)return el('div',{},[
			c.matched_positive>0?el('p',{'class':'az-inline-warning'},_('当前接入期间，有 ')+number(c.matched_positive)+_(' 条命中规则的查询仍返回了 IP 地址。请用“效果检测”核对实际过滤；DNS 查询量增加不等于拦截量增加。')):null,
			c.matched_error>0?el('p',{'class':'az-inline-warning'},_('有 ')+number(c.matched_error)+_(' 条命中规则的查询返回 DNS 失败（SERVFAIL），未计入已拦截。代理可能转换了拒绝应答，也可能存在解析故障；请核对当前代理日志。')):null
		]);
		var handoff=(this.currentStatus||{}).dns_handoff||{};
		if(handoff.reason==='conntrack_missing')return el('p',{'class':'az-inline-warning'},_('缺少 conntrack，旧 DNS 会话暂不能自动切换；请补齐安装依赖。过滤与统计可继续工作。'));
		if(o.connection&&o.connection.plugin==='passwall'){
			var skipped={
				passwall_disabled:_('旧 DNS 会话检查读到 PassWall 尚未启用，暂未处理旧连接；请重新检测当前接入。'),
				dns_redirect_disabled:_('PassWall 未启用默认 DNS 重定向，已跳过旧 DNS 会话自动切换。'),
				passwall_unconfigured:_('未读到 PassWall 默认配置，暂未处理旧 DNS 会话。'),
				config_unavailable:_('无法读取旧 DNS 会话检查所需配置，暂未处理旧连接。'),
				acl_unsupported:_('PassWall 已启用自定义访问控制，已跳过默认 DNS 会话自动切换。'),
				protection_disabled:_('保护开关已关闭，旧 DNS 会话检查已暂停。'),
				backend_disabled:_('广告规则后端已关闭，旧 DNS 会话检查已暂停。'),
				not_managed:_('当前后端未由 AdZero 管理，已跳过旧 DNS 会话处理。'),
				paused:_('保护处于临时暂停状态，旧 DNS 会话检查已暂停。')
			};
			if(skipped[handoff.reason])return el('p',{'class':'az-live-caption'},skipped[handoff.reason]);
		}
		if(handoff.reason==='unsupported_context')return el('p',{'class':'az-live-caption'},_('当前 DNS 方式或访问控制不支持旧会话自动切换，已跳过处理。'));
		if(handoff.state==='ready'&&handoff.repaired>0)return el('p',{'class':'az-live-caption'},_('已切换旧 UDP DNS 会话：')+number(handoff.repaired)+_(' 条。电脑已缓存的旧解析需等待过期；这不代表网站连接已验证。'));
		return el('p',{'class':'az-live-caption'},_('收到新的拦截应答才会增加计数；没有广告查询或设备直接使用缓存时，数字保持不变。'));
	},
	startVerification:function(){
		var self=this;if(self.observationBusy)return Promise.resolve();self.observationBusy=true;self.verificationNotice=null;self.paint();
		return observationCalls.test().then(function(reply){if(!reply||!reply.ok)throw Error(reply&&reply.message||_('检测未能开始。'));self.verificationCommands=reply.commands;return self.refreshObservation();}).catch(function(e){self.verificationNotice=String(e.message||e);}).then(function(){self.observationBusy=false;self.paint('az-test-commands');});
	},
	load: function() {
		// Render first. A slow RPC must not hold LuCI's whole view behind a spinner.
		return Promise.resolve(readSnapshot() || { state: 'loading' });
	},
	renderUpdatePanel:function(){
		var self=this,d=self.updateData||{},hasUpdate=d.ok&&d.available&&d.compatible,checking=self.updateBusy==='check',
			upgrading=self.updateBusy==='upgrade'||self.busy==='upgrade',blocked=self.busy||operationActive(self.currentStatus||{})||self.jobUncertain;
		var content=[el('div',{'class':'az-update-panel-heading'},[
			el('div',{},[el('h3',{id:'az-update-title'},_('在线更新')),
				el('p',{},_('仅检查 GitHub 官方发布包；安装前核对固件、架构与 SHA-256。'))]),
			el('button',{type:'button','class':'az-button az-button-quiet',id:'az-update-close',
				click:function(){self.updateOpen=false;self.updateConfirm=false;self.paint('az-version');}},_('关闭'))])];
		if(checking)content.push(el('p',{role:'status'},_('正在安全检查官方发布版本及安装包…')));
		else if(upgrading)content.push(el('p',{role:'status'},_('升级流程正在后台执行，可能短暂中断 DNS 或代理。请勿重复点击或关闭路由器。')));
		else if(self.updateComplete)content.push(el('p',{'class':'az-update-success',role:'status'},_('安装器报告升级完成。请重新载入页面并验证实际安装版本。')));
		else if(self.updateError||d.ok===false)content.push(el('p',{'class':'az-inline-warning',role:'alert'},self.updateError||d.message||_('无法检查更新，请确认路由器能够访问 GitHub。')));
		else if(d.ok){
			content.push(el('p',{'class':'az-update-detail'},[
				_('当前版本：'),el('strong',{translate:'no'},d.current||'v0.23.6'),
				_('　官方最新：'),el('strong',{translate:'no'},d.latest||'—')]));
			content.push(el('p',{role:'status'},!d.available?_('已是当前发布通道的最新版本。'):!d.compatible?d.message||_('有新版本，但没有与当前固件兼容的安装包，不能强制升级。'):_('发现适用于当前固件的新版本，已通过安装包与 SHA-256 预检。')));
		}else content.push(el('p',{},_('点击检查更新，查找官方最新版本。')));
		if(d.ok===false&&/^[0-9]{3}$/.test(d.api_status||'')&&/^[0-9]{3}$/.test(d.entry_status||'')){
			var statusText=function(code){return code==='000'?_('未收到 HTTP 应答'):'HTTP '+code;};
			content.push(el('p',{id:'az-update-diagnostic','class':'az-live-caption'},_('GitHub API：')+statusText(d.api_status)+_(' · 官方安装入口：')+statusText(d.entry_status)));
		}
		if(d.ok&&d.metadata_source==='official_entry')content.push(el('p',{id:'az-update-source','class':'az-live-caption'},_('版本信息来自官方安装入口；下载仍执行 SHA-256 和本机依赖预检。')));
		if(hasUpdate&&!upgrading&&!self.updateComplete){
			if(self.updateConfirm){
				content.push(el('p',{'class':'az-inline-warning'},_('升级将备份现有配置并尝试恢复旧版接管的 DNS/代理设置，可能短暂中断网络。请确认设备可通过局域网恢复。')));
				content.push(el('div',{'class':'az-update-actions'},[
					el('button',{type:'button',id:'az-upgrade-confirm','class':'az-button az-button-primary',disabled:blocked?true:null,
						click:function(){return self.runUpgrade();}},_('确认升级至 ')+(d.latest||'')),
					el('button',{type:'button',id:'az-upgrade-cancel','class':'az-button az-button-quiet',
						click:function(){self.updateConfirm=false;self.paint('az-version');}},_('取消'))]));
			}else content.push(el('button',{type:'button',id:'az-upgrade-ready','class':'az-button az-button-primary',disabled:blocked?true:null,
				click:function(){self.updateConfirm=true;self.paint('az-upgrade-confirm');}},_('立即更新到 ')+(d.latest||'')));
		}
		if(self.updateComplete)content.push(el('button',{type:'button',id:'az-update-reload','class':'az-button az-button-primary',
			click:function(){if(typeof window!=='undefined'&&window.location)window.location.reload();}},_('刷新页面确认版本')));
		if(!upgrading)content.push(el('button',{type:'button',id:'az-update-check','class':'az-button az-button-quiet',
			disabled:checking||self.busy?true:null,click:function(){return self.checkUpdate(false);}},checking?_('检查中…'):_('重新检查更新')));
		content.push(el('p',{'class':'az-update-footnote'},_('仅更新 AdZero 软件包，不自动升级整个 OpenWrt 固件；已发布版本不代表所有固件均已适配。')));
		return el('section',{id:'az-update-panel','class':'az-card az-update-panel',role:'region','aria-labelledby':'az-update-title'},content);
	},
	toggleUpdate:function(){
		this.updateOpen=!this.updateOpen;this.updateConfirm=false;
		this.paint('az-version');
		if(this.updateOpen&&!this.updateData&&!this.updateBusy)return this.checkUpdate(false);
		return Promise.resolve();
	},
	checkUpdate:function(silent){
		var self=this;
		if(self.updateBusy||self.busy)return Promise.resolve();
		self.updateBusy='check';self.updateChecked=true;self.updateError=null;self.updateConfirm=false;self.paint();
		var saved=sessionValue('update-check');
		if(saved&&!/^[a-f0-9]{8}-[a-f0-9]{4}-[a-f0-9]{4}-[a-f0-9]{4}-[a-f0-9]{12}$/.test(saved.job||'')){saved=null;sessionValue('update-check',null);}
		return (saved?Promise.resolve({ok:true,job:saved.job}):updateCalls.check()).then(function(d){
			if(!d||!d.job)return d; // A still-loaded older RPC can reply directly.
			if(!d.ok||!/^[a-f0-9]{8}-[a-f0-9]{4}-[a-f0-9]{4}-[a-f0-9]{4}-[a-f0-9]{12}$/.test(d.job))throw Error(_('更新检查未返回有效任务回执。'));
			sessionValue('update-check',{job:d.job});
			return waitJob(d).then(function(result){sessionValue('update-check',null);return result.update;},function(error){if(error.reason)sessionValue('update-check',null);throw error;});
		}).then(function(d){
			if(!d||typeof d.ok!=='boolean')throw Error(_('更新服务未返回有效结果。'));
			if(d.latest&&!/^v[0-9]{1,3}\.[0-9]{1,3}\.[0-9]{1,3}$/.test(d.latest))throw Error(_('官方版本格式无效。'));
			self.updateData=d;
			if(!d.ok&&silent!==true)self.updateError=d.message||_('检查失败，请稍后重试。');
		}).catch(function(e){
			self.updateData=null;
			self.updateError=String(e.message||e);
		}).then(function(){self.updateBusy=null;self.paint('az-version');});
	},
	runUpgrade:function(receipt){
		var self=this,d=self.updateData||{},s=self.currentStatus||{};
		if(self.updateBusy||self.busy)return Promise.resolve();
		if(!receipt&&(!self.updateConfirm||!d.ok||!d.available||!d.compatible||!/^v[0-9]{1,3}\.[0-9]{1,3}\.[0-9]{1,3}$/.test(d.latest||'')||
			operationActive(s)||s.stale||self.draftCount()||self.controlCount()||self.enhanceDirty()||self.unstagedEdits())){
			self.updateError=_('存在尚未应用的更改、其他后台任务或无效的版本校验。请先完成当前操作再更新。');
			self.paint();return Promise.resolve();
		}
		self.updateOpen=true;self.updateConfirm=false;self.busy='upgrade';self.updateBusy='upgrade';self.updateError=null;
		self.updateComplete=false;self.jobProgress=null;self.jobUncertain=false;self.paint();
		return (receipt?Promise.resolve(receipt):updateCalls.start(d.latest)).then(function(reply){
			return trackJob(reply,'upgrade','self-upgrade',self);
		}).then(function(){
			self.updateComplete=true;self.updateError=null;
		}).catch(function(e){
			self.updateError=_('升级没有确认完成：')+String(e.message||e)+_('。请保留原有恢复记录，核对后再操作。');
		}).then(function(){
			finishJob(self);self.updateBusy=null;self.paint('az-version');
		});
	},
	renderPanel: function(s) {
		var self = this; self.currentStatus = s;
		var f = flags(s), inv = s.compatibility || {}, dns = inv.dns || {},selection=connectionSelection(s);
		var confirming=confirmingAction(self)||s.state==='closing';
		var operating=operationActive(s);
		var adapter = s.adapter || {}, backend = s.backend || {}, busy = self.busy, locked = busy || s.stale || confirming || operating, loading = s.state === 'loading';
		var active = proxyName(adapter.plugin,s) || (adapter.plugin === 'none' ? _('路由器 DNS') : _('接入待确认'));
		var synced = f.enabled && adapter.stage === 'configured_unverified' && adapter.plugin && adapter.plugin !== 'none';
		var ready = f.enabled && backend.state === 'ready', verified = self.observation&&self.observation.available&&self.observation.active&&(self.observation.verification||{}).state==='passed';
		var checked = self.lastChecked ? new Intl.DateTimeFormat(undefined, { hour: '2-digit', minute: '2-digit', second: '2-digit' }).format(self.lastChecked) : '—';
		var pause = (s.policy || {}).pause || {}, allowed = (s.policy || {}).allowed || [];
		var routing = adapter.routing || {}, routingWanted = (s.policy || {}).routing_enabled === true;
		var strictDNS=!!(adapter.dns_policy||{}).strict,guardState=(adapter.dns_policy||{}).guard;
		var titles = { configured_unverified: _('广告过滤已开启'), disabled: _('广告过滤未开启'), paused: _('保护已临时暂停'),
			inconsistent: _('配置需要检查'), closing: _('正在关闭并恢复'), external_owner: _('当前由其他服务管理'),
			unavailable: _('过滤组件尚未就绪'), error: _('暂时无法读取状态'), loading: _('正在读取运行状态…') };
		var descriptions = { configured_unverified: _('规则已载入，当前接入配置已完成。'), disabled: _('开启后自动匹配当前网络，关闭时恢复原有设置。'),
			paused: pause.error ? _('自动恢复未完成，当前仍暂停。请尝试立即恢复，或关闭并恢复原设置。') : pause.remaining > 0 ? _('约 ') + Math.ceil(pause.remaining / 60) + _(' 分钟后自动恢复；重启路由器也会尝试恢复。') : _('暂停已到期，正在等待后台恢复保护。'),
			inconsistent: _('部分配置尚未确认，请关闭并恢复后再开启。'), closing: _('关闭请求已保存，后台将继续安全恢复。完成前不能重新开启或应用规则；不必反复点击。'), external_owner: _('请先在原服务中关闭过滤后端，再启用 AdZero。'),
			unavailable: _('请检查插件依赖是否完整，然后重新检测。'), error: _('状态请求未完成，请查看错误详情后重新检测。'), loading: _('页面已就绪，正在读取后端与代理状态。') };
		if(f.enabled&&selection.pending){titles.configured_unverified=_('代理接入待同步');descriptions.configured_unverified=selection.detail;}
		if(adapter.reload_pending){titles.inconsistent=_('正在确认代理重载');descriptions.inconsistent=_('本批规则已保留，后台正在核对代理是否完成。请勿重复应用或重载。');}
		if(strictDNS&&guardState!=='active'){titles.inconsistent=guardState==='error'?_('严格 DNS 保护待检查'):_('严格 DNS 正在等待接入');descriptions.inconsistent=guardState==='error'?_('防火墙保护未能确认，请检查日志；可关闭并恢复。'):_('DNS 查询保持阻断，等待代理接入确认。可重新检测，或关闭并恢复。');}
		if(confirming&&self.notice){titles.inconsistent=self.notice.action==='disable'?_('正在确认关闭'):_('正在确认配置');descriptions.inconsistent=_('操作已执行，正在核对最终状态，请勿重复提交。');}
		if(operating){var operationTitles={reconcile:_('正在同步代理接入'),disable:_('正在关闭并恢复'),'enhance-save':_('正在保存增强设置'),'dns-switch':_('正在切换备用 DNS'),temporary:_('正在应用临时放行'),'temporary-expire':_('正在恢复到期规则')};titles[s.state]=operationTitles[s.operation.action]||_('正在执行其他操作');descriptions[s.state]=_('后台操作尚未结束，完成后页面会自动更新。请稍候，无需再次关闭或应用。');}
		var blocker = proxyBlocker(s)||(s.state==='disabled'&&s.dns_mode_safe!==true?_('当前 DNS 模式不支持自动启用，请检查过滤后端设置。'):null);
		var toggle = queueDisable(s)&&f.canDisable&&!confirming ? el('button',{id:'az-toggle',type:'button','class':'az-button az-button-danger',disabled:busy?true:null,click:function(){return self.runAction('disable');}},_('排队关闭并恢复')) : operating || confirming ? el('button',{id:'az-toggle',type:'button','class':'az-button az-button-quiet',disabled:true,'aria-busy':'true'},operating?_('操作进行中…'):_('正在确认…')) : f.canDisable && !f.enabled && !f.paused ? el('button', { id: 'az-toggle', 'class': 'az-button az-button-danger', type: 'button',
			'data-action': 'disable', disabled: locked ? true : null, click: function() { return self.runAction('disable'); } }, _('关闭并恢复')) : el('button', {
			id: 'az-toggle', 'class': 'az-toggle' + (f.enabled ? ' is-on' : ''), type: 'button', role: 'switch',
			'aria-label': _('广告过滤'), 'aria-checked': f.enabled ? 'true' : 'false', 'aria-describedby': 'az-operation-note',
			'data-action': f.paused ? 'resume' : f.enabled ? 'disable' : 'enable', disabled: locked || (!f.canEnable && !f.canDisable) ? true : null,
			click: function() { return f.paused ? self.runPolicy('resume', []) : self.runAction(f.enabled ? 'disable' : 'enable'); }
		}, el('span', { 'class': 'az-toggle-track', 'aria-hidden': 'true' }, el('span', { 'class': 'az-toggle-thumb' })));
		var page=self.pageKey(),pages=[['overview',_('运行概览'),'shield',_('查看保护状态、实时统计与当前接入。')],['rules',_('过滤规则'),'book',_('按需选择订阅，改完统一应用。')],['controls',_('访问控制'),'check',_('查找误拦截，精确管理域名放行与拦截。')],['enhance',_('DNS 增强'),'settings',_('按需增强解析与过滤，保留原生网络路径。')]],currentPage=pages.filter(function(p){return p[0]===page;})[0];
		var nodes = [el('a',{'class':'az-skip-link',href:'#az-page-title'},_('跳到页面内容')),el('div', { 'class': 'az-header' }, [
			el('div', { 'class': 'az-brand' }, [ el('img', { 'class': 'az-brand-mark', src: L.resource('view/adzero/logo.png') + '?v=0.23.6', alt: '', width: 44, height: 44, draggable: 'false' }),
				el('div', {}, [ el('h2', { translate: 'no' }, 'AdZero'), el('p', {}, _('广告过滤与 DNS')) ]),
				el('button',{id:'az-version',type:'button','class':'az-version-button'+(self.updateData&&self.updateData.available&&self.updateData.compatible?' has-update':''),
					'aria-expanded':self.updateOpen?'true':'false','aria-controls':'az-update-panel',
					'aria-label':_('AdZero v0.23.6，检查在线更新'),disabled:self.updateBusy||self.busy?true:null,
					click:function(){return self.toggleUpdate();}},[
					el('span',{translate:'no'},'v0.23.6'),
					self.updateData&&self.updateData.available&&self.updateData.compatible?el('span',{'class':'az-update-dot','aria-label':_('有新版本')},''):null,
					icon('refresh')]) ]),
			el('nav', { 'class': 'az-help-links', 'aria-label': _('帮助与社区') }, helpLinks.map(function(link) {
				return el('a', { href: link[0], 'class': 'az-button az-help-link az-help-' + link[2], target: '_blank', rel: 'noopener noreferrer', 'aria-label': link[3] }, [icon(link[2]), link[1]]);
			})),
			el('div', { 'class': 'az-header-actions' }, [ el('span', { 'class': 'az-last-checked' }, s.stale ? (self.busy==='status' ? _('上次概览 · 后台核对') : _('上次概览 · 待重新检测')) : self.lastChecked ? _('刚刚检测于 ') + checked : _('正在连接路由器')),
				el('button', { id: 'az-status', type: 'button', 'class': 'az-button az-button-quiet', 'data-action': 'status', disabled: busy ? true : null,
					click: function() { return self.refreshStatus(); } }, [ icon('refresh'), busy === 'status' ? _('检测中…') : _('重新检测') ]) ]) ]) ];
		nodes.push(el('nav', { 'class':'az-tabs', 'aria-label':_('AdZero 页面') },pages.map(function(p){return el('a',{id:'az-tab-'+p[0],href:'#adzero/'+p[0],'class':page===p[0]?'is-selected':'','aria-current':page===p[0]?'page':null,click:function(e){if(e&&(e.ctrlKey||e.metaKey||e.shiftKey||e.altKey||e.button>0))return;if(e&&e.preventDefault)e.preventDefault();return self.selectPage(p[0]);}},[icon(p[2]),el('span',{},p[1])]);})));
		if(self.updateOpen)nodes.push(self.renderUpdatePanel());
		nodes.push(el('div',{'class':'az-page-heading'},[el('div',{},[el('h2',{id:'az-page-title',tabindex:'-1'},currentPage[1]),el('p',{},currentPage[3])])]));
		if(self.draftCount())nodes.push(self.renderDraft(s));
		if(self.controlCount())nodes.push(self.renderControlDraft());
		if (s.stale) nodes.push(el('p',{'class':'az-snapshot-hint',role:'status'},_('上次读取的概览，正在后台核对；取得新状态前暂停操作。')));
		if (self.notice) nodes.push(el('div', { 'class': 'az-notice az-notice-' + self.notice.kind, role: self.notice.kind === 'error' ? 'alert' : 'status' }, [
			icon(self.notice.kind === 'success' ? 'check' : 'info'), el('div', {}, [ el('strong', {}, self.notice.text),
				self.notice.detail ? el('details', { 'class': 'az-error-details' }, [ el('summary', {}, self.notice.kind==='error'?_('查看错误信息'):_('查看说明')), el('pre', {}, self.notice.detail) ]) : null ]) ]));
		if (s.state === 'error' && s.error) nodes.push(el('details', { 'class': 'az-error-details az-load-error' }, [ el('summary', {}, _('查看状态读取错误')), el('pre', {}, s.error) ]));
		if (busy && !loading && !(s.stale && busy === 'status')) nodes.push(el('div', { 'class': 'az-working', role: 'status', 'aria-live': 'polite' }, [ el('span', { 'class': 'az-spinner', 'aria-hidden': 'true' }),
			self.jobProgress && phaseText[self.jobProgress.stage] ? (busy==='disable'&&self.jobProgress.stage==='waiting_lock'?_('关闭已排队，正在等待后台操作安全结束…'):phaseText[self.jobProgress.stage]) + (self.jobProgress.elapsed>=10 ? _(' · 总计 ') + self.jobProgress.elapsed + _(' 秒') + (typeof self.jobProgress.phase_elapsed==='number' ? _(' · 本阶段 ') + self.jobProgress.phase_elapsed + _(' 秒') : '') : '') : busy === 'routing_on' ? _('正在开启自动分流…') : busy === 'routing_off' ? _('正在关闭自动分流…') : busy === 'rules' ? _('正在下载、校验并应用规则；离开页面后任务仍会继续。') : busy === 'enable' ? _('正在开启过滤…') : busy === 'disable' ? _('正在关闭并恢复…') : busy === 'refresh' ? _('正在更新并同步规则…') : busy === 'lookup' ? _('正在查询当前规则…') : busy === 'allow' || busy === 'revoke' ? _('正在应用放行名单并同步代理…') : busy === 'pause' ? _('正在暂停保护…') : busy === 'resume' ? _('正在恢复保护…') : _('正在检测状态…') ]));
		if (self.rulesOpen) { nodes.push(self.renderRules(s)); return el('div',{'class':'az-app'},nodes); }
		if (self.controlsOpen) { nodes.push(self.renderControls(s));return el('div',{'class':'az-app'},nodes); }
		if (self.enhanceOpen) {nodes.push(self.renderEnhance());return el('div',{'class':'az-app'},nodes);}
		nodes.push(el('section', { 'class': 'az-card az-hero' + (f.enabled && !operating && !selection.pending ? ' is-on' : '') + (loading ? ' is-loading' : ''), 'aria-busy': loading || confirming || operating ? 'true' : 'false' }, [
			el('div', { 'class': 'az-hero-main' }, [ el('div', { 'class': 'az-hero-heading' }, [ icon('shield'),
				el('h3', { 'class': 'az-hero-title' }, titles[s.state] || _('状态暂未确认')),
				pill(s.stale ? _('上次状态') : loading ? _('检测中') : operating ? _('同步中') : confirming ? _('确认中') : selection.pending ? _('待同步') : f.enabled ? _('已开启') : f.paused ? _('已暂停') : s.state === 'disabled' ? _('已关闭') : _('需要留意'), selection.pending ? 'attention' : operating ? 'neutral' : f.enabled ? 'positive' : s.state === 'disabled' || loading || confirming ? 'neutral' : 'attention') ]),
				el('p', { 'class': 'az-hero-copy' }, descriptions[s.state] || _('请重新检测当前状态。')), blocker ? el('p', { 'class': 'az-inline-warning' }, [ icon('info'), blocker ]) : null ]),
			el('div', { 'class': 'az-switch-control' }, [ el('span', {}, loading ? _('等待状态确认') : _('保护开关')), toggle ]) ]));
		if (f.enabled || f.paused) nodes.push(el('div', { 'class': 'az-pause-bar' }, f.paused ? [
			el('span', {}, _('暂时关闭保护，保留个人放行与分流选择。')),
			el('div', { 'class': 'az-action-row' }, [ el('button', { id: 'az-resume', type: 'button', 'class': 'az-button az-button-primary', disabled: locked ? true : null, click: function() { return self.runPolicy('resume', []); } }, _('立即恢复')),
				el('button', { type: 'button', 'class': 'az-button az-button-quiet', disabled: locked ? true : null, click: function() { return self.runAction('disable'); } }, _('保持关闭')) ])
		] : [ el('span', {}, strictDNS?_('严格 DNS 已开启 · 临时暂停不可用；误拦截请到“访问控制”处理。'):_('网页或 App 异常？先暂停对比，或前往“访问控制”检查域名。')),
			el('div', { 'class': 'az-action-row' }, [ el('label', { 'for': 'az-pause-duration', 'class': 'az-pause-label' }, _('暂停时长')),
				el('select', { id: 'az-pause-duration', disabled: locked||strictDNS ? true : null, change: function(e) {
					self.pauseSeconds = Number(e.target.value);
					var button=document.getElementById('az-pause');
					if(button)dom.content(button,[self.pauseButtonText()]);
				} }, [300,900,1800].map(function(seconds) { return el('option', { value: seconds, selected: self.pauseSeconds === seconds ? true : null }, seconds / 60 + _(' 分钟')); })),
				el('button', { id: 'az-pause', type: 'button', 'class': 'az-button az-button-quiet', disabled: locked||strictDNS ? true : null, click: function() { return self.runPolicy('pause', [self.pauseSeconds]); } }, self.pauseButtonText()) ]) ]));
		nodes.push(el('div',{id:'az-live-metrics'},[self.renderMetrics(s)]));
		nodes.push(el('div',{id:'az-stats-actions'},[self.renderStatsActions()]));
		nodes.push(el('p',{id:'az-stats-notice','class':'az-live-caption',role:'status'},self.statsNotice||''));
		nodes.push(el('div',{id:'az-live-health'},[self.renderObservationHealth()]));
		nodes.push(el('div',{id:'az-live-latency'},[self.renderLatency()]));
		var workbench=[el('div',{id:'az-live-verification'},[self.renderVerification()]),self.renderConnection(s)];
		nodes.push(el('div',{'class':'az-workbench'},workbench));
		var network=[],routingUnavailable=adapter.plugin==='ssrplus'||((((s.compatibility||{}).dns||{}).active_proxies||[]).join(',')==='ssrplus');
		network.push(el('section', {'class':'az-card az-domain-card'},[
			el('div',{'class':'az-section-heading'},[el('h3',{},_('自动分流')),
				el('button',{id:'az-routing',type:'button','class':'az-toggle'+((self.draft?self.draft.routing_enabled:routingWanted)?' is-on':''),role:'switch','aria-checked':(self.draft?self.draft.routing_enabled:routingWanted)?'true':'false','aria-label':_('自动分流'),disabled:locked||!(f.enabled||s.state==='disabled'&&!s.managed)||routingUnavailable&&!(self.draft?self.draft.routing_enabled:routingWanted)?true:null,
					click:function(){return self.stageRouting(!(self.draft?self.draft.routing_enabled:routingWanted));}},el('span',{'class':'az-toggle-track','aria-hidden':'true'},el('span',{'class':'az-toggle-thumb'}))) ]),
			el('p',{'class':'az-card-copy'},self.draft&&self.draft.routing_enabled!==routingWanted?_('待应用：')+(self.draft.routing_enabled?_('开启自动分流'):_('关闭自动分流'))+_('。当前网络继续使用已应用配置。'):routingWanted && f.enabled ? routing.mode==='proxy'?_('国内直连 / 国外代理 · 配置已应用') : routing.mode==='native'?_('沿用 PassWall2 原生 DNS 与分流'):routing.mode==='direct'?_('国内 DNS / 全部直连 · 配置已应用'):_('分流接入待确认') : f.paused?_('恢复保护后继续应用已保存的选择。'):!f.enabled?_('可先选择分流，应用后在开启保护时生效。'):_('自动识别当前代理，同时配置 DNS 和连接去向。')),
			el('p',{'class':'az-operation-note'},routingUnavailable?_('SSR Plus+ 当前支持广告过滤，请关闭 AdZero 自动分流；连接去向与 DNS 上游由 SSR Plus+ 原生设置管理。'):_('有代理时国内直连、国外走当前代理；无代理时使用国内 DNS 与本地直连。切换可能短暂重载网络，实际出口需在设备上验证。')) ]));
		network.push(self.renderDNS(s));
		nodes.push(el('details',{'class':'az-card az-network-settings',open:self.networkOpen?true:null,toggle:function(e){self.networkOpen=e.target.open;}},[
			el('summary',{},[el('div',{},[el('strong',{},_('DNS 与分流')),el('p',{},routingWanted?_('自动分流已开启'):_('使用当前网络设置'))]),el('span',{'class':'az-details-hint'},_('展开设置'))]),el('div',{'class':'az-network-body'+(self.dnsOpen?' is-editing':'')},network)
		]));
		if (backend.errors > 0) nodes.push(el('p', { 'class': 'az-inline-warning', role: 'alert' }, _('过滤后端报告错误，请检查服务日志后重新检测。')));
		if (!s.compatibility && !loading && s.state !== 'error') nodes.push(el('p', { 'class': 'az-inline-warning', role: 'alert' }, _('网络环境检测未完成，请检查插件安装文件后重新检测。')));
		nodes.push(el('footer', { 'class': 'az-footer' }, [ icon('info'), _('DNS 过滤无法覆盖同域视频广告、网页占位或绕过路由器的请求。') ]));
		return el('div', { 'class': 'az-app' }, nodes);
	},
	openDNS:function(){var self=this,editing=self.dnsEditing();self.networkOpen=true;self.dnsOpen=true;self.selectPage('overview');return Promise.all([self.loadSources(),self.loadEnhance(true)]).then(function(){if(!editing&&!self.dnsEditing())self.dnsForm=null;self.paint('az-dns-domestic');});},
	renderDNS: function(s) {
		var self=this,data=self.sourcesData,profile=self.draft&&self.draft.dns||data&&data.dns||defaultDNS();
		var nativeDNS=(s.adapter||{}).plugin==='passwall2',editable=!!data&&self.canStage()&&!self.sourcesLoading&&!data.pending&&!nativeDNS;
		if(!self.dnsForm){self.dnsForm={strict:!!profile.strict,failover:profile.failover!=null?profile.failover:!!(data||{}).dns_failover,proxy_port:profile.proxy_port!=null?profile.proxy_port:data&&data.dns_test_proxy_port||0};['domestic','foreign'].forEach(function(g){var p=profile[g];self.dnsForm[g]=[p.active].concat(p.servers.filter(function(v){return v!==p.active;})).join('\n');self.dnsForm[g+'_mode']=p.mode||'single';});self.dnsFormSaved=JSON.stringify(self.dnsForm);self.dnsFormBase=JSON.parse(JSON.stringify(data&&data.dns||defaultDNS()));}
		return el('section',{'class':'az-card az-domain-card'},[
			nativeDNS?el('p',{'class':'az-inline-warning'},_('当前 DNS 与分流由 PassWall2 原生管理。AdZero 仅接入广告过滤；这里保存的 DNS 组不改写 PassWall2，请到原插件调整上游。')):null,
			el('div',{'class':'az-section-heading'},[el('h3',{},_('DNS 设置')),el('button',{id:'az-dns-open',type:'button','class':'az-button az-button-quiet az-button-compact','aria-expanded':self.dnsOpen?'true':'false','aria-controls':'az-dns-editor',click:function(){self.dnsOpen=!self.dnsOpen;if(self.dnsOpen){if(!self.dnsEditing())self.dnsForm=null;return Promise.all([self.sourcesData?Promise.resolve():self.loadSources(),self.loadEnhance(true)]).then(function(){self.paint('az-dns-open');});}self.paint('az-dns-open');}},self.dnsOpen?_('收起'):_('编辑 DNS'))]),
			el('p',{'class':'az-card-copy'},!data?_('打开编辑后读取已保存的 DNS 设置。'):(self.draft&&self.draft.dns&&!sameDNS(self.draft.dns,self.draft.dns_before)?_('待应用 · '):_('已保存 · '))+_('国内：')+profile.domestic.active+(profile.domestic.mode==='parallel'?_(' 等 · 并发'):'')+_(' · 国外：')+profile.foreign.active+(profile.foreign.mode==='parallel'?_(' 等 · 并发'):'')+(profile.strict?_(' · 严格模式'):'')),
			self.sourcesError&&self.dnsOpen?el('div',{'class':'az-inline-warning',role:'alert'},[_('DNS 设置读取失败，编辑内容已保留。'),el('button',{type:'button','class':'az-button az-button-quiet az-button-compact',disabled:self.busy||self.sourcesLoading?true:null,click:function(){return self.loadSources();}},_('重新读取'))]):null,
			self.dnsOpen?el('div',{id:'az-dns-editor'},[
				el('p',{'class':'az-card-copy'},_('每行一个 IP。单一模式使用第一条，其余备用；并发模式同时查询同组最多 3 个地址，采用首个有效应答。')),
				el('div',{'class':'az-dns-grid'},['domestic','foreign'].map(function(g){return el('div',{'class':'az-dns-field'},[el('label',{'for':'az-dns-'+g},g==='domestic'?_('国内 DNS · 直连'):_('国外 DNS · 当前代理')),el('select',{id:'az-dns-'+g+'-mode','aria-label':g==='domestic'?_('国内查询策略'):_('国外查询策略'),value:self.dnsForm[g+'_mode'],disabled:!editable?true:null,change:function(e){self.dnsForm[g+'_mode']=e.target.value;self.updateDNSTestActions();}},[el('option',{value:'single',selected:self.dnsForm[g+'_mode']==='single'?true:null},_('单一 + 备用')),el('option',{value:'parallel',selected:self.dnsForm[g+'_mode']==='parallel'?true:null},_('组内并发'))]),el('textarea',{id:'az-dns-'+g,rows:3,spellcheck:'false',value:self.dnsForm[g],disabled:!editable?true:null,input:function(e){self.dnsForm[g]=e.target.value;self.updateDNSTestActions();}},self.dnsForm[g])]);})),
				el('label',{'class':'az-setting-row'},[el('span',{'class':'az-setting-copy'},[el('strong',{},_('严格防 DNS 泄露')),el('small',{},_('接入未确认时阻断 DNS，禁止国外组回落直连'))]),el('span',{'class':'az-setting-switch'},[el('input',{id:'az-dns-strict',type:'checkbox',role:'switch',checked:self.dnsForm.strict?true:null,disabled:!editable?true:null,change:function(e){self.dnsForm.strict=e.target.checked;self.updateDNSTestActions();}}),el('span',{'class':'az-toggle-track','aria-hidden':'true'},el('span',{'class':'az-toggle-thumb'}))])]),
				el('label',{'class':'az-enhance-field az-port-field'},[el('span',{},_('本机无认证 SOCKS5 端口')),el('input',{id:'az-dns-proxy-port',type:'number',min:1024,max:65535,value:self.dnsForm.proxy_port||'',placeholder:_('例如 1070'),disabled:!editable?true:null,input:function(e){self.dnsForm.proxy_port=e.target.value?Number(e.target.value):0;self.updateDNSTestActions();}}),el('small',{},_('国外测试使用；NaiLink 国外并发或严格模式、PassWall 国外并发或严格 TCP 模式必填。'))]),
				el('div',{id:'az-dns-validation','class':'az-dns-validation',role:'status','aria-live':'polite'},self.dnsPolicyError(self.dnsForm)||''),
				self.disclosure('dns-port-help',_('端口在哪里查看？'),[el('p',{},_('OpenClash：插件设置 → 流量控制中的 SOCKS5／混合端口；并发和严格模式本身不需要填写，仅国外测试使用。PassWall：基本设置或 SOCKS 配置中的本机 SOCKS 监听；以实际启用且无认证的端口为准。')),
					el('p',{},_('NaiLink：远端节点端口不是本机监听。没有可用本机 SOCKS 服务时，两组先使用“单一＋备用”，严格模式关闭。国内并发还需普通 TCP 53 上游可达；不会自动改成 UDP，以免回环。网页、DNS 和透明代理端口不能代替 SOCKS5。')),
					el('a',{href:'https://slobys.github.io/AdZero/dns.html#recipes',target:'_blank',rel:'noopener noreferrer'},_('查看分插件推荐设置'))]),
				el('label',{'class':'az-setting-row'},[el('span',{'class':'az-setting-copy'},[el('strong',{},_('自动使用备用 DNS')),el('small',{},_('仅单一模式：连续 3 轮失败后切换同组备用；并发组不轮换'))]),el('span',{'class':'az-setting-switch'},[el('input',{id:'az-dns-failover',type:'checkbox',role:'switch',checked:self.dnsForm.failover?true:null,disabled:!editable?true:null,change:function(e){self.dnsForm.failover=e.target.checked;self.updateDNSTestActions();}}),el('span',{'class':'az-toggle-track','aria-hidden':'true'},el('span',{'class':'az-toggle-thumb'}))])]),
				self.disclosure('dns-compatibility',_('兼容性与保护范围'),[el('p',{},_('OpenClash／Nikki 使用内核原生并发。NaiLink／PassWall 的本机并发会先检查实际 TCP 上游，接入后再检查应答；UDP 测试通过不能替代。PassWall 国外并发还要求原生 TCP DNS 模式和可用本机 SOCKS5，不替换 Xray／Sing-box 引擎。无代理时仅使用国内组。')),
					el('p',{},_('严格模式要求自动分流及唯一活动的受支持代理。接管默认 IPv4 LAN 的 53 端口查询，阻断该 LAN 的 IPv6 DNS 和 853 端口；保留国内组作为节点引导解析例外。加密 DNS 经当前代理转发时由代理提供传输保护；普通 TCP 本身不加密。')),
					el('p',{},_('浏览器／应用内置 DoH 使用 HTTPS，需在设备侧禁用或统一管理；其他 VLAN、VPN 和代理自身额外 DNS 不在承诺范围。严格模式期间增强 CNAME／设备入口及临时暂停不可用，关闭并恢复仍可使用。'))]),
				el('div',{'class':'az-action-row'},[el('button',{id:'az-dns-stage',type:'button','class':'az-button az-button-primary',disabled:!editable||!!self.dnsPolicyError(self.dnsForm)?true:null,click:function(){return self.stageDNS();}},_('保存到待应用')),el('button',{id:'az-dns-default',type:'button','class':'az-button az-button-quiet',disabled:!editable?true:null,click:function(){return self.stageDNS(defaultDNS());}},_('恢复默认到待应用'))]),
				self.disclosure('dns-tests',_('测试已应用的 DNS'),[el('div',{'class':'az-section-heading'},[el('p',{id:'az-enhance-test-hint','class':'az-section-intro'},self.dnsTestHint()),el('button',{id:'az-dns-test',type:'button','class':'az-button az-button-compact',disabled:!editable||self.dnsTestBlocked()?true:null,click:function(){return self.runEnhance('test');}},[icon('refresh'),_('测试 DNS')])]),
					el('div',{id:'az-enhance-dns-results','aria-live':'polite'},[self.renderDNSTests()]),el('p',{'class':'az-field-hint'},_('国内按已接入模式测试：本机并发使用 TCP，其余使用 UDP；国外经本机 SOCKS5 TCP，未填写时跳过。这是上游可达性测试，不验证污染、原生分流或网站登录。自动备用每分钟检查，切换后至少冷却 5 分钟。'))]),
				el('p',{'class':'az-operation-note'},_('与其他修改统一应用。严格模式会同时开启自动分流；不支持的组合会拒绝应用并保留恢复记录。'))]) : null]);
	},
	stageDNS: function(value){
		var self=this;if(!self.canStage()||!self.sourcesData)return Promise.resolve();
		try{
			if(!self.draft&&!sameDNS(self.dnsFormBase,self.sourcesData.dns))throw new Error(_('DNS 设置已变化，请重新打开编辑。'));
			var profile=value||{};
			if(!value){['domestic','foreign'].forEach(function(g){var servers=self.dnsForm[g].split(/\r?\n/).map(function(v){return v.trim().toLowerCase();}).filter(Boolean),parallel=self.dnsForm[g+'_mode']==='parallel';if(!servers.length||servers.length>(parallel?3:8)||servers.some(function(v){return !/^[0-9a-f:.]+$/.test(v);})||new Set(servers).size!==servers.length)throw new Error(_('每组需要不同的 DNS IP；单一模式最多 8 个，并发最多 3 个。'));profile[g]={servers:servers,active:servers[0]};if(parallel)profile[g].mode='parallel';});if(self.dnsForm.strict)profile.strict=true;if(self.dnsForm.failover!==!!(self.sourcesData||{}).dns_failover||(self.dnsFormBase||{}).failover!=null)profile.failover=!!self.dnsForm.failover;profile.proxy_port=self.dnsForm.proxy_port||0;if(self.dnsForm.proxy_port){if(!Number.isInteger(self.dnsForm.proxy_port)||self.dnsForm.proxy_port<1024||self.dnsForm.proxy_port>65535)throw new Error(_('SOCKS5 端口需要 1024–65535。'));profile.proxy_port=self.dnsForm.proxy_port;}}
			var policyError=self.dnsPolicyError(profile);if(policyError)throw new Error(policyError);
			self.ensureDraft();self.draft.dns=profile;if(profile.strict)self.draft.routing_enabled=true;self.dnsForm=null;self.notice=null;if(!self.draftCount())self.draft=null;self.paint();
		}catch(e){self.notice={kind:'error',text:String(e.message||e)};self.paint();}
		return Promise.resolve();
	},
	renderRuleAdvice:function(items,catalog){
		var selected=items.filter(function(item){return item.enabled&&catalog.some(function(rule){return rule.family==='hagezi-multi'&&rule.url===item.url;});});
		if(selected.length<2){
			if(items.filter(function(item){return item.enabled;}).length<6)return null;
			return el('p',{'class':'az-inline-warning'},_('已启用多份列表。重叠域名会自动去重；建议保留主列表并按需补充，避免增加下载耗时、内存占用和误拦截。'));
		}
		return el('p',{'class':'az-inline-warning'},_('已同时选择 ')+selected.map(function(item){return item.name;}).join('、')+_('，同系列覆盖高度重叠。重叠不会阻止应用，建议保留一项再补充其他用途。'));
	},
	renderRules: function(s) {
		var self=this,data=self.sourcesData,items=self.draft?self.draft.items:data ? data.items || [] : [],catalog=data ? data.catalog || [] : [];
		var enabled=flags(s).enabled, editable=!!data && !operationActive(s) && !self.controlCount() && !self.sourcesError && !self.busy && !self.sourcesLoading && !s.stale && !data.pending && (enabled || s.state==='disabled' && !s.managed);
		var form=self.customSource || (self.customSource={name:'',url:'',format:'domains'});
		function updateField(key) { return function(e) { form[key]=e.target.value; }; }
		function date(value) { return value ? new Intl.DateTimeFormat(undefined,{month:'short',day:'numeric',hour:'2-digit',minute:'2-digit'}).format(new Date(value*1000)) : _('尚无记录'); }
		return el('div',{'class':'az-rules-page'},[
			el('section',{'class':'az-card az-source-header'},[
				el('div',{},[el('div',{'class':'az-source-title'},[el('h3',{},_('我的过滤规则')),data?pill(_('已应用 ')+(data.items||[]).filter(function(i){return i.enabled;}).length+' / '+(data.items||[]).length):pill(_('读取中')),self.draftCount()?pill(_('待应用 ')+items.filter(function(i){return i.enabled;}).length+' / '+items.length,'attention'):null]),el('p',{'class':'az-card-copy'},self.draftCount()?_('列表展示待应用选择，当前保护继续使用已应用规则。'):_('选择规则，改完统一应用。'))]),
				el('div',{'class':'az-action-row'},[
					el('button',{id:'az-source-reload',type:'button','class':'az-button az-button-primary',title:self.draftCount()?_('请先应用或撤销待应用更改'):null,disabled:!editable || !!self.draftCount() || !enabled ? true:null,click:function(){return self.reloadCurrent();}},[icon('refresh'),_('重载已应用规则')]),
					el('button',{id:'az-source-refresh',type:'button','class':'az-button az-button-quiet',disabled:!editable || !items.length || !!self.draftCount() ? true:null,click:function(){return self.runSource('source_refresh',[]);}},_('更新订阅')),
					el('button',{id:'az-source-verify',type:'button','class':'az-button az-button-quiet',disabled:!editable||!items.some(function(i){return i.enabled;})||!!self.draftCount()?true:null,click:function(){return self.runSource('source_verify',[]);}},_('手动校验'))]) ]),
			self.renderRuleAdvice(items,catalog),
			self.renderCapacity(data),
			self.sourcesError ? el('div',{'class':'az-notice az-notice-error',role:'alert'},[el('span',{},self.sourcesError),el('button',{type:'button','class':'az-button az-button-quiet',disabled:self.busy?true:null,click:function(){return self.loadSources();}},_('重新读取'))]) : null,
			!data ? el('p',{'class':'az-card-copy',role:'status'},_('正在读取规则列表…')) : null,
			!editable && !self.busy ? el('p',{'class':'az-inline-warning'},s.stale ? _('正在核对状态，请稍候。') : operationActive(s) ? _('后台操作正在进行，完成后可继续调整规则。') : self.sourcesLoading ? _('正在核对规则列表，请稍候。') : (s.adapter||{}).reload_pending ? _('正在确认上一次代理重载，本批规则已保留。请稍候重新检测，勿重复应用。') : s.state==='paused' ? _('请先恢复保护或保持关闭，再调整规则。') : _('当前状态不支持更改规则，请先完成恢复或重新检测。')) : null,
			el('section',{'class':'az-card az-source-list'},items.length ? [
				el('div',{'class':'az-source-table-heading','aria-hidden':'true'},[el('span',{},_('规则名称 / 地址')),el('span',{},_('校验数量 / 最近检查')),el('span',{},_('操作'))]),
				items.map(function(item) { return el('div',{'class':'az-source-row'},[
					el('div',{'class':'az-source-identity'},[el('div',{'class':'az-source-name'},[el('strong',{},item.name),item._new?pill(_('新增 · 待应用'),'attention'):self.sourceChanged(item)?pill(_('已修改 · 待应用'),'attention'):null]),self.disclosure('source-'+sourceKey(item),el('span',{'class':'az-source-url',translate:'no',title:item.url},item.url),[el('p',{'class':'az-source-full-url',translate:'no'},item.url),el('small',{},item.format==='unknown'?_('后端已有格式'):item.format)],'az-source-origin')]),
					el('div',{'class':'az-source-metadata'},[el('strong',{},typeof item.domains==='number'?number(item.domains):item.validation==='backend_only'?_('格式未识别'):_('待校验')),el('span',{},(item.checked||item.updated)?date(item.checked||item.updated):item.enabled?_('更新时自动校验'):_('启用后校验')),
						item.skipped>0?el('small',{},_('已排除不支持的记录：')+number(item.skipped)):item.validation==='backend_only'?el('small',{},_('仅由后端处理；更新订阅可重新校验。')):null]),
					el('div',{'class':'az-source-actions'},[
						el('button',{type:'button','class':'az-toggle'+(item.enabled?' is-on':''),role:'switch','aria-checked':item.enabled?'true':'false','aria-label':_('启用规则 ')+item.name,disabled:!editable || !item.https && !item.enabled?true:null,
							click:function(){return self.stageSource('source_toggle',[sourceKey(item),item.url,!item.enabled]);}},el('span',{'class':'az-toggle-track','aria-hidden':'true'},el('span',{'class':'az-toggle-thumb'}))),
						el('button',{type:'button','class':'az-button az-button-compact','aria-label':_('编辑规则 ')+item.name,disabled:!editable?true:null,click:function(){self.editSource=sourceKey(item);self.editForm={name:item.name,url:item.url,format:item.format==='unknown'?'domains':item.format};self.paint();}},[icon('edit'),_('编辑')]),
						el('button',{type:'button','class':'az-button az-button-danger-soft az-button-compact','aria-label':_('删除规则 ')+item.name,disabled:!editable?true:null,click:function(){self.removeSource=sourceKey(item);self.paint();}},[icon('trash'),_('删除')]) ]),
					self.editSource===sourceKey(item)?self.renderSourceEdit(item,editable):null,
					self.removeSource===sourceKey(item) ? el('div',{'class':'az-source-confirm',role:'group','aria-label':_('确认删除规则')},[el('span',{},_('加入待删除列表？应用后生效，个人放行名单会保留。')),
						el('button',{type:'button','class':'az-button az-button-danger',disabled:!editable?true:null,click:function(){return self.stageSource('source_remove',[sourceKey(item),item.url]);}},_('待删除')),
						el('button',{type:'button','class':'az-button az-button-quiet',click:function(){self.removeSource=null;self.paint();}},_('取消'))]) : null ]); })
			] : !data ? el('div',{'class':'az-source-empty'},_('规则列表尚未就绪。')) : el('div',{'class':'az-source-empty'},[el('strong',{},_('还没有添加订阅')),el('p',{},_('从下方规则库选择，或添加自己的 HTTPS 订阅地址。'))])),
			self.disclosure('source-help',_('更新、校验与数量说明'),[el('p',{},_('默认沿用上次校验结果；文件未变化时不重新完整解析。手动校验只检查本地快照，不下载、不重载。新文件保留基础格式检查。')),el('p',{},_('数量为各列表下载校验后的域名数，可能互相重叠；合并去重及个人放行后，以概览的有效域名数为准。关闭保护时保留选择。'))]),
			self.disclosure('custom-source',el('span',{'class':'az-custom-title'},[el('strong',{},_('添加自定义订阅')),el('small',{},_('使用自己的 HTTPS 规则地址'))]),[
				el('p',{'class':'az-card-copy'},_('支持域名、Hosts 和 ||domain^ 基础 DNS 规则。网页元素、正则、修饰符及高级例外不能导入。')),
				el('div',{'class':'az-custom-grid'},[
					el('label',{},[el('span',{},_('名称')),el('input',{id:'az-source-name',type:'text',maxlength:100,value:form.name,placeholder:_('例如：我的广告列表'),disabled:!editable?true:null,input:updateField('name')})]),
					el('label',{},[el('span',{},_('订阅地址')),el('input',{id:'az-source-url',type:'url',maxlength:2048,value:form.url,placeholder:'https://example.com/domains.txt',disabled:!editable?true:null,input:updateField('url'),'autocomplete':'off'})]),
					el('label',{},[el('span',{},_('文件格式')),el('select',{id:'az-source-format',disabled:!editable?true:null,change:updateField('format')},[['domains',_('域名列表')],['hosts','Hosts'],['adblock',_('基础 DNS Adblock')]].map(function(option){return el('option',{value:option[0],selected:form.format===option[0]?true:null},option[1]);}))]) ]),
				el('div',{'class':'az-custom-submit'},[el('span',{},_('加入待应用列表，统一应用时再校验并同步。')),
					el('button',{id:'az-source-add',type:'button','class':'az-button az-button-primary',disabled:!editable?true:null,click:function(){return self.stageSource('source_add',['',form.name.trim(),form.url.trim(),form.format]);}},_('加入待应用'))]) ],'az-card az-custom-source'),
			el('div',{'class':'az-library-title'},[el('div',{},[el('h3',{},_('规则库')),el('p',{},_('按用途补充，同系列通常选一项即可。'))]),el('div',{'class':'az-library-filters',role:'group','aria-label':_('筛选规则库')},[['all',_('全部')],['available',_('未添加')]].map(function(option){var selected=(self.libraryFilter||'all')===option[0];return el('button',{id:'az-library-'+option[0],type:'button','class':'az-filter'+(selected?' is-active':''),'aria-pressed':selected?'true':'false',click:function(){self.libraryFilter=option[0];self.paint('az-library-'+option[0]);}},option[1]);}))]),
			el('div',{'class':'az-library-grid'},catalog.filter(function(rule){return self.libraryFilter!=='available'||!items.some(function(i){return i.url===rule.url;});}).map(function(rule){var added=items.some(function(i){return i.url===rule.url;});return el('section',{'class':'az-card az-library-item'},[
				el('div',{'class':'az-library-copy'},[el('div',{'class':'az-library-name'},[el('h3',{},rule.name),pill(rule.group)]),el('p',{},rule.description)]),
				el('div',{'class':'az-library-footer'},[el('a',{href:rule.home,target:'_blank',rel:'noopener noreferrer','aria-label':rule.name+_(' 项目主页（新窗口）')},_('主页')),
					el('button',{type:'button','class':'az-button az-button-compact'+(added?' az-button-added':' az-button-add'),'aria-label':(added?_('已添加 '):_('添加规则 '))+rule.name,disabled:added || !editable?true:null,click:function(){return self.stageSource('source_add',[rule.id,'','','domains']).then(function(){if(self.libraryFilter==='available')self.paint('az-library-available');});}},added?[icon('check'),_('已添加')]:[icon('plus'),_('添加规则')])]) ]);}).concat(self.libraryFilter==='available'&&catalog.length&&catalog.every(function(rule){return items.some(function(i){return i.url===rule.url;});})?[el('p',{'class':'az-library-empty',role:'status'},_('规则库中的列表均已添加，无需重复添加。'))]:[])),
			data && data.last ? el('p',{'class':'az-source-footnote'},_('最近完成：')+date(data.last.at)+(enabled?_(' · 当前接入已同步。'):_(' · 开启保护后应用所选规则。'))) : null ]);
	},
	openRules: function() {
		this.enhanceOpen=false;
		this.controlsOpen=false;this.rulesOpen=true;this.paint();
		return this.loadSources();
	},
	draftCount: function() {
		if(!this.draft)return 0;
		var d=this.draft,base=d.base || [],count=base.filter(function(i){return !d.items.some(function(v){return v.id===i.id;});}).length;
		count+=d.items.filter(function(i){var old=base.find(function(v){return v.id===i.id;});return i._new||!old||JSON.stringify(sourceChoice(i))!==JSON.stringify(sourceChoice(old));}).length;
		return count+(d.routing_enabled!==d.routing_before?1:0)+(d.dns&&!sameDNS(d.dns,d.dns_before)?1:0);
	},
	renderCapacity: function(data){
		var c=data&&data.capacity,l=data&&data.limits,label=_('每个来源独立校验，合并后统一去重；设备容量正在读取。');
		if(l&&Number.isInteger(l.source_domains)&&Number.isInteger(l.total_domains))label=_('单源上限 ')+number(l.source_domains)+_(' · 本机最终加载上限 ')+number(l.total_domains)+_(' · 自动按内存分档');
		var receipt='';
		if(c&&Number.isInteger(c.known_domains)){
			receipt=(c.state==='complete'?_('最近完整预检：'):c.state==='over_limit'?_('预检已超限：'):_('容量预检不完整，已知 '))+number(Number.isInteger(c.effective_domains)?c.effective_domains:c.known_domains)+' / '+number(c.limit||500000)+(Number.isInteger(c.effective_domains)?_(' 个预计有效域名'):_(' 个候选域名'));
			if(Number.isInteger(c.input_domains)&&Number.isInteger(c.unique_domains))receipt+=_('；来源记录 ')+number(c.input_domains)+_('，去重后 ')+number(c.unique_domains);
			if(c.state==='unknown')receipt+=_('；部分来源无法计数。');
		}
		return el('div',{id:'az-rule-capacity','class':'az-capacity-summary '+(c&&c.state!=='complete'?'az-inline-warning':'az-source-footnote')},[el('span',{},label),receipt?el('small',{},receipt):null,el('small',{},this.draftCount()?_('当前草稿尚未预检；应用时重新核对，不是累计启用配额。'):_('按原生规则语义扣除已知放行和冗余子域；最终载入数量以运行概览为准。'))]);
	},
	draftError: function(){
		if(this.draftConflict)return _('已应用配置发生真实变化，草稿已保留。请核对差异，撤销旧草稿后按当前配置重新选择；不会自动覆盖。');
		var d=this.draft,s=this.currentStatus||{},blocked=(s.policy||{}).blocked;
		if(d&&flags(s).enabled&&Array.isArray(blocked)&&!d.items.some(function(i){return i.enabled;})&&!blocked.length)return _('本次草稿没有启用订阅或手动拦截项。请保留一项规则；暂停全部过滤请使用暂停或保护开关。');
		return null;
	},
	sourceChanged: function(item) {
		var old=((this.sourcesData||{}).items||[]).find(function(i){return i.id===item.id;});
		return !!this.draft&&(!old||JSON.stringify(sourceChoice(old))!==JSON.stringify(sourceChoice(item)));
	},
	ensureDraft: function() {
		if(this.draft)return;
		this.draftConflict=false;
		var data=this.sourcesData;
		if(!data||!Array.isArray(data.items)||!/^[a-f0-9]{64}$/.test(data.revision||''))throw new Error(_('规则列表尚未就绪，请重新读取。'));
		var items=JSON.parse(JSON.stringify(data.items)),routing=(this.currentStatus.policy||{}).routing_enabled===true;
		this.draft={revision:data.revision,items:items,base:JSON.parse(JSON.stringify(items)),routing_before:routing,routing_enabled:routing,dns_before:JSON.parse(JSON.stringify(data.dns||defaultDNS())),dns:JSON.parse(JSON.stringify(data.dns||defaultDNS()))};
	},
	canStage: function() { var s=this.currentStatus||{};return !this.busy&&!s.stale&&!operationActive(s)&&!this.controlCount()&&!this.sourcesError&&(flags(s).enabled||s.state==='disabled'&&!s.managed); },
	stageRouting: function(value) {
		var self=this;if(!self.canStage())return Promise.resolve();
		return (self.sourcesData?Promise.resolve():self.loadSources()).then(function(){self.ensureDraft();self.draft.routing_enabled=value;self.notice=null;if(!self.draftCount())self.draft=null;self.paint('az-routing');}).catch(function(e){self.notice={kind:'error',text:String(e.message||e)};self.paint();});
	},
	stageSource: function(method,args) {
		var self=this;if(!self.canStage())return Promise.resolve();
		try{
			self.ensureDraft();var d=self.draft,item=d.items.find(function(i){return sourceKey(i)===args[0];});
			if(method==='source_add'){
				var c=args[0]?((self.sourcesData||{}).catalog||[]).find(function(i){return i.id===args[0];}):null;
				var name=c?c.name:args[1],url=c?c.url:args[2],format=c?c.format||'domains':args[3];
				if(!name||!/^https:\/\//i.test(url||''))throw new Error(_('请填写名称和 HTTPS 订阅地址。'));
				url=String(url).replace(/^(https:\/\/[^/?]+)$/,'$1/');
				if(d.items.some(function(i){return i.url===url;}))throw new Error(_('此地址已在列表中。'));
				if(d.items.length>=30)throw new Error(_('最多管理 30 个订阅。'));
				self.draftSequence=(self.draftSequence||0)+1;
				d.items.push({id:null,_new:true,_key:'pending_'+self.draftSequence,name:name,url:url,format:format,enabled:true,https:true,domains:null});
				if(!args[0])self.customSource={name:'',url:'',format:'domains'};
			}else if(item&&item.url===args[1]){
				if(method==='source_toggle')item.enabled=args[2]===true;
				else if(method==='source_remove')d.items=d.items.filter(function(i){return i!==item;});
				else if(method==='source_edit'){
					var form=self.editForm;if(!form.name.trim()||!/^https:\/\//i.test(form.url))throw new Error(_('请填写名称和 HTTPS 订阅地址。'));
					var address=form.url.trim().replace(/^(https:\/\/[^/?]+)$/,'$1/');
					if(d.items.some(function(i){return i!==item&&i.url===address;}))throw new Error(_('此地址已在列表中。不同列表的域名重叠会自动去重。'));
					item.name=form.name.trim();item.url=address;item.format=form.format;item.https=true;item.domains=null;item.updated=null;item.checked=null;
				}
			}
			self.removeSource=null;self.editSource=null;self.notice=null;if(!self.draftCount())self.draft=null;self.paint();
		}catch(e){self.notice={kind:'error',text:String(e.message||e)};self.paint();}
		return Promise.resolve();
	},
	renderSourceEdit: function(item,editable){
		var self=this,form=self.editForm;
		return el('div',{'class':'az-source-edit'},[
			el('div',{'class':'az-custom-grid'},['name','url','format'].map(function(key){
				var label=key==='name'?_('名称'):key==='url'?_('订阅地址'):_('文件格式');
				return el('label',{},[el('span',{},label),key==='format'?el('select',{change:function(e){form.format=e.target.value;},disabled:!editable?true:null},['domains','hosts','adblock'].map(function(v){return el('option',{value:v,selected:form.format===v?true:null},v);})):el('input',{type:key==='url'?'url':'text',value:form[key],maxlength:key==='url'?2048:100,disabled:!editable?true:null,input:function(e){form[key]=e.target.value;}})]);
			})),el('div',{'class':'az-action-row'},[
				el('button',{type:'button','class':'az-button az-button-primary',disabled:!editable?true:null,click:function(){return self.stageSource('source_edit',[sourceKey(item),item.url]);}},_('保存到待应用')),
				el('button',{type:'button','class':'az-button az-button-quiet',click:function(){self.editSource=null;self.paint();}},_('取消'))])]);
	},
	renderDraft: function(s){
		var self=this,problem=self.draftError(),locked=!!self.busy||s.stale||self.sourcesLoading||!self.canStage()||!!problem;
		return el('section',{'class':'az-draft-bar',role:'region','aria-label':_('待应用更改')},[
			el('div',{},[el('strong',{},self.draftCount()+_(' 项更改待应用')),el('p',{},_('当前网络仍使用已应用配置。应用时复用已有规则并统一同步；离开页面前请应用或撤销。')),problem?el('p',{id:'az-draft-error','class':'az-inline-warning',role:'status'},problem):null,self.draftConflict?self.renderDraftConflict():null]),
			el('div',{'class':'az-action-row'},[
				el('button',{id:'az-draft-discard',type:'button','class':'az-button az-button-quiet',disabled:self.busy||self.jobUncertain?true:null,click:function(){self.draft=null;self.draftConflict=false;self.dnsForm=null;self.editSource=null;self.notice=null;self.paint();}},_('撤销更改')),
				el('button',{id:'az-draft-apply',type:'button','class':'az-button az-button-primary','aria-describedby':problem?'az-draft-error':null,disabled:locked?true:null,click:function(){return self.applyDraft();}},_('应用并重新加载'))])]);
	},
	renderDraftConflict:function(){
		var d=this.draft,actual=(this.sourcesData||{}).items||[],base=d.base||[],rows=[];
		base.concat(actual.filter(function(i){return !base.some(function(b){return b.id===i.id;});})).forEach(function(i){var before=base.find(function(b){return b.id===i.id;}),now=actual.find(function(b){return b.id===i.id;}),wanted=d.items.find(function(b){return b.id===i.id;});if(JSON.stringify(before&&sourceChoice(before))!==JSON.stringify(now&&sourceChoice(now)))rows.push(el('li',{},[(now||before).name+_('：当前 ')+(now?(now.enabled?_('启用'):_('停用')):_('已移除'))+_('；草稿 ')+(wanted?(wanted.enabled?_('启用'):_('停用')):_('移除')),el('small',{},_('当前地址：')+(now?now.url:_('无'))),el('small',{},_('草稿地址：')+(wanted?wanted.url:_('无')))]));});
		return this.disclosure('draft-conflict',_('核对配置差异'),rows.length?[el('ul',{},rows)]:[el('p',{},_('过滤、DNS 或其他受保护设置已变化。旧草稿不能直接覆盖当前配置。'))]);
	},
	reloadCurrent: function(){
		if(this.draftCount()){this.notice={kind:'warning',text:_('请先应用或撤销待应用更改；重载按钮只处理已应用规则。')};this.paint();return Promise.resolve();}
		return this.runAction('reload');
	},
	confirmPendingDraft: function(){
		var d=this.draft,s=this.currentStatus||{},data=this.sourcesData;
		if(!d||this.draftConflict||this.sourceFailureReason==='config_conflict'||this.busy&&this.busy!=='status'||operationActive(s)||s.stale||s.state!=='configured_unverified'||!data||data.pending||this.sourcesError)return;
		if(data.items.length===d.items.length && d.items.every(function(i){return data.items.some(function(v){return v.url===i.url&&v.name===i.name&&v.enabled===i.enabled&&(i.format==='unknown'||v.format===i.format);});}) && (s.policy||{}).routing_enabled===d.routing_enabled&&(!d.dns||sameDNS(data.dns,d.dns))){
			this.draft=null;this.notice={kind:'success',text:_('已确认上次重载完成，更改已应用。')};
		}
	},
	applyDraft: function(receipt){
		var self=this;if(!receipt&&(!self.canStage()||!self.draftCount()))return Promise.resolve();
		if(!receipt&&self.draftError()){self.notice={kind:'warning',text:self.draftError()};self.paint();return Promise.resolve();}
		var d=self.draft,p=d?{revision:d.revision,items:d.items.map(sourceChoice),routing_before:d.routing_before,routing_enabled:d.routing_enabled,...(d.dns?{dns_before:d.dns_before,dns:d.dns}:{})}:null;
		var policyError=p&&p.routing_enabled&&self.dnsPolicyError(p.dns);if(!receipt&&policyError){self.notice={kind:'error',text:policyError};self.paint();return Promise.resolve();}
		self.applyingDraft=p;self.busy='rules';self.notice=null;self.paint();
		return (receipt?Promise.resolve(receipt):sourceCalls.settings_apply(JSON.stringify(p))).then(function(reply){return self.continueSourceJob(reply,'settings');}).then(function(){
			if(self.notice&&self.notice.kind==='success'){
				var actual=(self.sourcesData||{}).items||[];
				var matches=!p||(actual.length===p.items.length&&p.items.every(function(i){return actual.some(function(v){return v.url===i.url&&v.name===i.name&&v.enabled===i.enabled&&(i.format==='unknown'||v.format===i.format);});})&&((self.currentStatus.policy||{}).routing_enabled===p.routing_enabled)&&(!p.dns||sameDNS((self.sourcesData||{}).dns,p.dns)));
				if(matches){self.draft=null;self.dnsForm=null;if(self.enhanceData&&self.sourcesData.dns)self.enhanceData.profile=JSON.parse(JSON.stringify(self.sourcesData.dns));self.notice={kind:'success',text:_('更改已统一应用。')};}
				else self.notice={kind:'error',text:_('任务已结束，但配置尚未核对一致。待应用选择已保留。')};
			}else if(self.draft&&self.sourceFailureReason==='config_conflict')self.draftConflict=true;
			self.applyingDraft=null;self.paint();
		}).catch(function(e){self.busy=null;self.notice={kind:'error',text:_('更改尚未应用，选择已保留。'),detail:String(e.message||e)};self.paint();});
	},
	loadSources: function() {
		var self=this,hadData=!!self.sourcesData;
		self.sourcesLoading=true;self.paint();
		return sourceCalls.source_list().then(function(reply){if (!reply || !reply.ok || !reply.data || !Array.isArray(reply.data.items)) throw new Error(reply && reply.message || _('无法读取规则列表。'));self.sourcesData=reply.data;if(!hadData)self.dnsForm=null;self.sourcesError=null;})
			.catch(function(e){self.sourcesError=String(e.message || e);}).then(function(){self.sourcesLoading=false;self.paint();});
	},
	continueSourceJob: function(reply,kind) {
		var self=this,failure=null;self.busy='rules';self.notice=null;self.jobProgress=null;self.jobUncertain=false;self.sourceFailureReason=null;self.paint();
		return trackJob(reply,kind||'source',null,self).catch(function(e){failure=String(e.message || e);self.sourceFailureReason=e.reason;}).then(function(){return taskStatus(self);}).then(function(s){
			self.currentStatus=s;self.lastChecked=new Date();self.domainResult=null;self.removeSource=null;
			var healthy=s.state==='disabled' || s.state==='configured_unverified';
			self.notice=failure || !healthy ? {kind:'error',text:s.state==='configured_unverified'&&(s.backend||{}).state==='ready'?_('本次规则操作未完成，当前保护仍在运行。'):_('规则未确认应用，已重新读取当前状态。'),detail:failure || _('请先处理恢复记录。')} :
				{kind:'success',text:s.state==='disabled'?_('规则选择已保存，开启保护后应用。'):_('规则已应用并同步到当前接入。')};
		}).catch(function(e){self.currentStatus={state:'error',error:String(e)};self.notice={kind:'error',text:_('规则任务结果暂时无法确认，请重新检测。')};})
			.then(function(){finishJob(self);return self.loadSources();}).then(function(){
				if (self.sourcesError) { self.notice={kind:'error',text:_('任务已结束，但规则列表暂时无法核对，请重新读取。')};self.paint(); }
			});
	},
	runSource: function(method,args) {
		var self=this,s=self.currentStatus || {};
		if (self.busy || operationActive(s) || s.stale || self.draftCount() || !(flags(s).enabled || s.state==='disabled' && !s.managed)) return Promise.resolve();
		if (method==='source_add' && !args[0] && (!args[1] || !/^https:\/\//i.test(args[2]))) {self.notice={kind:'error',text:_('请填写名称和 HTTPS 订阅地址。')};self.paint('az-source-name');return Promise.resolve();}
		if (['source_add','source_toggle','source_remove','source_refresh','source_verify'].indexOf(method)<0) return Promise.resolve();
		self.busy='rules';self.notice=null;self.jobProgress=null;self.jobUncertain=false;self.paint();
		return sourceCalls[method].apply(null,args).then(function(reply){return self.continueSourceJob(reply);}).then(function(){
			if (!self.notice || self.notice.kind!=='success') return;
			var items=(self.sourcesData || {}).items || [], matches=true;
			if (method==='source_add') matches=items.some(function(i){return args[0] ? i.catalog_id===args[0] : i.url===String(args[2]).replace(/^(https:\/\/[^/?]+)$/,'$1/');});
			if (method==='source_toggle') matches=items.some(function(i){return i.id===args[0] && i.url===args[1] && i.enabled===args[2];});
			if (method==='source_remove') matches=!items.some(function(i){return i.id===args[0];});
			if (!matches || self.sourcesError) self.notice={kind:'error',text:_('任务已结束，但规则选择尚未核对成功。请重新读取列表。')};
			else if (method==='source_add' && !args[0]) self.customSource={name:'',url:'',format:'domains'};
			if(method==='source_verify'&&self.notice.kind==='success')self.notice={kind:'success',text:_('已校验启用规则的本地快照，未下载订阅或重载网络。')};
			self.paint();
		})
			.catch(function(e){self.busy=null;self.notice={kind:'error',text:_('规则操作未完成。'),detail:String(e.message || e)};self.paint();});
	},
	renderLookup: function() {
		var self = this, r = self.domainResult;
		if (!r) return [];
		if (r.error) return el('p', { 'class': 'az-inline-warning', role: 'alert' }, r.error);
		var hit = r.match === 'exact' || r.match === 'parent';
		return el('div', { 'class': 'az-lookup-result' }, [
			el('div', {}, [ el('strong', {}, r.match === 'allowed' ? _('已在个人放行名单中') : hit ? _('命中 AdZero 过滤规则') : _('未命中当前规则')),
				el('p', { translate: 'no' }, r.domain), hit ? el('p', {}, [ _('命中规则：'), el('strong', { translate: 'no' }, r.rule) ]) : null,
				hit?el('p',{},r.provenance&&r.provenance.items&&r.provenance.items.length?_('命中来源：')+r.provenance.items.map(function(v){return v.name;}).join('、')+(r.provenance.incomplete?_('（部分来源暂不可确认）'):''):_('来源暂不可确认；只核对当前已应用的来源快照。')):null,
				hit ? el('p', { 'class': 'az-scope-note' }, [_('将放行 '), el('strong', { translate: 'no' }, r.scope), _(' 及其所有子域。')]) : el('p', {}, _('这是当前规则匹配结果，不能代替终端 DNS 实测。')) ]),
			hit ? el('button', { id: 'az-allow', type: 'button', 'class': 'az-button az-button-primary', disabled: !self.canControl() ? true : null,
				click: function() { return self.stageControls('allowed',r.scope); } }, _('加入放行待应用')) : el('button',{id:'az-block',type:'button','class':'az-button az-button-quiet',disabled:!self.canControl()?true:null,click:function(){return self.stageControls('blocked',r.domain);}},_('加入拦截待应用')),
			hit?el('div',{'class':'az-action-row'},[600,3600].map(function(seconds){return el('button',{id:'az-temporary-'+seconds,type:'button','class':'az-button az-button-quiet',disabled:!self.canControl()||self.controlCount()||self.draftCount()?true:null,click:function(){return self.runPolicy('temporary',[r.domain,r.scope,seconds]);}},seconds===600?_('临时放行 10 分钟'):_('临时放行 1 小时'));})):null ]);
	},
	lookupDomain: function() {
		var self = this;
		if (self.busy || operationActive(self.currentStatus||{}) || (self.currentStatus || {}).stale || !flags(self.currentStatus || {}).enabled) return Promise.resolve();
		var input = (self.domainInput || '').trim();
		if (!input) { self.domainResult = {error:_('请输入要查询的域名或网址。')}; self.paint('az-domain'); return Promise.resolve(); }
		self.busy = 'lookup'; self.notice = null; self.domainResult = null; self.paint();
		return policyCalls.lookup(input).then(function(result) {
			if (!result || !result.ok || !result.data || !result.data.available) throw new Error(result && result.message || _('当前规则无法查询，请重新检测。'));
			self.domainResult = result.data;
		}).catch(function(err) { self.domainResult = {error:String(err.message || err)}; })
			.then(function() { self.busy = null; self.paint('az-domain'); });
	},
	runPolicy: function(action, args, receipt) {
		var self = this, f = flags(self.currentStatus || {});
		if (['allow','temporary','revoke','pause','resume','routing_on','routing_off'].indexOf(action)<0 || self.busy || !receipt && (operationActive(self.currentStatus||{})||(self.currentStatus || {}).stale || !((action === 'resume' && f.paused) || (['allow','temporary','revoke','pause','routing_on','routing_off'].indexOf(action) >= 0 && f.enabled)))) return Promise.resolve();
		self.busy = action; self.notice = null; self.jobProgress=null;self.jobUncertain=false;self.paint();
		var failure;
		return (receipt ? Promise.resolve(receipt) : policyCalls[action].apply(null,args)).then(function(result) {
			return trackJob(result,'policy',action,self);
		}).catch(function(err) { failure = String(err.message || err); }).then(function(){return taskStatus(self);}).then(function(s) {
			self.currentStatus = s; self.lastChecked = new Date(); self.domainResult = null;
			var allowed = (s.policy || {}).allowed || [], expected = action === 'pause' ? s.state === 'paused' : s.state === 'configured_unverified';
			if ((action === 'allow'||action==='temporary') && !receipt) expected = expected && allowed.indexOf(args[1]) >= 0;
			if(action==='temporary'&&!receipt)expected=expected&&((s.policy||{}).temporary||[]).some(function(t){return t.scope===args[1]&&t.remaining>0;});
			if (action === 'revoke' && !receipt) expected = expected && allowed.indexOf(args[1]) < 0;
			if (action === 'routing_on' || action === 'routing_off') expected = expected && (s.policy || {}).routing_enabled === (action === 'routing_on') && (action === 'routing_off' || ['proxy','direct','native'].indexOf(((s.adapter || {}).routing || {}).mode)>=0);
			self.notice = failure || !expected ? {kind:'error',text:_('操作未确认完成，已重新读取当前状态。'),detail:failure || _('请检查当前状态后重试。')} :
				{kind:'success',text: action==='temporary'?_('临时放行已应用，到期由路由器自动尝试恢复；无需保持页面打开。'):action === 'routing_on' ? _('分流配置已应用，请在设备上验证实际出口。') : action === 'routing_off' ? _('自动分流已关闭，广告过滤继续运行。') : action === 'pause' ? _('保护已暂停，到期后自动尝试恢复。') : action === 'resume' ? _('保护已恢复。') : action === 'allow' ? _('放行名单已应用，并同步到当前接入。') : _('已撤销放行，并同步到当前接入。')};
		}).catch(function(err) {
			self.currentStatus = {state:'error',error:String(err)};
			self.notice = {kind:'error',text:_('操作结果暂时无法确认，请重新检测。'),detail:failure || String(err)};
		}).then(function() { finishJob(self); self.paint(action === 'routing_on' || action === 'routing_off' ? 'az-routing' : action === 'allow' || action === 'revoke' ? 'az-domain' : 'az-toggle'); });
	},
	paint: function(focusId) {
		var panel = this.panelNode || document.getElementById('adzero-panel');
		if (!panel) return;
		var active = document.activeElement, mayFocus = !active || active === document.body || panel.contains(active);
		var editing=active&&active.id&&['input','textarea','select'].indexOf((active.tagName||active.tag||'').toLowerCase())>=0&&panel.contains(active);
		var selection=editing&&typeof active.selectionStart==='number'?[active.selectionStart,active.selectionEnd,active.selectionDirection]:null;
		if(!focusId&&mayFocus&&active&&active.id)focusId=active.id;
		dom.content(panel, this.renderPanel(this.currentStatus || { state: 'loading' }));
		if (focusId && mayFocus) {
			var target = document.getElementById(focusId);
			if (!target || target.disabled) target = document.getElementById('az-status');
			if (target && !target.disabled) {target.focus();if(selection&&target.id===active.id&&target.setSelectionRange)try{target.setSelectionRange(selection[0],selection[1],selection[2]);}catch(e){}}
		}
	},
	refreshStatus: function(initial) {
		var self = this;
		if (self.busy) return Promise.resolve();
		var saved=sessionValue('job') || self.currentJob;
		if(initial!==true && saved && typeof saved==='object' && saved.job){
			var receipt={ok:true,job:saved.job};
			if(saved.kind==='action')return self.runAction(saved.action,receipt);
			if(saved.kind==='controls')return self.applyControls(receipt);
			if(saved.kind==='policy')return self.runPolicy(saved.action,[],receipt);
			if(saved.kind==='upgrade')return self.runUpgrade(receipt);
			if(saved.kind==='source')return self.continueSourceJob(receipt);
			if(saved.kind==='settings')return self.applyDraft(receipt);
		}
		var previous=self.currentStatus,confirmation=self.notice&&self.notice.expectedState?self.notice:null;
		self.statusRecheckRevision=(self.statusRecheckRevision||0)+1;
		self.busy = 'status'; self.notice = confirmation; self.paint();
		return getStatus().then(function(s) {
			self.currentStatus = s; self.lastChecked = new Date();
			if(confirmation)self.notice=confirmationNotice(confirmation,s);
			if((previous.adapter||{}).reload_pending && !(s.adapter||{}).reload_pending && s.state==='configured_unverified' && self.draft)
				return self.loadSources().then(function(){self.confirmPendingDraft();});
		})
			.catch(function(err) {
				if (previous && previous.state!=='loading' && previous.state!=='error') { self.currentStatus=Object.assign({},previous,{stale:true}); self.notice={kind:'error',text:_('无法取得新状态，上次概览仅供参考。请重新检测。')}; }
				else self.currentStatus = { state: 'error', error: String(err) };
			})
			.then(function() { self.busy = null; self.paint(initial === true ? null : 'az-status'); });
	},
	runAction: function(action, receipt) {
		var self = this, f = flags(self.currentStatus || {});
		if (['enable','disable','refresh','reload'].indexOf(action)<0 || self.busy || !receipt && ((self.currentStatus||{}).state==='closing'||operationActive(self.currentStatus||{})&&!(action==='disable'&&queueDisable(self.currentStatus))||confirmingAction(self)||(self.currentStatus || {}).stale || !((action === 'enable' && f.canEnable) || (action === 'disable' && f.canDisable) || ((action === 'refresh'||action === 'reload') && f.enabled)))) return Promise.resolve();
		self.statusRecheckRevision=(self.statusRecheckRevision||0)+1;
		self.busy = action; self.notice = null; self.jobProgress=null;self.jobUncertain=false;self.paint();
		var failure = null;
		return (receipt ? Promise.resolve(receipt) : policyCalls[action]()).then(function(result) {
			return trackJob(result,'action',action,self);
		}).catch(function(err) { failure = err; }).then(function() { return taskStatus(self,failure?null:action==='disable'?'disabled':'configured_unverified'); }).then(function(s) {
			self.currentStatus = s; self.lastChecked = new Date();
			var expected = action === 'disable' ? 'disabled' : 'configured_unverified';
			if(action==='disable'&&s.state==='closing')self.notice=Object.assign(actionConfirmation(action),{text:_('关闭请求已保存，后台将继续恢复。'),detail:failure?String(failure.message||failure):_('完成后页面会自动更新；新的规则应用已暂停。')});
			else if(failure&&failure.reason==='lock_busy')self.notice=lockNotice(action);
			else if(failure&&/^dns_pool_/.test(failure.reason||''))self.notice={kind:'error',text:failure.recovery==='restored'?_('DNS 并发入口启动未完成，本次操作已撤销。'):failure.recovery==='unconfirmed'?_('DNS 并发入口启动失败，恢复尚未确认。'):_('DNS 并发入口尚未确认就绪。'),detail:String(failure.message||failure)};
			else if(!failure&&s.state==='inconsistent')self.notice=actionConfirmation(action);
			else if (failure || s.state !== expected) self.notice = { kind: 'error', text: _('操作未完成，已重新读取当前状态。'), detail: failure ? String(failure) : _('当前状态尚未满足操作结果，请检查服务日志后重新检测。') };
			else self.notice = actionSuccess(action);
		}).catch(function(err) {
			self.currentStatus = { state: 'error', error: String(err) };
			self.notice = { kind: 'error', text: _('操作结果暂时无法确认，请重新检测。'), detail: String(failure || err) };
		}).then(function() { finishJob(self); self.paint(action === 'refresh' ? 'az-update' : 'az-toggle'); });
	},
	render: function(status) {
		var self = this;
		if(self.stopPagePolling)self.stopPagePolling();
		self.pageClosed=false;self.pageEpoch=(self.pageEpoch||0)+1;self.observationReading=false;
		if(typeof window!=='undefined'&&window.location){
			var initialPage=/^#adzero\/(overview|rules|controls|enhance)$/.exec(window.location.hash||'');
			if(initialPage){self.enhanceOpen=initialPage[1]==='enhance';self.rulesOpen=initialPage[1]==='rules';self.controlsOpen=initialPage[1]==='controls';}
			self.pageNavigationHandler=function(){var hash=window.location.hash||'',match=/^#adzero\/(overview|rules|controls|enhance)$/.exec(hash);if(match||!hash)return self.selectPage(match?match[1]:'overview',true);};
			window.addEventListener('popstate',self.pageNavigationHandler);window.addEventListener('hashchange',self.pageNavigationHandler);
		}
		if(typeof window!=='undefined'&&!self.draftLeaveHandler){
			self.draftLeaveHandler=function(e){if((self.draftCount()||self.controlCount()||self.enhanceDirty()||self.unstagedEdits())&&!self.busy){e.preventDefault();e.returnValue='';}};
			window.addEventListener('beforeunload',self.draftLeaveHandler);
		}
		status = status || { state: 'loading' };
		this.panelNode = el('div', { id: 'adzero-panel' }, this.renderPanel(status));
		var root = el('div', { 'class': 'az-root' }, [ el('link', { rel: 'stylesheet', href: L.resource('view/adzero/overview.css') + '?v=0.23.6' }), this.panelNode ]);
		pagePolling(this,this.panelNode);
		if(self.rulesOpen&&!self.sourcesData)self.initialPageRequest=self.loadSources();
		if(self.enhanceOpen)self.initialPageRequest=self.loadEnhance();
		this.initialObservationRequest=this.refreshObservation();
		if (status.cached_at) this.lastChecked=new Date(status.cached_at);
		if (status.state === 'loading' || status.stale) {
			var pendingJob=sessionValue('job');
			this.initialRequest = this.refreshStatus(true).then(function(){
				if (typeof pendingJob==='string') pendingJob={job:pendingJob,kind:'source'};
				if (!pendingJob || !/^[a-f0-9-]{36}$/.test(pendingJob.job || '')) return;
				var receipt={ok:true,job:pendingJob.job};
				if (pendingJob.kind==='action') return self.runAction(pendingJob.action,receipt);
				if (pendingJob.kind==='upgrade') return self.runUpgrade(receipt);
				if (pendingJob.kind==='controls') {self.controlsOpen=true;return self.applyControls(receipt);}
				if (pendingJob.kind==='policy') return self.runPolicy(pendingJob.action,[],receipt);
				if (pendingJob.kind==='enhance') {self.enhanceOpen=true;self.rulesOpen=false;self.controlsOpen=false;return self.runEnhance(pendingJob.action,receipt);}
				if (pendingJob.kind==='source') { self.rulesOpen=true; self.loadSources(); return self.continueSourceJob(receipt); }
				if (pendingJob.kind==='settings') { return self.applyDraft(receipt); }
			});
		}
		// One quiet check per page load. A network error only affects the
		// optional version control; the main LuCI page remains usable.
		if(typeof window!=='undefined'&&!self.updateChecked){
			self.updateChecked=true;
			setTimeout(function(){if(!self.pageClosed&&!self.busy)self.checkUpdate(true);},2500);
		}
		return root;
	},
	handleSave: null, handleSaveApply: null, handleReset: null
});
