'use strict';
// Native plugin extension points only. Invoked under ctl's global lock.
import * as fs from 'fs';
import { cursor } from 'uci';
import { connect } from 'ubus';
import * as net from 'socket';
import * as lib from './adapter-lib.uc';
import * as route from './routing-lib.uc';
import * as progress from './progress-lib.uc';
import * as rulecache from './rules-cache.uc';
import * as proxy from './proxy-state.uc';
import * as native from './dns-native-lib.uc';
import { lan_valid } from './dns-handoff-lib.uc';
import { config as enhance_config } from './dns-enhance-lib.uc';
import { limits } from './capacity-lib.uc';
import { store } from './provider-store.uc';
import * as poolstate from './dns-pool-state.uc';
import { now as monotime } from './dns-io.uc';

const root = getenv('ADZERO_ADAPTER_ROOT') ?? '';
const state_dir = root + '/etc/adzero';
const journal_path = state_dir + '/adapter.json';
const yaml_reader = fs.dirname(sourcepath()) + '/yaml-json.rb';
const uci = cursor(root + '/etc/config');
let split_enabled = uci.get('adzero','main','routing_enabled') == '1';
let dns_profile=route.profile(json(uci.get('adzero','main','dns_profile')??'null'));
let routing_data = null;
let pending_nikki = null;
let journal = null;
let changed = false;
let selected_core = null;
let nailink_foreign_before = null, nailink_foreign_captured = false;
let transaction_previous = null;
let transaction_saved = false;
let clear_nailink_pending, nailink_completed, rules_signature, passwall_engine, nailink_capture_foreign;
let action = shift(ARGV);
if (action == '--') action = shift(ARGV);
let openclash_error = 'hook_failed';
const native_runtime={passwall:proxy.dns_runtime(root,'passwall'),passwall2:proxy.dns_runtime(root,'passwall2'),ssrplus:proxy.dns_runtime(root,'ssrplus')};
let guard, finish_guard, pool_needed, prepare_pool, verify_pool, pool_preflight, strict_target, policy_prerequisites, passwall_pool_foreign, stop_pool;

function quote(s) { return "'" + replace(s, /'/g, "'\\''") + "'"; }
function service_command(plugin,operation){
	let path=root+'/etc/init.d/'+(plugin=='ssrplus'?'shadowsocksr':plugin);
	// /bin/sh init.d/name ignores its rc.common shebang and only defines
	// functions. Explicitly preserve OpenWrt's interpreter argument.
	let header=split(fs.readfile(path,256)??'','\n')[0];
	if(match(header,/^#!\/bin\/sh[[:space:]]+\/etc\/rc.common([[:space:]]|$)/))
		return ['/bin/sh',root+'/etc/rc.common',path,operation];
	return ['/bin/sh',path,operation];
}
function timeout_helper(){
	let test=root?getenv('ADZERO_SERVICE_TIMEOUT'):null;
	for(let path in test?[test]:[root+'/usr/libexec/timeout-coreutils',root+'/usr/bin/timeout'])if(fs.access(path,'x'))return path;
	die('缺少 coreutils-timeout，尚未重启 DNS 或代理；请补齐安装依赖。');
}
function restart_service(plugin){
	let argv=service_command(plugin,'restart');
	// Timed ucode system() in the supported firmware leaks a blocked SIGCHLD
	// into directly spawned daemons. An external deadline leaves that mask
	// untouched, while still bounding the native restart and its process group.
	return system([timeout_helper(),'-k','5','120','/bin/sh','-c','umask 022; exec "$@"','adzero-service',...argv]);
}
function capture(argv) {
	let proc = fs.popen(join(' ', map(argv, quote)) + ' 2>/dev/null', 'r');
	if (!proc) die('Cannot execute adapter prerequisite');
	let data = proc.read('all'), rc = proc.close();
	if (rc != 0) die('Adapter prerequisite failed (private output omitted)');
	return data;
}
function directory(path) {
	if (fs.stat(path)?.type == 'directory') return;
	directory(fs.dirname(path));
	if (!fs.mkdir(path, 0700)) die('Cannot create adapter directory');
}
function atomic(path, data, mode) {
	if (fs.lstat(path)?.type == 'link') die('Refusing to replace an adapter target symlink');
	directory(fs.dirname(path));
	let name = path + '.adzero.' + time();
	let tmp = fs.open(name, 'wx', 0600);
	if (!tmp) die('Cannot create adapter temporary file');
	let written = tmp.write(data), closed = tmp.close(); // close flushes, with a checked return value
	if (written != length(data) || !closed) { fs.unlink(name); die('Short adapter write'); }
	if (!fs.chmod(name, mode ?? 0600) || !fs.rename(name, path)) { fs.unlink(name); die('Cannot commit adapter file'); }
}
function save() {
	if (transaction_previous != null && !transaction_saved) {
		atomic(state_dir + '/adapter.previous.json', transaction_previous);
		transaction_saved = true;
	}
	atomic(journal_path, sprintf('%J', journal));
}
function read_journal() {
	let raw = fs.readfile(journal_path);
	if (raw == null) return null;
	let value = json(raw);
	if (value.schema != 1 || !value.plugin || type(value.files) != 'array') die('Invalid adapter recovery journal');
	return value;
}
const providers=store(state_dir+'/providers',limits(root).output_bytes,atomic);
function file_pack(raw,kind){return kind=='owned'||length(raw??'')>65536?providers.put(raw):raw;}
function file_read(ref,kind){return type(ref)=='object'||kind=='owned'?providers.read(ref):ref;}
function file_match(path,ref,kind){return type(ref)=='object'||kind=='owned'?providers.matches(path,ref):fs.readfile(path)==ref;}
function tracked(path, content, kind, mode) {
	let original = fs.readfile(path), entry;
	for (let file in journal.files) if (file.path == path) entry = file;
	if (entry) {
		if (!file_match(path,entry.after,kind)) die('Adapter file changed externally; refusing to overwrite');
		if (original == content) return;
	} else {
		if (kind == 'owned' && original != null) die('Reserved AdZero provider file already exists');
		entry = {path, before:file_pack(original,kind), after:file_pack(original,kind), kind, mode:(fs.stat(path)?.mode ?? mode ?? 0600) & 0777};
		push(journal.files, entry);
	}
	entry.previous_after = entry.after;
	entry.after = file_pack(content,kind);
	save(); // durable intent precedes mutation
	atomic(path, content, mode ?? entry.mode);
	changed = true;
}
function same(a, b) { return sprintf('%J', a) == sprintf('%J', b); }
function alias_wanted(){return enhance_config(json(uci.get('adzero','main','dns_enhance')??'null')).enabled&&!dns_profile.strict;}
function alias_lan(){
	let info=json(capture(['ubus','call','network.interface.lan','status'])),addresses=info?.['ipv4-address'];
	if(length(addresses??[])!=1)die('native_lan');let lan={device:info.l3_device,address:addresses[0].address,mask:addresses[0].mask};
	if(!lan_valid(lan))die('native_lan');return lan;
}
function native_bridge(operation,context){
	let path=state_dir+'/native-operation.json';atomic(path,sprintf('%J',context));
	try{capture([root+'/usr/libexec/adzero/dns-native',operation,path]);fs.unlink(path);}catch(e){fs.unlink(path);die(e);}
}
function retire_alias(){if(journal?.alias_retire){native_bridge('cleanup',journal.alias_retire);delete journal.alias_retire;save();}}
function native_dns_state(){
	return journal?.runtime_supported?native_runtime[journal.plugin](journal.runtime_samples,journal.dns_engine):{state:'unknown',reason:'runtime_unavailable'};
}
function native_needs_reload(value){return value?.state=='missing'||value?.reason=='dns_sigchld_blocked';}
function record_dns_runtime(value){
	if(!same(journal.runtime,value)){journal.runtime=value;save();}
	if(value.state=='ready'&&(journal.runtime_retry_after||journal.runtime_retries)){
		delete journal.runtime_retry_after;delete journal.runtime_retries;save();
	}
}
function wait_native_dns(){
	let value=native_dns_state();
	for(let attempt=0;native_needs_reload(value)&&attempt<4;attempt++){
		progress.stage('confirming_proxy');sleep(1000);value=native_dns_state();
	}
	if(journal?.plugin=='passwall2'&&value.state!='ready')die('PassWall2 的实际 DNS 实例尚未确认载入过滤文件，未将本次接入记为成功；请检查其 DNS 重定向与默认实例。');
	return value;
}

function openclash_paths() {
	let source=uci.get('openclash','config','config_path');
	if(type(source)!='string'||substr(source,0,22)!='/etc/openclash/config/'||index(source,'..')>=0)
		die('OpenClash selected configuration path is unavailable');
	let name=substr(source,22);
	if(!match(name,/^[^\/\r\n]+[.]ya?ml$/)||substr(name,0,1)=='.')die('Unexpected OpenClash configuration filename');
	return {temporary:'/tmp/'+name,generated:'/etc/openclash/'+name};
}
const openclash_errors={
	target:'OpenClash supplied an unexpected generated configuration path',
	dns_disabled:'OpenClash DNS is disabled; automatic adapter is unavailable',
	core_missing:'OpenClash requires its Mihomo/Meta core',
	core_rejected:'OpenClash core rejected the adapted configuration',
	hook_failed:'OpenClash custom configuration hook failed'
};
function openclash_failure(reason) {
	progress.failure(reason=='dns_disabled'?'openclash_dns':reason=='core_missing'||reason=='core_rejected'?'openclash_core':'openclash_hook');
	die(openclash_errors[reason]??openclash_errors.hook_failed);
}

function setting(config, section, option, value) {
	if (length(uci.changes(config)?.[config] ?? [])) die('配置有其他页面的未提交修改，拒绝一并提交。');
	let entry;
	for (let flag in journal.flags) if (flag.config == config && flag.section == section && flag.option == option) entry = flag;
	let current = uci.get(config, section, option);
	if (entry && !same(current, entry.after)) die('Adapter UCI setting changed externally');
	if (!entry) { entry = {config, section, option, before:current, after:value}; push(journal.flags, entry); save(); }
	if (same(current, value)) return;
	entry.previous_after=entry.after;entry.after=value;save();
	if (!(value==null?uci.delete(config,section,option):uci.set(config, section, option, value)) || !uci.commit(config)) die('Cannot save adapter UCI setting');
	uci.unload(config);uci.load(config); // libuci retains the cursor's committed delta history
	changed = true;
}
function rules() {
	let mode = uci.get('adblock-fast', 'config', 'dns');
	let path = mode == 'unbound.adb_list' ? '/var/lib/unbound/adb_list.adblock-fast' : '/var/run/adblock-fast/' + mode;
	if (index(['dnsmasq.servers','dnsmasq.addnhosts','smartdns.domainset','unbound.adb_list'], mode) < 0) die('Unsupported processed rules mode');
	let data;
	for (let attempt = 0; attempt < 30; attempt++) {
		data = fs.stat(root + path)?.size;
		if (data > 0) break;
		sleep(1000);
	}
	if (!(data > 0)) die('Backend has not produced a non-empty blocklist');
	return rulecache.load(root + path, mode, root);
}
function yaml(path, engine) {
	let text = fs.readfile(path);
	if (!length(trim(text ?? ''))) return {};
	let data;
	if (engine == 'nikki') data = capture(['yq', '-o=json', '.', path]);
	// Keep OpenClash's existing ruby-yaml dependency, but emit strict JSON:
	// Psych.to_json emits YAML-only escapes for emoji and control characters.
	else {
		try { data=capture(['ruby',yaml_reader,path]); }
		catch(e) { progress.failure('openclash_yaml');die('OpenClash YAML conversion failed (private configuration omitted)'); }
	}
	let value = json(data);
	if (value == null) return {};
	if (type(value) != 'object') die('Expected a YAML mapping');
	return value;
}
function confirm_openclash() {
	let config=yaml(root+openclash_paths().generated,'openclash'),provider=config['rule-providers']?.['adzero-dns'];
	let filter_rule=config.dns?.['fake-ip-filter-mode']=='rule'?'RULE-SET,adzero-dns,real-ip':'rule-set:adzero-dns';
	if(config.dns?.enable!=true||provider?.type!='file'||provider?.behavior!='domain'||
		index(['/etc/openclash/rule_provider/adzero.txt','./rule_provider/adzero.txt'],provider?.path)<0||
		!same(config.dns?.['nameserver-policy']?.['rule-set:adzero-dns'],['rcode://name_error'])||
		index(config.dns?.['fake-ip-filter']??[],filter_rule)<0||index(config.rules??[],'RULE-SET,adzero-dns,REJECT')<0)
		openclash_failure('hook_failed');
	if(journal.split&&(config.mode!='rule'||index(config.rules??[],'RULE-SET,adzero-cn-domain,DIRECT')<0))openclash_failure('hook_failed');
}
function graphql(query, variables, readiness) {
	let operation = index(query, 'run(dry:false)') >= 0 ? 'reload' :
		index(query, 'parsedDns') >= 0 ? 'validate_dns' : index(query, 'updateDns') >= 0 ? 'write_dns' :
		index(query, 'parsedRouting') >= 0 ? 'validate_routing' : index(query, 'updateRouting') >= 0 ? 'write_routing' : 'read';
	// Isolated API transaction fixture; unavailable on an unprefixed router.
	let fixture = getenv('ADZERO_NAILINK_API_FIXTURE');
	if (root && fixture) {
		let request = state_dir + '/fixture-request.json';
		atomic(request,sprintf('%J',{query,variables:variables ?? {},readiness:!!readiness}));
		try { let data = json(capture([fixture,request])); fs.unlink(request); return data; }
		catch(e) { fs.unlink(request); die(e); }
	}
	// Use NaiLink's LuCI CGI gateway. No passwords, private JWT keys or SQLite edits.
	let bus = connect(), session, request, output;
	if (!bus) die('NaiLink requires rpcd/ubus');
	try {
		session = bus.call('session', 'create', {timeout:60})?.ubus_rpc_session;
		if (!session || !match(session, /^[a-f0-9]{32}$/)) die('Cannot create local adapter session');
		bus.call('session', 'grant', {ubus_rpc_session:session, scope:'access-group', objects:[['luci-app-daed-router','write']]});
		if (bus.call('session', 'access', {ubus_rpc_session:session, scope:'access-group', object:'luci-app-daed-router', function:'write'})?.access !== true)
			die('NaiLink local LuCI session was not granted access; no API request sent');
		let payload = readiness ? '' : sprintf('%J', {query, variables:variables ?? {}});
		request = state_dir + '/graphql-request.' + session + '.json';
		atomic(request, payload);
		let path = readiness ? '/health/ready' : '/graphql';
		let argv = ['env', 'REQUEST_METHOD=' + (readiness ? 'GET' : 'POST'), 'REQUEST_URI=/cgi-bin/daed-router' + path,
			'SCRIPT_NAME=/cgi-bin/daed-router', 'PATH_INFO=' + path, 'SERVER_PROTOCOL=HTTP/1.1',
			'HTTP_HOST=localhost', 'HTTP_ORIGIN=http://localhost', 'HTTP_COOKIE=daed_luci_session=' + session,
			'CONTENT_TYPE=application/json', 'CONTENT_LENGTH=' + length(payload), root + '/usr/libexec/daed/luci-proxy'];
		output = state_dir + '/graphql-response.' + session;
		atomic(output, '');
		// Bound read-only probes too: a database busy during reload must not
		// turn a completion poll into another 30-second wait per query.
		let rc = system(join(' ', map(argv, quote)) + ' < ' + quote(request) + ' > ' + quote(output) + ' 2>/dev/null', operation == 'read' ? 5000 : 35000);
		let response = fs.readfile(output, 8388609); fs.unlink(output);
		fs.unlink(request);
		if (rc != 0 || !length(response)) die('NaiLink CGI request failed');
		response = replace(response, /\r\n/g, '\n');
		let separator = index(response, '\n\n');
		if (separator < 0) die('NaiLink CGI returned an invalid response (' + operation + ')');
		let status = match(substr(response, 0, separator), /(^|\n)Status: ([0-9]{3})/);
		let code = status ? +status[2] : 200;
		let not_ready = readiness && code == 503;
		if (code >= 400 && !not_ready) {
			let reason = index([401,403],code) >= 0 ? 'local session denied' :
				index([502,504],code) >= 0 ? 'upstream unavailable or timed out; completion unconfirmed' :
				code == 503 ? 'gateway or backend not ready' : code == 413 ? 'request too large' : 'request rejected';
			if (operation == 'reload' && index([502,504],code) >= 0 && journal) {
				journal.nailink_reload_unconfirmed = true; save();
			}
			die('NaiLink CGI HTTP ' + code + ' (' + operation + '): ' + reason + '; private response omitted');
		}
		let result;
		try { result = json(substr(response, separator + 2)); }
		catch(e) { die('NaiLink CGI returned an invalid response (' + operation + '); private response omitted'); }
		if (not_ready && result?.ready !== false)
			die('NaiLink gateway or backend not ready; private response omitted');
		if (readiness) { bus.call('session','destroy',{ubus_rpc_session:session}); bus.disconnect(); return result; }
		// Only an explicit old-schema rejection permits the legacy API fallback.
		// A transport/auth failure during a core switch must remain uncertain.
		if(query=='{coreStatus}' && length(result.errors??[]) &&
			length(filter(result.errors,e=>type(e.message)=='string' && match(e.message,/([Cc]annot query field|[Uu]nknown field).*coreStatus/)))==length(result.errors))
			result={data:{coreStatus:null}};
		if (length(filter(result.errors ?? [], e => type(e.message) == 'string' && substr(e.message,0,13) == 'access denied'))) {
			progress.failure('nailink_auth');
			die('NaiLink GraphQL authorization denied (' + operation + '); local LuCI session passed; check NaiLink local identity; private response omitted');
		}
		if (length(result.errors ?? []) || !result.data) {
			// A completed GraphQL error is a definite negative acknowledgement,
			// unlike a disconnected transport with no completed response.
			if(operation=='reload')clear_nailink_pending();
			die('NaiLink API rejected ' + operation + ' (private response omitted)');
		}
		bus.call('session', 'destroy', {ubus_rpc_session:session});
		bus.disconnect();
		return result.data;
	} catch (e) {
		if (session) bus.call('session', 'destroy', {ubus_rpc_session:session});
		bus.disconnect();
		if(request)fs.unlink(request);
		if(output)fs.unlink(output);
		die(e);
	}
}
function nailink_runtime_state(with_modified){
	let current=graphql('{coreStatus}').coreStatus;
	let legacy=graphql(with_modified?'{general{dae{running modified}}}':'{general{dae{running}}}').general?.dae;
	let state=proxy.nailink_status(current,legacy);
	if(state.check_health)state=proxy.nailink_health(state,graphql('',null,true));
	delete state.check_health;
	return {...legacy,...state,legacy_running:legacy?.running};
}
function active_plugin() {
	let inventory = getenv('ADZERO_ADAPTER_INVENTORY');
	let data = inventory ? json(fs.readfile(inventory)) : json(capture(['/bin/sh', root + '/usr/libexec/adzero/probe']));
	if (length(data.dns?.uncertain_proxies ?? [])) die(index(data.dns.uncertain_proxies,'nailink')>=0?'无法确认 NaiLink 内核状态，请检查 NaiLink 后重新检测；尚未修改配置。':'无法确认当前代理内核状态，请检查对应插件后重新检测；尚未修改配置。');
	let active = filter(data.plugins, proxy.active);
	if (length(active) > 1) die('Multiple active proxy plugins require manual DNS review');
	if (!length(active)) { if(split_enabled)timeout_helper();policy_prerequisites('none');return 'none'; }
	if (index(['nailink','passwall','passwall2','openclash','nikki','ssrplus'], active[0].id) < 0) die('Active proxy adapter is not supported yet');
	if(active[0].id!='nailink')timeout_helper();
	if(active[0].id=='passwall'){
		if(split_enabled){
			let engine=passwall_engine();
			if(index(['dnsmasq','chinadns-ng'],engine)<0)die('当前 PassWall DNS 引擎不支持 AdZero 自动分流；请保留原生分流或关闭 AdZero 自动分流。');
			if(engine=='chinadns-ng'){
				let helper=fs.readfile(root+'/usr/share/passwall/helper_chinadns_add.lua',1048577)??'';
				if(index(helper,'group null')<0||index(helper,'block_host')<0)die('当前 PassWall 缺少受支持的 ChinaDNS-NG 原生屏蔽接口。');
			}
		}
	}
	if(active[0].id=='openclash')openclash_paths();
	if(active[0].id=='passwall2'){
		let helper=fs.readfile(root+'/usr/share/passwall2/helper_dnsmasq.lua',1048577)??'';
		if(index(helper,'function copy_instance')<0||index(helper,'conf-dir=')<0||index(helper,'address=')<0||uci.get('adblock-fast','config','dns')!='dnsmasq.servers')die('当前 PassWall2 未确认可继承系统 dnsmasq 的过滤文件，原配置保持不变。');
		if((uci.get('passwall2','@global[0]','dns_redirect')??'1')!='1')die('PassWall2 过滤接入需要其原生 DNS 重定向开启；AdZero 不会代为更改。');
	}
	if(active[0].id=='ssrplus'){
		if(split_enabled&&action!='routing-check')die('SSR Plus+ 当前支持广告过滤；请先关闭 AdZero 自动分流，保留 SSR Plus+ 原生分流。');
		let helper=fs.readfile(root+'/usr/share/shadowsocksr/gfw2ipset.sh',1048577)??'';
		if(index(helper,'/etc/ssrplus/deny.list')<0||index(helper,'denylist.conf')<0)die('当前 SSR Plus+ 缺少受支持的原生域名屏蔽接口。');
	}
	// Check the real gateway/backend before ctl enables or restarts filtering.
	// Merely recognizing a running process does not prove API authorization.
	selected_core=active[0].core??null;
	let nailink_runtime = active[0].id == 'nailink' ? nailink_runtime_state() : null;
	if(nailink_runtime?.reason=='core_transition')die('NaiLink 正在切换内核，请等待其完成；原配置保持不变。');
	if (active[0].id == 'nailink' && type(nailink_runtime?.running) != 'bool') die('NaiLink returned invalid runtime state');
	if(active[0].id=='nailink'&&selected_core&&nailink_runtime.core!=selected_core)die('NaiLink 正在切换内核，请等待其完成；原配置保持不变。');
	if(active[0].id=='nailink')selected_core=nailink_runtime.core;
	if (split_enabled && active[0].id!='ssrplus') {
		if (active[0].id == 'nailink') {
			if (nailink_runtime?.running !== true) {policy_prerequisites('none');return 'none';}
		} else if (active[0].runtime == 'stopped') {policy_prerequisites('none');return 'none';}
		else if (active[0].runtime != 'running') {
			let expected=index(['passwall','passwall2'],active[0].id)>=0?['xray','sing-box']:['mihomo','clash'];
			if (!length(filter(data.cores??[],c=>index(expected,c)>=0))) die('无法确认当前代理内核状态，请检查代理服务。');
		}
	}
	policy_prerequisites(active[0].id);
	return active[0].id;
}
function selected_dns() {
	let values = graphql('{dnss(selected:true){id dns{string}}}').dnss;
	if (length(values) != 1 || type(values[0].dns.string) != 'string') die('Expected one selected NaiLink DNS profile');
	return values[0];
}
function update_dns(id, text) {
	nailink_capture_foreign();
	graphql('query($raw:String!){parsedDns(raw:$raw){string}}', {raw:text});
	graphql('mutation($id:ID!,$dns:String!){updateDns(id:$id,dns:$dns){id}}', {id, dns:text});
}
function selected_traffic(require_proxy) {
	let data=graphql('{routings(selected:true){id routing{string fallback{... on Plaintext{val}}}} groups{name}}');
	if(length(data.routings??[])!=1)die('需要唯一选中的 NaiLink 流量路由。');
	let current=data.routings[0],target=current.routing?.fallback?.val;
	if((require_proxy??split_enabled)&&index(map(data.groups??[],g=>g.name),target)<0)die('无法确认 NaiLink 默认代理策略，请检查当前路由。');
	return {id:current.id,text:current.routing.string,target};
}
function routing_restore_conflict() {
	if (journal.recovery_reason != 'nailink_routing_conflict') {
		journal.recovery_reason = 'nailink_routing_conflict';
		save();
	}
	progress.failure('nailink_routing_conflict');
	die(progress.messages.nailink_routing_conflict);
}
function update_traffic(id,text,expected) {
	nailink_capture_foreign();
	graphql('query($raw:String!){parsedRouting(raw:$raw){string}}',{raw:text});
	if (expected != null) {
		let current = selected_traffic();
		if (current.id != id || current.text != expected) routing_restore_conflict();
	}
	graphql('mutation($id:ID!,$routing:String!){updateRouting(id:$id,routing:$routing){id}}',{id,routing:text});
	if (expected != null) {
		let current = selected_traffic();
		if (current.id != id || current.text != text) routing_restore_conflict();
	}
}
function restore_traffic_plan(runtime) {
	let current = selected_traffic(), recorded = journal.traffic;
	if (current.id == recorded.id) {
		if (index([recorded.before, recorded.after, recorded.previous_after], current.text) < 0)
			routing_restore_conflict();
		return current;
	}
	// A replaced profile may carry the original blocks plus new user rules.
	// Only rebind when the original is gone and no other profile uses our data.
	let clean = runtime?.modified === false && journal.split && !journal.alias
		? route.restore_copied_dae_routing(current.text, recorded) : null;
	if (clean == null) routing_restore_conflict();
	let profiles = graphql('{routings{id routing{string}}}').routings;
	if (type(profiles) != 'array') routing_restore_conflict();
	let matching = filter(profiles, p => p.id == current.id);
	if (length(matching) != 1 || matching[0].routing?.string !== current.text ||
		length(filter(profiles, p => p.id == recorded.id ||
			p.id != current.id && route.dae_routing_references(p.routing?.string))))
		routing_restore_conflict();
	// Save both sides before mutation so a retry can recognize a completed write.
	journal.traffic = {id:current.id, before:trim(clean), after:current.text};
	return current;
}
function apply_alias(plugin){
	if(plugin!='nailink')return;
	let dns=selected_dns(),traffic=null,next_dns=native.strip_dns(dns.dns.string,journal.alias),next=null,plan=null;
	if(journal.alias){traffic=selected_traffic();next=native.strip_routing(traffic.text,journal.alias);}
	if(alias_wanted()&&(!selected_core||selected_core=='dae')){
		// Validate both native profiles before changing either of them. Old
		// cores/listeners remain usable when this optional capability is absent.
		try{
			let lan=alias_lan();traffic??=selected_traffic();next??=traffic.text;
			if(!journal.alias)for(let kind in [net.SOCK_DGRAM,net.SOCK_STREAM]){
				let s=net.create(net.AF_INET,kind);if(!s)die('native_socket');let ok=s.bind({address:lan.address,port:5355});s.close();if(!ok)die('native_port_busy');
			}
			let candidate=native.nai_dns(next_dns,lan),routing=native.nai_routing(next,lan);
			graphql('query($raw:String!){parsedDns(raw:$raw){string}}',{raw:candidate});
			graphql('query($raw:String!){parsedRouting(raw:$raw){string}}',{raw:routing});
			plan={plugin:'nailink',port:5355,lan};next_dns=candidate;next=routing;delete journal.alias_unavailable;
		}catch(e){journal.alias_unavailable='native_listener_or_routing_unavailable';}
	}else if(alias_wanted())journal.alias_unavailable='native_core_listener_not_supported';
	if(traffic&&journal.traffic&&(traffic.id!=journal.traffic.id||traffic.text!=journal.traffic.after))die('NaiLink 流量路由已被外部修改。');
	if(journal.alias&&!same(journal.alias,plan))journal.alias_retire=journal.alias;
	journal.alias=plan;
	if(traffic){journal.traffic??={id:traffic.id,before:traffic.text,after:traffic.text};journal.traffic.previous_after=traffic.text;journal.traffic.after=next;}
	journal.dns.previous_after=dns.dns.string;journal.dns.after=next_dns;save();
	// Install a persistent native fallback BEFORE eBPF releases LAN DNS.
	// A dead enhancement process then leaves the same native resolver usable.
	if(journal.alias_retire&&plan)retire_alias();
	if(plan)native_bridge('install',plan);
	if(dns.dns.string!=next_dns){update_dns(dns.id,next_dns);changed=true;}
	if(traffic&&traffic.text!=next){update_traffic(traffic.id,next);changed=true;}
}
function nailink_identity() {
	let fixture=getenv('ADZERO_NAILINK_IDENTITY_FIXTURE');
	if(root && getenv('ADZERO_NAILINK_API_FIXTURE') && fixture)return json(fs.readfile(fixture));
	let bus = connect();
	if (!bus) return null;
	let services;
	try { services = bus.call('service','list',{name:'daed'}); } catch(e) { bus.disconnect(); return null; }
	bus.disconnect();
	let instances = filter(values(services?.daed?.instances ?? {}), i => i.running === true && type(i.pid) == 'int');
	if (length(instances) != 1) return null;
	let pid = instances[0].pid;
	if (pid <= 1) return null;
	let stat = match(fs.readfile('/proc/' + pid + '/stat') ?? '', /^[0-9]+ \(.*\) (.*)$/);
	let fields = stat ? split(stat[1], /[ \t]+/) : [];
	let boot = trim(fs.readfile('/proc/sys/kernel/random/boot_id') ?? '');
	if (!length(boot) || !match(fields[19] ?? '', /^[0-9]+$/)) return null;
	return {boot,pid,start:fields[19]};
}
function nailink_foreign_revision() {
	// A modified=true result includes our own staged DNS edits. Capture all
	// other configuration consumed by run(), so explicit recovery can distinguish
	// that from new user edits. Never persist or log node links/credentials.
	let temporary;
	try {
		let fields=graphql('{__type(name:"Global"){fields{name type{kind name ofType{kind name ofType{kind name ofType{kind name}}}}}}}').__type?.fields;
		if(type(fields)!='array'||!length(fields)||length(fields)>200)return null;
		let names=[];
		for(let field in fields){
			let t=field.type;for(let i=0;i<4&&t?.ofType;i++)t=t.ofType;
			if(!match(field.name??'',/^[A-Za-z_][A-Za-z0-9_]{0,80}$/)||index(['SCALAR','ENUM'],t?.kind)<0)return null;
			push(names,field.name);
		}
		let data=graphql('{configs(selected:true){id global{'+join(' ',sort(names))+'}} groups{id name policy policyParams{key val} nodes{id name link tag subscriptionID} subscriptions{nameFilterRegex subscription{id tag link} matchedNodes{id name link tag subscriptionID}}}}');
		if(length(data.configs??[])!=1||type(data.groups)!='array')return null;
		function canonical(value,key){
			if(type(value)=='array'){
				let list=map(value,v=>canonical(v));
				if(index(['configs','groups','nodes','matchedNodes','subscriptions'],key)>=0)list=sort(list,(a,b)=>sprintf('%J',a)<sprintf('%J',b)?-1:sprintf('%J',a)>sprintf('%J',b)?1:0);
				return list;
			}
			if(type(value)!='object')return value;
			let result={};for(let k in sort(keys(value)))result[k]=canonical(value[k],k);return result;
		}
		let text=sprintf('%J',canonical(data));if(length(text)>4194304)return null;
		// Private scratch stays in tmpfs, outside persistent state/backups.
		temporary=root+'/tmp/adzero-nailink-owner.'+trim(fs.readfile('/proc/sys/kernel/random/uuid'));
		atomic(temporary,text,0600);let sha256=rulecache.fingerprint(temporary);fs.unlink(temporary);
		return sha256?{schema:1,sha256,daemon:rulecache.fingerprint(root+'/etc/config/daed')}:null;
	}catch(e){if(temporary)fs.unlink(temporary);return null;}
}
nailink_capture_foreign=function(){
	if(nailink_foreign_captured)return;
	nailink_foreign_captured=true;
	if(!journal?.nailink_reload_unconfirmed){
		nailink_foreign_before=nailink_foreign_revision();
		if(journal){journal.nailink_foreign_baseline=nailink_foreign_before;save();}
	}
};
function confirm_nailink_recovery() {
	if (!journal?.nailink_reload_unconfirmed) return;
	if (nailink_completed()) { clear_nailink_pending(); return; }
	let identity = nailink_identity();
	if(action=='remove' && identity && journal.nailink_run_identity && same(identity,journal.nailink_run_identity)){
		// Only an explicit close can recover an old, unprovable receipt by
		// replacing the native manager. Scheduled sync never restarts it here.
		let runtime=nailink_runtime_state(true);
		if(runtime.running==null){progress.failure('nailink_transition');die(progress.messages.nailink_transition);}
		let foreign=journal.nailink_completion?.foreign;
		let foreign_matches=foreign?.schema==1&&same(foreign,nailink_foreign_revision());
		if(foreign?.schema==1&&!foreign_matches){progress.failure('nailink_external_edit');die('NaiLink 全局、群组、节点或原生服务设置已被修改，未重启代理；请先在 NaiLink 完成或撤销修改。');}
		if(journal.dns && (runtime.running===true || runtime.running===false&&runtime.legacy_running===false&&foreign_matches) && (runtime.modified===false||foreign_matches) &&
			!same(journal.nailink_recovery_restart,identity)){
			let dns=selected_dns();
			if(dns.id!=journal.dns.id || index([journal.dns.before,journal.dns.after,journal.dns.previous_after],dns.dns.string)<0)
				die('NaiLink DNS 已被外部修改，未重启代理；恢复记录保留。');
			if(journal.traffic){let traffic=selected_traffic();if(traffic.id!=journal.traffic.id || index([journal.traffic.before,journal.traffic.after,journal.traffic.previous_after],traffic.text)<0)die('NaiLink 流量路由已被外部修改，未重启代理。');}
			for(let file in journal.files)if(!file_match(file.path,file.after,file.kind) && !file_match(file.path,file.before,file.kind) && !file_match(file.path,file.previous_after,file.kind))die('NaiLink 规则文件已被外部修改，未重启代理。');
			let phase=journal.nailink_completion?.forward?'forward':'restore',count=journal.nailink_recovery_count??0;
			if(count>=2||count>0&&(!journal.nailink_recovery_stage||journal.nailink_recovery_stage==phase)){progress.failure('nailink_recovery_limit');die('本阶段已尝试过 NaiLink 原生恢复；记录保留，将继续核对，不会反复重启。');}
			if(!fs.access(root+'/etc/init.d/daed','x'))die('NaiLink 原生恢复服务不可用，恢复记录已保留。');
			timeout_helper();
			if(foreign_matches){nailink_foreign_captured=true;nailink_foreign_before=foreign;}
			journal.nailink_recovery_restart=identity;journal.nailink_recovery_count=count+1;journal.nailink_recovery_stage=phase;save();
			progress.stage('recovering_proxy');
			restart_service('daed');
			for(let attempt=0;attempt<10;attempt++){
				identity=nailink_identity();
				if(identity && !same(identity,journal.nailink_run_identity)){
					try{graphql('{general{dae{running}}}');break;}catch(e){}
				}
				if(root && getenv('ADZERO_NAILINK_API_FIXTURE'))break;
				sleep(1000);
			}
		}
	}
	if (!identity || !journal.nailink_run_identity || same(identity,journal.nailink_run_identity)) {
		progress.failure('nailink_gateway');
		die('NaiLink reload completion is unconfirmed; no second mutation submitted. Restart the native NaiLink service before recovery; journal retained');
	}
	// A different procd process plus a responsive authorized API proves the
	// previous daemon's request cannot still commit after this restoration.
	graphql('{general{dae{running}}}');
	clear_nailink_pending();
}
clear_nailink_pending = function() {
	delete journal.nailink_reload_unconfirmed;
	delete journal.nailink_run_identity;
	delete journal.nailink_completion;
	delete journal.nailink_recovery_restart;
	save();
};
nailink_completed = function() {
	let proof = journal?.nailink_completion;
	if (proof?.schema != 1 || !proof.barrier) return false;
	try {
		let runtime = nailink_runtime_state(true);
		if (runtime?.running !== true || runtime?.modified !== false) return false;
		let health = graphql('', null, true);
		if (health?.ready !== true || health.configured !== true || health.listening !== true ||
			health.initialized !== true || health.dns_ready !== true || health.routing_ready !== true) return false;
		if(health.core!=null && health.core!=runtime.core)return false;
		if(proof.legacy_start && runtime.legacy_running!==true)return false;
		let dns = selected_dns();
		if (dns.id != proof.dns.id || dns.dns.string != proof.dns.text) return false;
		if (proof.traffic) { let traffic=selected_traffic(); if (traffic.id != proof.traffic.id || traffic.text != proof.traffic.text) return false; }
		for (let file in journal.files) {
			if (!file_match(file.path,file.after,file.kind)) return false;
		}
		if (!proof.input || !same(proof.input,rules_signature('nailink'))) return false;
		// run() serializes configuration reloads and commits running versions only
		// after completion. A later native core switch does not invalidate that
		// version barrier. Recheck the live core, rather than pinning the old one.
		let stable=nailink_runtime_state(true);
		if(stable.running!==true || stable.modified!==false || stable.core!=runtime.core)return false;
		return true;
	} catch(e) { return false; }
};
function prepare_nailink_completion() {
	nailink_capture_foreign();
	if(!journal.dns){
		journal.nailink_completion=null;journal.nailink_run_identity=nailink_identity();
		journal.nailink_reload_unconfirmed=true;save();return;
	}
	let dns=selected_dns(), runtime=nailink_runtime_state(true);
	// Updating only the DAT file leaves modified=false for both the old and
	// new core. The native updateDns API increments the profile version even
	// for identical text. Establish a version barrier before submitting run.
	if (journal.stage=='restarting' && runtime?.running === true && runtime.modified === false) {
		update_dns(dns.id,dns.dns.string);
		runtime=nailink_runtime_state(true);
	}
	let traffic=journal.traffic?selected_traffic():null;
	journal.nailink_completion={schema:1,forward:journal.stage=='restarting',
		foreign:nailink_foreign_before,
		core:runtime.core,
		legacy_start:runtime.legacy_running===false,
		barrier:runtime?.running===false || runtime.legacy_running===false || runtime?.running===true && runtime.modified===true,
		dns:{id:dns.id,text:dns.dns.string},traffic:traffic?{id:traffic.id,text:traffic.text}:null,
		input:rules_signature('nailink')};
	journal.nailink_run_identity=nailink_identity();
	journal.nailink_reload_unconfirmed=true; save(); // intent before dispatch
}
function settle_nailink() {
	if (!journal?.nailink_reload_unconfirmed || !journal.nailink_completion?.forward || !nailink_completed())
		die('NaiLink reload is still unconfirmed; no second mutation submitted; journal retained');
	journal.stage='configured_unverified';
	if(journal.nailink_completion.input)journal.rules_source=journal.nailink_completion.input;
	clear_nailink_pending();
	finish_guard('nailink');
	fs.unlink(state_dir+'/adapter.previous.json');providers.gc([journal]);
}
function restart(plugin) {
	progress.stage('restarting');
	if(plugin=='passwall'){
		// The native ChinaDNS-NG helper caches user lists independently of its
		// daemon. Drop only this list's cache, including on restore/stopped core.
		let dir=root+'/tmp/etc/passwall_tmp/user_rules';
		if(fs.lstat(dir)?.type=='link')die('Unsafe PassWall rule cache directory');
		for(let suffix in ['', '_ok', '_geo']){
			let path=dir+'/block_host'+suffix,stat=fs.lstat(path);
			if(stat&&(stat.type!='file'||!fs.unlink(path)))die('Cannot invalidate PassWall block-list cache');
		}
	}
	if (plugin == 'none') {
		if (journal?.split && restart_service('dnsmasq')!=0)die('国内 DNS 配置未能重新加载。');
		return;
	}
	if (plugin == 'nailink') {
		let runtime=nailink_runtime_state();
		if(runtime.running==null){progress.failure('nailink_transition');die(progress.messages.nailink_transition);}
		if(runtime.running === false){
			if(journal.stage=='restarting')die('NaiLink 内核在应用期间已停止。');
			return; // restoring profiles must not start a core the user stopped
		}
		prepare_nailink_completion();
		try { graphql('mutation{run(dry:false)}'); }
		catch(e) {
			if(!journal.nailink_reload_unconfirmed)die(e);
			progress.stage('confirming_proxy');
			let started=time();
			for(let attempt=0;journal.nailink_completion?.barrier && attempt<30 && time()-started<60;attempt++) {
				if (nailink_completed()) { clear_nailink_pending(); return; }
				if(root && getenv('ADZERO_NAILINK_API_FIXTURE'))break;
				sleep(2000);
			}
			progress.failure('nailink_gateway');
			die(e.message+'; completion pending, no second mutation submitted');
		}
		clear_nailink_pending(); return;
	}
	{
		let inventory=getenv('ADZERO_ADAPTER_INVENTORY');
		let data=inventory?json(fs.readfile(inventory)):json(capture(['/bin/sh',root+'/usr/libexec/adzero/probe']));
		let state=filter(data.plugins??[],p=>p.id==plugin)[0];
		if(state && !proxy.active(state) && (state.runtime=='stopped' || state.enabled===false)){
			if(journal.stage=='restarting')die('代理内核在应用期间已停止。');
			return; // restore persisted settings without starting a stopped proxy
		}
	}
	if (plugin == 'openclash') {
		// OpenClash passes /tmp/<selected basename>.yaml to its custom hook,
		// then moves it to /etc/openclash. Provider-only changes are absent
		// from its quick-start manifest: invalidate that generated cache on
		// apply AND restore, so neither can silently keep the previous rules.
		openclash_paths();
		fs.unlink(root+'/tmp/openclash.change');
		if(journal?.stage=='restarting')fs.unlink(state_dir+'/openclash-hook.json');
	}
	if (restart_service(plugin) != 0) die('Proxy restart failed');
	if(native_runtime[plugin]&&journal?.stage=='restarting'){
		let value=wait_native_dns();
		// Native PassWall returns zero when another start/restart holds its
		// lock. Permit one retry, then keep an operable pending state instead
		// of rolling back valid rules or restarting again every minute.
		if(native_needs_reload(value)){
			if(restart_service(plugin)!=0)die('Proxy restart failed');
			value=wait_native_dns();
		}
		record_dns_runtime(value);
		if(native_needs_reload(value)){journal.runtime_retry_after=time()+300;journal.runtime_retries=(journal.runtime_retries??0)+1;}
	}
	if (plugin == 'openclash' && journal?.stage == 'restarting') {
		let ready = false;
		for (let attempt = 0; attempt < 45; attempt++) {
			let raw=fs.readfile(state_dir+'/openclash-hook.json'),receipt;
			try { receipt=raw?json(raw):null; } catch(e) { receipt=null; }
			if(receipt?.generation==journal.generation){
				if(receipt.state=='failed')openclash_failure(receipt.reason);
				if(receipt.state=='ready'){ready=true;break;}
			}
			sleep(1000);
		}
		if (!ready) { progress.failure('openclash_confirmation');die('OpenClash did not confirm its generated DNS configuration'); }
		// User overwrite calls and native provider-path normalization run after
		// our hook. Confirm the final generated file, not only the hook receipt.
		confirm_openclash();
	}
	if (plugin == 'nikki' && journal?.stage == 'restarting') {
		let actual = yaml(root + '/etc/nikki/run/config.yaml', 'nikki');
		if (actual.dns?.enable != true || !actual['rule-providers']?.['adzero-dns'] ||
			!actual.dns?.['nameserver-policy']?.['rule-set:adzero-dns'] ||
			index(actual.dns?.['fake-ip-filter'] ?? [], journal.filter_rule) < 0)
			die('Nikki generated configuration did not retain the DNS adapter');
		if(journal.split && (actual.mode!='rule' || index(actual.rules??[],'RULE-SET,adzero-cn-domain,DIRECT')<0 || actual.dns?.nameserver?.[0]!='tcp://'+route.endpoint(dns_profile.foreign.active)+'#'+journal.split.target))die('Nikki 最终配置未保留分流策略。');
	}
}
function apply_plugin(plugin, list) {
	if(plugin=='passwall2'){
		// Its native helper copies servers-file from the system dnsmasq. The
		// backend owns that file; do not inject PassWall v1 settings or hooks.
		return;
	}else if(plugin=='ssrplus'){
		let candidates=[];
		for(let file in ['black.list','white.list','netflix.list','gfw_list.conf','gfw_base.conf','oversea_list.conf']){
			let raw=fs.readfile(root+'/etc/ssrplus/'+file,5000001)??'';if(length(raw)>5000000)die('SSR Plus+ 域名列表超过安全限制。');
			for(let line in split(raw,'\n')){
				line=trim(line);let forward=match(line,/^server=\/([a-z0-9.-]+)\//);if(forward)line=forward[1];
				if(match(line,/^[a-z0-9][a-z0-9.-]*[a-z0-9]$/))push(candidates,line);
			}
		}
		list=route.expand_blockers(list,candidates);
		let path=root+'/etc/ssrplus/deny.list',base=lib.strip_block(fs.readfile(path)??'');
		tracked(path,base+(substr(base,-1)=='\n'||!length(base)?'':'\n')+lib.block(join('\n',list)),'block',0644);
	} else if (plugin == 'passwall') {
		let section;
		uci.foreach('passwall', 'global', s => { if (!section) section = s['.name']; });
		if (!section) die('Missing PassWall global section');
		let path = root + '/etc/passwall/rules/block_host';
		{
			let candidates=[];
			// dnsmasq's more-specific server rule can override a blocked parent.
			// Add equivalent child blockers; retain the user's native lists.
			for(let file in ['/usr/share/passwall/rules/chnlist','/usr/share/passwall/rules/gfwlist','/etc/passwall/rules/direct_host','/etc/passwall/rules/proxy_host']){
				let raw=fs.readfile(root+file,5000001)??'';if(length(raw)>5000000)die('PassWall 域名列表超过安全限制。');
				for(let line in split(raw,'\n')){line=trim(line);if(match(line,/^[a-z0-9][a-z0-9.-]*[a-z0-9]$/))push(candidates,line);}
			}
			list=route.expand_blockers(list,candidates);
		}
		let base = lib.strip_block(fs.readfile(path) ?? '');
		tracked(path, base + (substr(base, -1) == '\n' || !length(base) ? '' : '\n') + lib.block(join('\n', list)), 'block', 0644);
		setting('passwall', section, 'use_block_list', '1');
	} else if (plugin == 'openclash' || plugin == 'nikki') {
		if(plugin=='openclash')openclash_paths();
		let provider = plugin == 'nikki' ? '/etc/nikki/run/providers/rule/adzero.txt' : '/etc/openclash/rule_provider/adzero.txt';
		tracked(root + provider, join('\n', map(list, s => '+.' + s)) + '\n', 'owned', 0644);
		if (plugin == 'openclash') {
			let path = root + '/etc/openclash/custom/openclash_custom_overwrite.sh';
			let text = lib.strip_block(fs.readfile(path) ?? '#!/bin/sh\n');
			let pos = index(text, '\n') + 1;
			if (substr(text, 0, 2) != '#!') pos = 0;
			let hook = lib.block('/usr/libexec/adzero/adapter hook-openclash "$1" || exit 1');
			tracked(path, substr(text, 0, pos) + hook + substr(text, pos), 'block', 0755);
		} else {
			if (uci.get('nikki','config','core_only') == '1') die('Nikki core_only bypasses native mixins');
			let path = root + '/etc/nikki/mixin.yaml', config = yaml(path, 'nikki');
			if (uci.get('nikki','mixin','mixin_file_content') != '1' && length(keys(config))) die('Enabling an existing disabled Nikki mixin needs manual review');
			let effective = yaml(root + '/etc/nikki/run/config.yaml', 'nikki');
			config.dns ??= {};
			config.dns['fake-ip-filter-mode'] = uci.get('nikki','mixin','fake_ip_filter_mode') ?? config.dns['fake-ip-filter-mode'] ?? effective.dns?.['fake-ip-filter-mode'] ?? 'blacklist';
			config.dns['fake-ip-filter'] ??= effective.dns?.['fake-ip-filter'] ?? [];
			if (!length(journal.files) || !filter(journal.files, f => f.path == path)[0]) {
				if (config['rule-providers']?.['adzero-dns'] || config.dns?.['nameserver-policy']?.['rule-set:adzero-dns']) die('Reserved Mihomo names already exist');
			}
			// Nikki prepends nikki-rules; setting .rules would replace subscription rules.
			let original_rules = config.rules;
			config.rules = config['nikki-rules'] ?? [];
			config = lib.mihomo(config, provider, false);
			journal.filter_rule = config.dns['fake-ip-filter'][0];
			config['nikki-rules'] = config.rules;
			if (original_rules == null) delete config.rules; else config.rules = original_rules;
			if(split_enabled)pending_nikki={path,config,effective};
			else tracked(path, sprintf('%J', config) + '\n', 'nikki', 0600);
			setting('nikki', 'mixin', 'mixin_file_content', '1');
			// UCI-generated mixins are merged last and can replace YAML filter arrays.
			if (!split_enabled && uci.get('nikki','mixin','fake_ip_filter') == '1') {
				let filters = uci.get('nikki','mixin','fake_ip_filters') ?? [];
				if (type(filters) != 'array') filters = [filters];
				let own = config.dns['fake-ip-filter'][0];
				setting('nikki','mixin','fake_ip_filters',[own, ...filter(filters, s => s != own)]);
			}
		}
	} else if (plugin == 'nailink') {
		tracked(root + '/etc/daed/adzero-geosite.dat', lib.geosite(list), 'owned', 0644);
		let current = selected_dns();
		if (journal.dns && (current.id != journal.dns.id || current.dns.string != journal.dns.after)) die('NaiLink DNS profile changed externally');
		let after = lib.dae_dns(current.dns.string);
		journal.dns ??= {id:current.id, before:current.dns.string};
		if (after != current.dns.string) { journal.dns.after = after; save(); }
		if (selected_dns().dns.string != current.dns.string) die('NaiLink DNS changed while adapting');
		if (after != current.dns.string) { update_dns(current.id, after); changed = true; }
	}
}
function country_cached(plugin) {
	if(!same(journal?.country_sources,route.sources))return false;
	let suffixes=plugin=='nailink'?['adzero-cn-geosite.dat','adzero-cn-geoip.dat',...(pool_needed(plugin)?['adzero-cn-domains.txt']:[])]:['adzero-cn-domains.txt','adzero-cn-ips.txt'];
	for(let suffix in suffixes){
		let entries=filter(journal.files,f=>substr(f.path,-length(suffix)-1)=='/'+suffix);
		if(length(entries)!=1||entries[0].kind!='owned'||!providers.matches(entries[0].path,entries[0].after))return false;
	}
	return true;
}
function country_files(plugin) {
	let prefix=plugin=='nailink'?'/etc/daed/':plugin=='nikki'?'/etc/nikki/run/providers/rule/':'/etc/openclash/rule_provider/';
	if(country_cached(plugin))return {domains:prefix+'adzero-cn-domains.txt',ips:prefix+'adzero-cn-ips.txt'};
	if(plugin=='nailink'){
		tracked(root+prefix+'adzero-cn-geosite.dat',lib.geosite(routing_data.domains),'owned',0644);
		tracked(root+prefix+'adzero-cn-geoip.dat',route.geoip(routing_data.cidrs),'owned',0644);
		if(pool_needed(plugin))tracked(root+prefix+'adzero-cn-domains.txt',join('\n',routing_data.domains)+'\n','owned',0644);
	}else{
		tracked(root+prefix+'adzero-cn-domains.txt',join('\n',map(routing_data.domains,d=>'+.'+d))+'\n','owned',0644);
		tracked(root+prefix+'adzero-cn-ips.txt',join('\n',routing_data.cidrs)+'\n','owned',0644);
	}
	journal.country_sources=route.sources;save();
	return {domains:prefix+'adzero-cn-domains.txt',ips:prefix+'adzero-cn-ips.txt'};
}
passwall_engine=function(){
	let section;uci.foreach('passwall','global',s=>{section??=s['.name'];});
	let current=uci.get('passwall',section,'dns_shunt');
	let owned=filter(journal?.flags??[],f=>f.config=='passwall'&&f.section==section&&f.option=='dns_shunt')[0];
	return (owned&&same(current,owned.after)?owned.before:current)??'dnsmasq';
};
function restore_passwall_option(section,option){
	let owned=filter(journal?.flags??[],f=>f.config=='passwall'&&f.section==section&&f.option==option)[0];
	if(owned&&same(uci.get('passwall',section,option),owned.after))setting('passwall',section,option,owned.before);
}
function apply_split(plugin) {
	if(!split_enabled)return;
	if(plugin=='passwall2'){journal.split={mode:'native',traffic_verified:false};return;}
	journal.split ??= {mode:plugin=='none'?'direct':'proxy',target:null,traffic_verified:false};
	if(plugin=='none'){
		let instances=[];uci.foreach('dhcp','dnsmasq',s=>push(instances,s));
		if(length(instances)!=1 || instances[0].port=='0' || index(['dnsmasq.servers','dnsmasq.addnhosts'],uci.get('adblock-fast','config','dns'))<0)die('无代理自动模式需要一个启用的 dnsmasq 实例。');
		let s=instances[0],servers=s.server??[];if(type(servers)!='array')servers=[servers];
		setting('dhcp',s['.name'],'server',[...route.addresses(dns_profile,'domestic'),...filter(servers,v=>substr(v,0,1)=='/')]);
		if(dns_profile.domestic.mode=='parallel')setting('dhcp',s['.name'],'allservers','1');
		setting('dhcp',s['.name'],'noresolv','1');
		return;
	}
	if(plugin=='nailink'){
		country_files(plugin);
		prepare_pool(plugin);
		let current=selected_dns();
		if(current.id!=journal.dns.id || current.dns.string!=journal.dns.after)die('NaiLink DNS 已变化，拒绝覆盖。');
		let after=route.dae_dns(current.dns.string,dns_profile);
		if(after!=current.dns.string){journal.dns.after=after;save();update_dns(current.id,after);changed=true;}
		let traffic=selected_traffic();
		if(journal.traffic && (traffic.id!=journal.traffic.id || traffic.text!=journal.traffic.after))die('NaiLink 流量路由已被外部修改。');
		after=route.dae_routing(traffic.text,traffic.target,dns_profile);
		journal.traffic ??= {id:traffic.id,before:traffic.text,after:traffic.text};
		journal.split.target=traffic.target;
		if(after!=traffic.text){journal.traffic.after=after;save();if(selected_traffic().text!=traffic.text)die('NaiLink 流量路由存在并发修改。');update_traffic(traffic.id,after);changed=true;}
		return;
	}
	if(plugin=='passwall'){
		let section;uci.foreach('passwall','global',s=>{section??=s['.name'];});
		let engine=passwall_engine();
		let app=fs.readfile(root+'/usr/share/passwall/app.sh')??'',node=uci.get('passwall',section,'node');
		if(!node || !uci.get('passwall',node) || index(app,'direct_dns_mode')<0 || index(app,'USE_DEFAULT_DNS')<0)die('当前 PassWall 版本或默认节点不支持自动适配。');
		for(let name in ['chnlist','chnroute'])if(!length(trim(fs.readfile(root+'/usr/share/passwall/rules/'+name)??'')))die('PassWall 国内分类库未就绪。');
		let mode=uci.get('passwall',section,'dns_mode')??'tcp';
		if(index(['xray','sing-box','dns2socks','tcp'],mode)<0)die('当前 PassWall DNS 模式需先确认代理传输能力。');
		if(mode=='tcp'&&!fs.access(root+'/usr/bin/chinadns-ng','x'))die('当前 PassWall 缺少其原生 TCP DNS 转发组件。');
		// Retire the old forced engine only while its recorded value is still
		// ours. A later manual engine choice remains untouched.
		restore_passwall_option(section,'dns_shunt');
		if(engine=='dnsmasq')setting('passwall',section,'use_default_dns','remote');
		else restore_passwall_option(section,'use_default_dns');
		prepare_pool(plugin);
		for(let entry in [['chn_list','direct'],['direct_dns_mode',dns_profile.domestic.mode=='parallel'?'tcp':'udp'],['direct_dns',dns_profile.domestic.mode=='parallel'?'127.0.0.1:5356':route.endpoint(dns_profile.domestic.active)],['remote_dns',passwall_pool_foreign()?'127.0.0.1:5357':route.endpoint(dns_profile.foreign.active)],['v2ray_dns_mode','tcp']])setting('passwall',section,entry[0],entry[1]);
		if(dns_profile.strict){setting('passwall',section,'use_direct_list','0');setting('passwall',section,'chinadns_ng_default_tag','gfw');}
		else for(let name in ['use_direct_list','chinadns_ng_default_tag'])restore_passwall_option(section,name);
		journal.split.target='current-node';return;
	}
	let files=country_files(plugin);
	if(plugin=='nikki'){
		let path=pending_nikki.path,config=pending_nikki.config,effective=pending_nikki.effective;
		// Capture effective DNS arrays before disabling later UCI array overrides.
		let own_dns=config.dns??{},own_groups=config['proxy-groups'];
		config.dns={...(effective.dns??{}),...own_dns};
		config.dns['nameserver-policy']={...(effective.dns?.['nameserver-policy']??{}),...(own_dns['nameserver-policy']??{})};
		config['proxy-groups']??=effective['proxy-groups'];
		let own=config['nikki-rules']??[],original=config.rules;
		config.rules=effective.rules??[];
		let result=route.mihomo(config,files.domains,files.ips,dns_profile);config=result.config;journal.split.target=result.target;
		// Only prepend owned additions. The subscription's original rules remain.
		config['nikki-rules']=[...own,...filter(config.rules,r=>index(effective.rules??[],r)<0 && index(own,r)<0)];
		if(original==null)delete config.rules;else config.rules=original;
		if(own_groups==null)delete config['proxy-groups'];
		tracked(path,sprintf('%J',config)+'\n','nikki',0600);
		for(let key in ['dns_nameserver','dns_nameserver_policy'])if(uci.get('nikki','mixin',key)=='1')setting('nikki','mixin',key,'0');
		if(uci.get('nikki','mixin','mode')!=null)setting('nikki','mixin','mode','rule');
		if(uci.get('nikki','mixin','fake_ip_filter')=='1')setting('nikki','mixin','fake_ip_filters',config.dns['fake-ip-filter']);
	}
}
function restore() {
	if(fs.lstat(state_dir+'/dns-guard.json'))guard('hold');
	if (!journal) {if(action=='remove'){guard('cleanup');stop_pool();}return;}
	let runtime;
	if(journal.plugin=='nailink'){
		runtime=nailink_runtime_state(true);
		let started=monotime();
		while(runtime.running==null && action=='remove' && index(['starting','switching','reloading','recovering'],runtime.phase)>=0 && monotime()-started<300){
			progress.stage('waiting_proxy');
			if(root && getenv('ADZERO_NAILINK_API_FIXTURE'))break;
			sleep(1000);runtime=nailink_runtime_state(true);
		}
		if(runtime.running==null){progress.failure('nailink_transition');die(progress.messages.nailink_transition);}
		if(!journal.nailink_reload_unconfirmed&&runtime.modified===true && (!journal.nailink_foreign_baseline||!same(journal.nailink_foreign_baseline,nailink_foreign_revision()))){
			progress.failure('nailink_external_edit');die('NaiLink 有尚未应用的外部修改；请先在 NaiLink 应用或撤销，关闭请求已保留。');
		}
	}
	confirm_nailink_recovery();
	let traffic = journal.traffic ? restore_traffic_plan(runtime) : null;
	delete journal.recovery_reason;
	progress.stage('restoring');
	journal.stage = 'restoring'; save();
	let profile_changed=false;
	// Restore configuration first, restart, then remove provider files.
	for (let file in journal.files) {
		if (file.kind == 'owned') continue;
		let current = fs.readfile(file.path), restored = file_read(file.before,file.kind);
		if (!file_match(file.path,file.after,file.kind) && !file_match(file.path,file.before,file.kind) && !file_match(file.path,file.previous_after,file.kind)) {
			if (file.kind == 'block') restored = lib.strip_block(current ?? '');
			else die('Mixin was edited externally; recovery journal retained for review');
		}
		if (restored == null) fs.unlink(file.path); else atomic(file.path, restored, file.mode);
	}
	for (let flag in journal.flags) {
		if(length(uci.changes(flag.config)?.[flag.config]??[]))die('恢复配置有未提交的外部修改。');
		let current = uci.get(flag.config, flag.section, flag.option);
		if (!same(current, flag.after)) continue; // retain a later manual choice
		if (flag.before == null) uci.delete(flag.config, flag.section, flag.option);
		else uci.set(flag.config, flag.section, flag.option, flag.before);
		if (!uci.commit(flag.config)) die('Cannot restore adapter flag');
		uci.unload(flag.config);uci.load(flag.config);
	}
	if (journal.dns) {
		let current = selected_dns();
		if (current.id != journal.dns.id) die('NaiLink selected profile changed; recovery required');
		if((journal.split||journal.alias) && current.dns.string!=journal.dns.after && current.dns.string!=journal.dns.before&&current.dns.string!=journal.dns.previous_after)die('NaiLink DNS 已修改，分流恢复记录保留。');
		let restored = current.dns.string == journal.dns.after||current.dns.string==journal.dns.previous_after ? journal.dns.before : lib.strip_block(current.dns.string);
		if (current.dns.string != restored) { update_dns(current.id, restored);profile_changed=true; }
	}
	if (traffic && traffic.text != journal.traffic.before) {
		update_traffic(traffic.id, journal.traffic.before, traffic.text);
		profile_changed = true;
	}
	let original_loaded=false;
	if(journal.plugin=='nailink'&&!profile_changed){
		let runtime=nailink_runtime_state(true);
		if(runtime.running===false)original_loaded=true;
		else if(runtime.modified===false){let health=graphql('',null,true);original_loaded=health.ready===true&&health.configured===true&&health.dns_ready===true&&health.routing_ready===true;}
	}
	if(!original_loaded)restart(journal.plugin);
	if(journal.alias)native_bridge('cleanup',journal.alias);
	retire_alias();
	for (let file in journal.files) if (file.kind == 'owned') {
		let current = fs.readfile(file.path);
		if (current != null && !providers.matches(file.path,file.after) && !providers.matches(file.path,file.previous_after)) die('Provider edited externally; journal retained');
		if (current != null && !fs.unlink(file.path)) die('Cannot remove owned provider');
	}
	if (!fs.unlink(journal_path)) die('Cannot clear adapter journal');
	fs.unlink(state_dir + '/adapter.previous.json');providers.gc([journal]);
	journal = null;
	providers.gc([]);
	if(action=='remove'){
		guard('cleanup');
		stop_pool();
	}
}
function rollback_update(previous) {
	confirm_nailink_recovery();
	progress.stage('restoring');
	let old = json(previous);
	journal.stage='rolling_back';
	for (let file in journal.files) {
		let saved = filter(old.files, f => f.path == file.path)[0];
		let value = saved ? file_read(saved.after,saved.kind) : file_read(file.before,file.kind);
		let current = fs.readfile(file.path);
		if (current != value && !file_match(file.path,file.after,file.kind) && !file_match(file.path,file.previous_after,file.kind)) die('Concurrent edit prevents update rollback');
		if (value == null) fs.unlink(file.path); else atomic(file.path, value, file.mode);
	}
	for (let flag in journal.flags) {
		let saved = filter(old.flags, f => f.config == flag.config && f.section == flag.section && f.option == flag.option)[0];
		let value = saved ? saved.after : flag.before;
		let current = uci.get(flag.config, flag.section, flag.option);
		if (!same(current, value) && !same(current, flag.after)) die('Concurrent UCI change prevents rollback');
		if (value == null) uci.delete(flag.config, flag.section, flag.option); else uci.set(flag.config, flag.section, flag.option, value);
		if (!uci.commit(flag.config)) die('Cannot restore update UCI');
	}
	if (journal.dns) {
		let current = selected_dns(), value = old.dns?.after ?? journal.dns.before;
		if (current.id != journal.dns.id || (current.dns.string != journal.dns.after && current.dns.string != value&&current.dns.string!=journal.dns.previous_after)) die('Concurrent NaiLink DNS change prevents rollback');
		if (current.dns.string != value) update_dns(current.id, value);
	}
	if(journal.traffic){let current=selected_traffic(),value=old.traffic?.after??journal.traffic.before;if(current.id!=journal.traffic.id || current.text!=journal.traffic.after && current.text!=value&&current.text!=journal.traffic.previous_after)die('Concurrent routing change prevents rollback');if(current.text!=value)update_traffic(current.id,value);}
	restart(journal.plugin);
	if(journal.alias&&!old.alias)native_bridge('cleanup',journal.alias);
	if(old.alias)native_bridge('install',old.alias);
	atomic(journal_path, previous);
	fs.unlink(state_dir + '/adapter.previous.json');
	journal = old;
	providers.gc([journal]);
}

function select_dns_candidate(path,wanted) {
	let stat=path?fs.lstat(path):null;
	if(stat?.type!='file'||stat.size>100000)die('DNS 预检计划无效。');
	let p=json(fs.readfile(path));if(type(p?.routing_enabled)!='bool')die('DNS 预检开关无效。');
	if(wanted!=null&&(p.routing_enabled?'1':'0')!=wanted)die('分流预检目标与待应用计划不一致。');
	split_enabled=p.routing_enabled;dns_profile=route.profile(p.dns??dns_profile);
}
function routing_check(wanted) {
	if(index(['0','1'],wanted)<0)die('自动分流目标无效。');
	if(wanted=='0'&&dns_profile.strict)die('请同时关闭严格 DNS 模式，再关闭自动分流。');
	let plugin=active_plugin();
	if(plugin=='ssrplus'&&wanted=='1')die('SSR Plus+ 当前仅支持广告过滤，请使用其原生分流设置。');
	if(plugin=='passwall'&&journal?.plugin=='passwall'&&journal.stage=='configured_unverified'){
		if(wanted=='1'&&index(['dnsmasq','chinadns-ng'],passwall_engine())<0)die('当前 PassWall DNS 引擎不支持 AdZero 自动分流，原配置保持不变。');
		for(let flag in journal.flags)if(!same(uci.get(flag.config,flag.section,flag.option),flag.after))die('PassWall 设置已被外部修改，请先检查恢复记录。');
		return 'passwall';
	}
	if(plugin!='nailink'||journal?.plugin!='nailink'||journal.stage!='configured_unverified')return 'full';
	if(journal.alias||alias_wanted())return 'full';
	let dns=selected_dns();
	if(dns.id!=journal.dns?.id||dns.dns.string!=journal.dns.after)die('NaiLink DNS profile changed externally');
	// Check syntax and selected target before any restore, download or reload.
	if(wanted=='1'){
		route.dae_dns(lib.dae_dns(journal.dns.before),dns_profile);
		let traffic=selected_traffic(true);
		if(journal.traffic&&(traffic.id!=journal.traffic.id||traffic.text!=journal.traffic.after))die('NaiLink 流量路由已被外部修改。');
		route.dae_routing(journal.traffic?.before??traffic.text,traffic.target,dns_profile);
	}
	return 'nailink';
}
rules_signature = function(plugin) {
	let mode=uci.get('adblock-fast','config','dns');
	let path=mode=='unbound.adb_list'?'/var/lib/unbound/adb_list.adblock-fast':'/var/run/adblock-fast/'+mode;
	let sha256=rulecache.fingerprint(root+path);if(!sha256)return null;
	let configs=['adzero','adblock-fast',plugin=='nailink'?'daed':plugin=='none'?'dhcp':plugin=='ssrplus'?'shadowsocksr':plugin];
	let dependencies={};
	let paths=map(filter(configs,c=>c!='adblock-fast'),c=>'/etc/config/'+c);
	if(plugin=='passwall')paths=[...paths,'/usr/share/passwall/app.sh','/usr/share/passwall/rules/chnlist','/usr/share/passwall/rules/chnroute','/usr/share/passwall/rules/gfwlist','/etc/passwall/rules/direct_host','/etc/passwall/rules/proxy_host'];
	if(plugin=='passwall2')paths=[...paths,'/usr/share/passwall2/app.sh','/usr/share/passwall2/helper_dnsmasq.lua'];
	if(plugin=='ssrplus')paths=[...paths,'/usr/share/shadowsocksr/gfw2ipset.sh',...map(['black.list','white.list','netflix.list','gfw_list.conf','gfw_base.conf','oversea_list.conf'],f=>'/etc/ssrplus/'+f)];
	if(plugin=='nikki')push(paths,'/etc/nikki/run/config.yaml');
	for(let c in configs)for(let delta in uci.changes(c)?.[c]??[]){
		if(c=='adblock-fast'&&delta[0]=='set'&&delta[2]=='size'&&match(''+delta[3],/^[0-9]+$/)&&uci.get(c,delta[1])=='file_url')continue;
		return null;
	}
	// Names, validation timestamps/counts and native download sizes do not
	// alter the rules. The exact processed output hash above still gates reuse.
	let backend_options=[];
	uci.foreach('adblock-fast',null,s=>{
		if(s['.type']=='file_url'&&s.enabled=='0')return;
		let values={};for(let k in sort(keys(s)))if(substr(k,0,1)!='.'&&!(s['.type']=='file_url'&&(k=='name'||k=='size'||substr(k,0,7)=='adzero_')))values[k]=s[k];
		push(backend_options,{id:s['.type']=='file_url'?null:s['.name'],type:s['.type'],values});
	});
	dependencies['/etc/config/adblock-fast']=backend_options;
	for(let name in paths){
		let stat=fs.lstat(root+name);
		if(!stat){dependencies[name]='missing';continue;}
		if(stat.type!='file')return null;
		if(name=='/etc/config/adzero'){
			let options=[];
			uci.foreach('adzero',null,s=>{
				let values={};for(let k in sort(keys(s)))if(substr(k,0,1)!='.'&&k!='dns_profile')values[k]=s[k];
				if(s['.name']=='main'&&split_enabled)values.dns_profile=route.effective(dns_profile);
				push(options,{id:s['.name'],type:s['.type'],values});
			});
			dependencies[name]=options;continue;
		}
		if(!stat.size){dependencies[name]='empty';continue;}
		let hash=rulecache.fingerprint(root+name);if(!hash)return null;dependencies[name]=hash;
	}
	return {schema:5,mode,sha256,dependencies};
};
function reconfigure_nailink(with_rules) {
	if(routing_check(split_enabled?'1':'0')!='nailink')die('快速切换仅适用于当前 NaiLink 接入。');
	guard('hold');
	if(split_enabled&&!country_cached('nailink'))routing_data=json(capture(['/bin/sh',getenv('ADZERO_ROUTING_DATA_PROGRAM')??'/usr/libexec/adzero/routing-data','prepare']));
	let previous=sprintf('%J',journal);transaction_previous=previous;
	try{
		progress.stage('adapting');
		if(with_rules){let list=rules();tracked(root+'/etc/daed/adzero-geosite.dat',lib.geosite(list),'owned',0644);journal.count=length(list);}
		// Retire a bridge only after the old native route is restored/reloaded.
		// Full apply composes the same transaction for alias-mode changes.
		if(journal.alias||alias_wanted())die('NaiLink 别名入口变化需要统一应用。');
		let dns=selected_dns(),after=lib.dae_dns(journal.dns.before),traffic=null,next=null;
		if(split_enabled){
			country_files('nailink');prepare_pool('nailink');after=route.dae_dns(after,dns_profile);traffic=selected_traffic();
			journal.traffic??={id:traffic.id,before:traffic.text,after:traffic.text};
			next=route.dae_routing(journal.traffic.before,traffic.target,dns_profile);
			journal.split={mode:'proxy',target:traffic.target,traffic_verified:false};
		}else{
			delete journal.split;
			if(journal.traffic){traffic=selected_traffic();next=journal.traffic.before;}
		}
		if(traffic&&(traffic.id!=journal.traffic.id||traffic.text!=journal.traffic.after))die('NaiLink 流量路由存在并发修改。');
		if(selected_dns().dns.string!=dns.dns.string)die('NaiLink DNS 存在并发修改。');
		journal.dns.after=after;if(traffic)journal.traffic.after=next;
		journal.stage='preparing';save(); // one durable intent covers both profiles
		if(dns_profile.strict)guard('pin',['nailink',strict_target('nailink')]);
		if(dns.dns.string!=after)update_dns(dns.id,after);
		if(traffic&&traffic.text!=next)update_traffic(traffic.id,next);
		journal.stage='restarting';save();restart('nailink');
		journal.stage='configured_unverified';
		let source=rules_signature('nailink');
		if(source&&(with_rules||source.sha256==journal.rules_source?.sha256))journal.rules_source=source;else delete journal.rules_source;
		save();finish_guard('nailink');fs.unlink(state_dir+'/adapter.previous.json');providers.gc([journal]);
	}catch(e){
		if(journal?.nailink_reload_unconfirmed)die(e);
		progress.failure('adapter');
		try{rollback_update(previous);}catch(recovery){journal.stage='recovery_required';save();die('快速切换恢复未完成，请保留恢复记录。');}
		die(e);
	}
}

function reconfigure_passwall(with_rules){
	if(routing_check(split_enabled?'1':'0')!='passwall')die('当前不是可直接更新的 PassWall 接入。');
	guard('hold');
	let previous=sprintf('%J',journal);transaction_previous=previous;
	try{
		progress.stage('adapting');
		if(with_rules){let list=rules();apply_plugin('passwall',list);journal.count=length(list);journal.runtime_samples=uniq([list[0],list[int(length(list)/2)],list[length(list)-1]]);}
		if(split_enabled)apply_split('passwall');
		else{
			let section;uci.foreach('passwall','global',s=>{section??=s['.name'];});
			for(let name in ['dns_shunt','chn_list','use_default_dns','direct_dns_mode','direct_dns','remote_dns','v2ray_dns_mode','use_direct_list','chinadns_ng_default_tag'])restore_passwall_option(section,name);
			delete journal.split;
		}
		let section;uci.foreach('passwall','global',s=>{section??=s['.name'];});
		journal.dns_engine=uci.get('passwall',section,'dns_shunt')??'dnsmasq';
		journal.runtime_supported=index(['dnsmasq','chinadns-ng'],journal.dns_engine)>=0&&(uci.get('passwall',section,'dns_redirect')??'1')=='1';
		if(dns_profile.strict)guard('pin',['passwall',strict_target('passwall')]);
		journal.stage='restarting';save();restart('passwall');
		journal.stage='configured_unverified';journal.rules_source=rules_signature('passwall');save();
		finish_guard('passwall');
		fs.unlink(state_dir+'/adapter.previous.json');providers.gc([journal]);
	}catch(e){
		progress.failure('adapter');
		try{rollback_update(previous);}catch(recovery){journal.stage='recovery_required';save();die('PassWall 更新恢复未完成，请保留恢复记录。');}
		die(e);
	}
}

function unchanged(plugin,signature) {
	if(!journal||journal.stage!='configured_unverified'||journal.plugin!=plugin||!signature||!same(journal.rules_source,signature)||!!journal.split!=split_enabled||selected_core&&journal.core!=selected_core)return false;
	if(split_enabled&&index(['nailink','nikki','openclash'],plugin)>=0&&!country_cached(plugin))return false;
	for(let f in journal.files)if(!file_match(f.path,f.after,f.kind))return false;
	for(let f in journal.flags)if(length(uci.changes(f.config)?.[f.config]??[])||!same(uci.get(f.config,f.section,f.option),f.after))return false;
	if(journal.dns){let current=selected_dns();if(current.id!=journal.dns.id||current.dns.string!=journal.dns.after)return false;}
	if(journal.traffic){let current=selected_traffic();if(current.id!=journal.traffic.id||current.text!=journal.traffic.after)return false;}
	return true;
}

guard=function(action,target){
	if(action!='socks'&&!dns_profile.strict&&!fs.lstat(state_dir+'/dns-guard.json'))return;
	let program=getenv('ADZERO_GUARD_PROGRAM')??root+'/usr/libexec/adzero/dns-guard';
	let argv=['env','ADZERO_GUARD_ROOT='+root,program,action];if(target)argv=[...argv,...target];
	if(system(argv)!=0){if(action=='socks')progress.failure('dns_socks_unavailable');die('DNS 路径未确认，原配置或恢复记录已保留。');}
};
finish_guard=function(plugin){
	prepare_pool(plugin);
	if(dns_profile.strict){
		let target=strict_target(plugin);guard('pin',[plugin,target]);
		guard('ready');
	}else guard('cleanup');
	if(!pool_needed(plugin))stop_pool();
};
stop_pool=function(){
	let marker=state_dir+'/dns-pool-needed';if(fs.lstat(marker)){
		if(fs.lstat(marker)?.type!='file'||fs.readfile(marker)!='AdZero DNS pool v1\n')die('DNS 并发所有权标记无效，未清理。');
		if(!fs.unlink(marker))die('DNS 并发退出记录未确认。');
	}
	if(fs.access(root+'/etc/init.d/adzero-dns','x'))system(service_command('adzero-dns','stop'));
};
passwall_pool_foreign=function(){return dns_profile.foreign.mode=='parallel'||dns_profile.strict&&(uci.get('passwall','@global[0]','dns_mode')??'tcp')=='tcp';};
pool_needed=function(plugin){return plugin=='nailink'&&(dns_profile.strict||dns_profile.domestic.mode=='parallel'||dns_profile.foreign.mode=='parallel')||plugin=='passwall'&&(dns_profile.domestic.mode=='parallel'||passwall_pool_foreign());};
pool_preflight=function(plugin){
	if(!pool_needed(plugin))return;
	let custom=getenv('ADZERO_POOL_CHECK_PROGRAM'),argv=custom?[custom]:['ucode','-S',root+'/usr/libexec/adzero/dns-pool.uc','check-profile'],value;
	try{value=json(capture([timeout_helper(),'-k','1','15',...argv,plugin,sprintf('%J',dns_profile),uci.get('passwall','@global[0]','dns_mode')??'tcp']));}catch(e){}
	if(value?.ready!==true){let reason=index(['dns_passwall_mode','dns_socks_required','dns_pool_domestic_failed','dns_pool_foreign_failed'],value?.reason)>=0?value.reason:'dns_pool_preflight_failed';progress.failure(reason);die(progress.messages[reason]);}
};
verify_pool=function(plugin){
	let value=poolstate.probe(dns_profile,plugin,uci.get('passwall','@global[0]','dns_mode')??'tcp');
	if(!value.ready){progress.failure(value.reason);die(progress.messages[value.reason]);}
};
prepare_pool=function(plugin){
	if(!pool_needed(plugin))return;
	function fail(reason){progress.failure(reason);die(progress.messages[reason]);}
	let marker=state_dir+'/dns-pool-needed';
	if(fs.lstat(marker)?.type=='link'||fs.lstat(marker)&&fs.readfile(marker)!='AdZero DNS pool v1\n')die('DNS 并发所有权标记冲突，未覆盖。');
	if(!fs.access(root+'/etc/init.d/adzero-dns','x'))fail('dns_pool_start_failed');
	if(!fs.lstat(marker))atomic(marker,'AdZero DNS pool v1\n',0600);
	if(system(service_command('adzero-dns','enable'))!=0)fail('dns_pool_start_failed');
	let expected=sprintf('%J',route.effective(dns_profile));
	if(poolstate.inspect(root,expected).ready){verify_pool(plugin);return;}
	let requested=monotime();
	if(system([timeout_helper(),'-k','3','15',...service_command('adzero-dns','start')])!=0)fail('dns_pool_start_failed');
	let value=poolstate.wait_ready(root,expected,requested);
	if(!value.ready)fail(value.reason);
	verify_pool(plugin);
};
strict_target=function(plugin){
	if(plugin=='nailink')return '127.0.0.1#5358';
	let port;
	if(plugin=='passwall'){let text=fs.readfile(root+'/tmp/etc/passwall/acl/default/dnsmasq.conf')??'';port=match(text,/(^|\n)port=([0-9]+)(\n|$)/)?.[2];}
	else{let cfg=plugin=='nikki'?yaml(root+'/etc/nikki/run/config.yaml','nikki'):yaml(root+openclash_paths().generated,'openclash');port=match(cfg.dns?.listen??'',/:([0-9]+)$/)?.[1];}
	if(!port||int(port)<1024||int(port)>65535)die('严格模式无法确认原生 DNS 监听端口，原网络保持不变。');
	return '127.0.0.1#'+port;
};
policy_prerequisites=function(plugin){
	if(plugin=='passwall2'&&(dns_profile.strict||action=='dns-check'&&(dns_profile.domestic.mode=='parallel'||dns_profile.foreign.mode=='parallel')))die('PassWall2 当前沿用原生 DNS 与分流，不支持 AdZero 独立并发或严格模式；原设置保持不变。');
	if(dns_profile.strict&&!split_enabled)die('严格 DNS 模式需要先应用自动分流。');
	if(!split_enabled)return;
	if(dns_profile.strict){
		if(plugin=='none'||plugin=='ssrplus')die('严格 DNS 模式需要唯一活动且受支持的代理；不会回落直连。');
		if(plugin=='nailink'&&!dns_profile.proxy_port){progress.failure('dns_socks_required');die('NaiLink 严格模式需要填写其本机无认证 SOCKS5 端口。');}
		if(plugin=='passwall'){
			let enabled=0,section;uci.foreach('passwall','global',s=>{section??=s['.name'];});
			for(let kind in ['acl_rule','acl_rules'])uci.foreach('passwall',kind,s=>{if(s.enabled=='1')enabled++;});
			if(enabled)die('PassWall 自定义 ACL 尚不支持严格 DNS，请保留原设置。');
			if((uci.get('passwall',section,'dns_redirect')??'1')!='1'||(uci.get('passwall',section,'localhost_proxy')??'1')!='1')die('PassWall 严格模式需要其默认 DNS 接管和路由器本机代理开启。');
		}
		guard('preflight');strict_target(plugin);
	}
	if(plugin=='passwall'&&dns_profile.foreign.mode=='parallel'){
		let section;uci.foreach('passwall','global',s=>{section??=s['.name'];});
		if((uci.get('passwall',section,'dns_mode')??'tcp')!='tcp'){progress.failure('dns_passwall_mode');die('PassWall 国外并发当前需要原生 TCP DNS 模式；不会更换现有 Xray／Sing-box 引擎。');}
	}
	if(plugin=='passwall'&&passwall_pool_foreign()){
		if(!dns_profile.proxy_port){progress.failure('dns_socks_required');die('PassWall 国外并发或严格 TCP DNS 需要填写其本机无认证 SOCKS5 端口，避免直连回落。');}
	}
	if(plugin=='nailink'&&dns_profile.foreign.mode=='parallel'&&!dns_profile.proxy_port){progress.failure('dns_socks_required');die('NaiLink 国外并发需要可用的本机无认证 SOCKS5 出口；透明代理端口不能代替。请保留国外“单一＋备用”。');}
	pool_preflight(plugin);
}

;

try {
	if(action=='dns-check'){
		select_dns_candidate(shift(ARGV));journal=read_journal();active_plugin();exit(0);
	}
	journal = read_journal();
	if(index(['apply','reconfigure-nailink','reconfigure-nailink-rules','reconfigure-passwall','reconfigure-passwall-rules'],action)>=0 && fs.lstat(state_dir+'/close-requested'))
		die('关闭请求已保存，跳过新的规则接入；请等待恢复完成。');
	if (action == 'status') {
		let protection;try{protection=json(fs.readfile(state_dir+'/dns-guard.json',16001));}catch(e){}
		print(sprintf('%J\n', {plugin:journal?.plugin ?? 'none', core:journal?.core??null, stage:journal?.stage ?? 'inactive', domains:journal?.count ?? 0,
			dns_policy:{managed:journal?.plugin!='passwall2',domestic:dns_profile.domestic.mode??'single',foreign:dns_profile.foreign.mode??'single',strict:!!dns_profile.strict,guard:protection?.state??'off',scope:'default_lan_ipv4_dns',doh:'device_policy_required',passwall_dns_mode:index(['tcp','xray','sing-box','dns2socks'],uci.get('passwall','@global[0]','dns_mode'))>=0?uci.get('passwall','@global[0]','dns_mode'):null},
			client_dns_verified:false, router_dns_verified:false,reload_pending:!!journal?.nailink_reload_unconfirmed,
			recovery_reason:journal?.recovery_reason == 'nailink_routing_conflict' ? journal.recovery_reason : null,
			runtime:native_runtime[journal?.plugin]?native_dns_state():null,
			runtime_retry_exhausted:(journal?.runtime_retries??0)>=3,
			routing:{enabled:split_enabled,mode:journal?.split?.mode??'inactive',traffic_verified:false}}));
	} else if (action == 'nailink-status') {
		let state = graphql('{general{dae{running modified}}}').general?.dae;
		if (type(state?.running) != 'bool') die('NaiLink returned invalid runtime state');
		print(sprintf('%J\n', {authorized:true,running:state.running,modified:type(state.modified) == 'bool' ? state.modified : null}));
	} else if (action == 'pending') { if(journal?.nailink_reload_unconfirmed){print('nailink\n');exit(0);}exit(1); }
	else if (action == 'settle') settle_nailink();
	else if (action == 'remove'||action=='detach') restore();
	else if(action=='routing-check'){
		let wanted=shift(ARGV),path=shift(ARGV);
		if(path!=null)select_dns_candidate(path,wanted);
		print(routing_check(wanted)+'\n');
	}
	else if(action=='reconfigure-nailink')reconfigure_nailink();
	else if(action=='reconfigure-nailink-rules')reconfigure_nailink(true);
	else if(action=='reconfigure-passwall')reconfigure_passwall();
	else if(action=='reconfigure-passwall-rules')reconfigure_passwall(true);
	else if (action == 'check') { print(active_plugin() + '\n'); }
	else if(action=='alias-context'){
		if(journal?.stage!='configured_unverified'||!alias_wanted())exit(1);
		if(journal.plugin=='nailink'&&journal.alias)print(sprintf('%J',journal.alias));
		else if(journal.plugin=='openclash')print(sprintf('%J',native.mihomo(yaml(root+openclash_paths().generated,'openclash'),alias_lan())));
		else exit(1);
	}
	else if (action == 'hook-openclash') {
		let path = shift(ARGV);
		openclash_error='target';
		if(!journal||journal.plugin!='openclash')die('Unexpected OpenClash hook target');
		let paths=openclash_paths();
		// Bind temporary-file access to the selected subscription; never allow
		// arbitrary /tmp paths, traversal or links to user configuration files.
		if(index([paths.temporary,paths.generated],path)<0||fs.lstat(root+path)?.type!='file')die('Unexpected OpenClash hook target');
		openclash_error='hook_failed';
		let config = yaml(root + path, 'openclash');
		if (config.dns?.enable != true) { openclash_error='dns_disabled';die(openclash_errors.dns_disabled); }
		if (config['rule-providers']?.['adzero-dns'] && config['rule-providers']['adzero-dns'].path != '/etc/openclash/rule_provider/adzero.txt') die('Reserved OpenClash rule provider already exists');
		config = lib.mihomo(config, '/etc/openclash/rule_provider/adzero.txt', false);
		if(journal.split){let result=route.mihomo(config,'/etc/openclash/rule_provider/adzero-cn-domains.txt','/etc/openclash/rule_provider/adzero-cn-ips.txt',dns_profile);config=result.config;}
		let core = root + '/etc/openclash/core/clash_meta';
		if (!fs.access(core, 'x')) { openclash_error='core_missing';die(openclash_errors.core_missing); }
		let temporary = root + path + '.adzero-check';
		atomic(temporary, sprintf('%J', config) + '\n');
		// Match OpenClash's procd environment for its UI and CA directories.
		// Override inherited SAFE_PATHS for this child only; retain core path checks.
		let command = join(' ', map(['env', 'SAFE_PATHS=' + root + '/usr/share/openclash:' + root + '/etc/ssl',
			core, '-t', '-d', root + '/etc/openclash', '-f', temporary], quote));
		let rc = system(command + ' >/dev/null 2>&1', 30000);
		if (rc != 0) { fs.unlink(temporary);openclash_error='core_rejected';die(openclash_errors.core_rejected); }
		if (!fs.rename(temporary, root + path)) die('Cannot commit generated OpenClash configuration');
		atomic(state_dir+'/openclash-hook.json',sprintf('%J',{generation:journal.generation,state:'ready'}));
	} else if (action == 'apply') {
		progress.stage('adapting');
		if (journal && journal.stage != 'configured_unverified') die('Adapter recovery is pending; disable protection first');
		let plugin = active_plugin(), signature=rules_signature(plugin);
		let runtime=native_runtime[plugin]&&journal?.plugin==plugin?native_dns_state():null;
		if(unchanged(plugin,signature)&&!(native_runtime[plugin]&&!journal.runtime_samples)){
			if(journal.alias)native_bridge('install',journal.alias);
			retire_alias();
			if(runtime)record_dns_runtime(runtime);
			if(!native_needs_reload(runtime)||((journal.runtime_retry_after??0)>time()||(journal.runtime_retries??0)>=3)&&getenv('ADZERO_ADAPTER_REFRESH')!='1'){
				finish_guard(plugin);fs.unlink(state_dir+'/adapter.previous.json');providers.gc([journal]);exit(0);
			}
		}
		let list = rules();
		guard('hold');
		if (journal && journal.plugin != plugin) restore();
		if (plugin == 'none' && !split_enabled) {finish_guard(plugin);exit(0);}
		if(split_enabled && plugin!='none' && plugin!='passwall'&&plugin!='passwall2'&&!country_cached(plugin))routing_data=json(capture(['/bin/sh',getenv('ADZERO_ROUTING_DATA_PROGRAM')??'/usr/libexec/adzero/routing-data','prepare']));
		let previous = journal ? sprintf('%J', journal) : null;
		transaction_previous = previous;
		journal ??= {schema:1, plugin, files:[], flags:[], stage:'preparing'};
		journal.core=selected_core;
		try {
			if(journal.split && !split_enabled)die('分流设置变化，请先关闭并恢复。');
			if(split_enabled)journal.split??={mode:plugin=='none'?'direct':'proxy',traffic_verified:false};
			apply_plugin(plugin, list);
			apply_split(plugin);
			apply_alias(plugin);
			if(dns_profile.strict)guard('pin',[plugin,strict_target(plugin)]);
			if(native_runtime[plugin]){
				let section;uci.foreach('passwall','global',s=>{section??=s['.name'];});
				journal.dns_engine=plugin=='passwall'?(uci.get('passwall',section,'dns_shunt')??'dnsmasq'):null;
				journal.runtime_supported=plugin=='ssrplus'||plugin=='passwall2'||index(['dnsmasq','chinadns-ng'],journal.dns_engine)>=0&&(uci.get('passwall',section,'dns_redirect')??'1')=='1';
				journal.runtime_samples=uniq([list[0],list[int(length(list)/2)],list[length(list)-1]]);
				runtime=native_dns_state();record_dns_runtime(runtime);
			}
			let signature_after=rules_signature(plugin);
			if(signature&&signature_after&&signature.sha256==signature_after.sha256&&signature.mode==signature_after.mode)journal.rules_source=signature_after;else delete journal.rules_source;
			if (!changed && journal.stage == 'configured_unverified' && !native_needs_reload(runtime)) { journal.count=length(list);save();finish_guard(plugin);fs.unlink(state_dir + '/adapter.previous.json');providers.gc([journal]); exit(0); }
			if(changed||getenv('ADZERO_ADAPTER_REFRESH')=='1')delete journal.runtime_retries;
			journal.count = length(list); journal.stage = 'restarting'; journal.generation = time(); save();
			restart(plugin);
			retire_alias();
			journal.stage = 'configured_unverified';
			signature_after=rules_signature(plugin);
			if(signature&&signature_after&&signature.sha256==signature_after.sha256&&signature.mode==signature_after.mode)journal.rules_source=signature_after;else delete journal.rules_source;
			save();
			finish_guard(plugin);
			fs.unlink(state_dir + '/adapter.previous.json');providers.gc([journal]);
		} catch (e) {
			if(journal?.nailink_reload_unconfirmed)die(e);
			if (previous == null) {
				try { restore(); } catch (recovery) { die(e.message + '; recovery: ' + recovery.message + '. Journal retained in /etc/adzero/adapter.json'); }
			} else {
				try { rollback_update(previous); } catch (recovery) { journal.stage = 'recovery_required'; save(); die('Update rollback incomplete; recovery journal retained'); }
			}
			die(e);
		}
	} else die('Usage: adapter {check|apply|remove|status|hook-openclash PATH}');
} catch (e) {
	if(action=='hook-openclash')progress.failure(openclash_error=='dns_disabled'?'openclash_dns':openclash_error=='core_missing'||openclash_error=='core_rejected'?'openclash_core':'openclash_hook');
	progress.failure('adapter');
	if(action=='hook-openclash'&&journal?.plugin=='openclash') {
		// The native init script suppresses hook stderr. Preserve only fixed
		// diagnostics, not subscription contents or core output, for the caller.
		atomic(state_dir+'/openclash-hook.json',sprintf('%J',{generation:journal.generation,state:'failed',reason:openclash_error}));
		journal.stage='recovery_required';save();
	}
	fs.stderr.write('AdZero DNS adapter: ' + e.message + '\n');
	exit(1);
}
