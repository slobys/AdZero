'use strict';
// Read-only inventory. No subscriptions, passwords, or full process arguments leave here.
import * as fs from 'fs';
import { cursor } from 'uci';
import { connect } from 'ubus';
import * as proxy from './proxy-state.uc';

const root = getenv('ADZERO_PROBE_ROOT') ?? '';
const uci = cursor(root + '/etc/config');
function release_file(path) {
	let data = {};
	for (let line in split(fs.readfile(root + path) ?? '', '\n')) {
		let m = match(line, /^([A-Z_]+)=(.*)\r?$/);
		if (!m) continue;
		let value = replace(m[2], /\r$/, '');
		if ((substr(value, 0, 1) == "'" && substr(value, -1) == "'") ||
			(substr(value, 0, 1) == '"' && substr(value, -1) == '"')) value = substr(value, 1, length(value)-2);
		data[m[1]] = value;
	}
	return data;
}
function quote(value) { return "'" + replace(value, /'/g, "'\\''") + "'"; }
function command(name, args) {
	let executable = getenv('ADZERO_' + uc(name)) ?? name;
	let proc = fs.popen(join(' ', map([executable, ...args], quote)) + ' 2>/dev/null', 'r');
	if (!proc) return '';
	let result = proc.read('all'), rc = proc.close();
	return rc == 0 ? trim(result) : '';
}
function has_command(name) {
	let executable = getenv('ADZERO_' + uc(name)) ?? name;
	if (index(executable, '/') >= 0) return fs.access(executable, 'x');
	for (let path in split(getenv('PATH') ?? '/usr/sbin:/usr/bin:/sbin:/bin', ':'))
		if (fs.access(path + '/' + executable, 'x')) return true;
	return false;
}
const processes = {};
for (let path in fs.glob(root + '/proc/[0-9]*/comm') ?? []) {
	let name = trim(fs.readfile(path) ?? '');
	if (index(['dae','daed','mihomo','clash','xray','sing-box','dnsmasq','smartdns','unbound','AdGuardHome'], name) >= 0) processes[name] = true;
}
let services = null;
const fixture = getenv('ADZERO_PROBE_SERVICE_FIXTURE');
try {
	if (fixture && root) services = json(fs.readfile(fixture));
	else {
		let bus = connect();
		if (bus) { services = bus.call('service', 'list', {}); bus.disconnect(); }
	}
} catch (e) { services = null; }
const services_checked=type(services)=='object';
if (type(services) != 'object') services = {};
const owned_cores=proxy.core_states(root);
function service_state(name, only) {
	let instances = services[name]?.instances, stopped = false;
	if (type(instances) != 'object') return only&&services_checked&&!services[name]?'stopped':'unknown';
	for (let id in keys(instances ?? {})) {
		if(only&&id!=only)continue;
		if (instances[id].running === true) return 'running';
		if (instances[id].running === false) stopped = true;
	}
	return stopped||only ? 'stopped' : 'unknown';
}
function section(config, type) {
	let first = null;
	uci.foreach(config, type, s => { first ??= s['.name']; });
	return first;
}
function value(config, name, option) { return name ? uci.get(config, name, option) : null; }
function ssrplus_client(){
	if(fs.stat(root+'/proc')?.type!='directory')return null;
	let clients=['xray','v2ray','ss-redir','ssr-redir','sslocal','ss-local','ssr-local','trojan','naive','hysteria','hysteria2','tuic-client','shadow-tls','redsocks2','ipt2socks'];
	for(let path in fs.glob(root+'/proc/[0-9]*/cmdline')??[]){
		let args=split(fs.readfile(path,8192)??'','\x00'),binary=args[0]??'';
		if(index(clients,fs.basename(binary))<0)continue;
		let config=length(filter(args,a=>match(a,/^\/(var|tmp)\/etc\/ssrplus\/[a-zA-Z0-9_.-]*(retcp|reudp|ssr-local|shunt)[a-zA-Z0-9_.-]*$/)))>0;
		if(config||match(binary,/^\/(var|tmp)\/etc\/ssrplus\/bin\//)&&!length(filter(args,a=>index(a,'ssr-server')>=0)))return true;
	}
	return false;
}
const plugins = [], rows = [
	['nailink','daed','main','enabled',null,null],
	['passwall','passwall',section('passwall','global'),'enabled',null,'dns_redirect'],
	['passwall2','passwall2',section('passwall2','global'),'enabled',null,'dns_redirect'],
	['openclash','openclash','config','enable',null,'enable_redirect_dns'],
	['nikki','nikki','config','enabled','proxy','ipv4_dns_hijack'],
	['ssrplus','shadowsocksr',section('shadowsocksr','global'),'global_server',null,null],
	['homeproxy','homeproxy','config','main_node','infra','dns_redirect']
];
let proxy_count = 0, active_proxy_count = 0, uncertain_proxies = [], active_proxies = [];
for (let row in rows) {
	let id = row[0], service = row[1], name = row[2], flag = row[3], hint_section = row[4], hint_option = row[5];
	let installed = !!(fs.stat(root + '/etc/config/' + service) || fs.stat(root + '/etc/init.d/' + service));
	let runtime = 'unknown', hint = 'unknown', enabled = null, manager_runtime = null, core_checked = null, core=null, identity=null, phase=null, core_reason=null, core_source='core_api';
	if (id == 'nailink') {
		installed ||= !!fs.stat(root + '/usr/share/daed-router/version') || !!processes.daed || owned_cores.nailink?.running===true;
		manager_runtime = processes.daed ? 'running' : service_state(service);
		if (installed && (manager_runtime == 'running'||owned_cores.nailink?.running===true)) {
			let state = proxy.nailink_state(root), running=state.running;
			core=state.core;phase=state.phase??null;core_reason=state.reason??null;core_source=state.evidence??'core_api';
			let worker=owned_cores.nailink;
			if ((running===true&&core!='dae'&&(worker?.running!==true||worker.core!=core)) ||
				(worker?.running===true&&(running===false||(core!=null&&(core=='dae'||worker.core!=core))))) {
				running=null;core_reason='core_process_mismatch';
			}
			identity=owned_cores.nailink?.identity??null;
			core_checked = type(running) == 'bool';
			runtime = core_checked ? (running ? 'running' : 'stopped') : 'unknown';
		} else if (installed && fs.stat(root + '/proc')?.type == 'directory') { runtime = 'stopped'; core_checked = true; }
		hint = 'proxy_core_dns';
	}
	let setting = value(service, name, flag);
	if (setting == '1') enabled = true; else if (setting == '0') enabled = false;
	if(index(['ssrplus','homeproxy'],id)>=0){
		enabled=setting==null?null:setting!=''&&setting!='nil';
		if(id=='homeproxy'&&value(service,'config','routing_mode')=='custom')enabled=null;
	}
	if (installed && id != 'nailink') runtime = service_state(service,id=='homeproxy'?'sing-box-c':null);
	let evidence=runtime!='unknown'?'service':'enabled_setting';
	if(installed&&id!='nailink'&&owned_cores[id]?.available){
		let core=owned_cores[id].running;
		if(core===true){runtime='running';core_checked=true;evidence='process_config';}
		else if(core===false||runtime=='unknown'){
			core_checked=type(core)=='bool';runtime=core_checked?'stopped':'unknown';
			evidence=core_checked?'process_inventory':'unconfirmed';
		}
		else if(core==null){runtime='unknown';core_checked=false;evidence='unconfirmed';}
	}
	if(id!='nailink'){core=owned_cores[id]?.core??null;identity=owned_cores[id]?.identity??null;}
	if(installed&&id=='ssrplus'){
		let running=ssrplus_client();core_checked=type(running)=='bool';
		runtime=core_checked?(running?'running':'stopped'):'unknown';
	}
	if (hint_option) {
		let setting = value(service, hint_section ?? name, hint_option);
		if (setting == '0') hint = 'disabled';
		else if (setting == '1') hint = 'plugin_redirect';
		else if (id == 'openclash' && setting == '2') hint = 'firewall_redirect';
	}
	if (installed) proxy_count++;
	let plugin = {id, installed, enabled, runtime, dns_hint:hint, integration:'unverified'};
	plugin.runtime_evidence=id=='nailink'?core_source:evidence;
	plugin.core=core;
	if(phase)plugin.core_phase=phase;
	if(core_reason)plugin.core_status_reason=core_reason;
	if(identity)plugin.runtime_identity=identity;
	plugin.adapter_supported=index(['nailink','passwall','passwall2','openclash','nikki','ssrplus'],id)>=0;
	plugin.routing_supported=index(['nailink','passwall','openclash','nikki'],id)>=0;
	if (id == 'nailink') plugin.manager_runtime = manager_runtime;
	if(core_checked!=null)plugin.core_checked=core_checked;
	plugin.active = proxy.active(plugin);
	if(id=='homeproxy'&&runtime=='unknown'){
		plugin.active=false;
		if(installed&&(enabled===true||value(service,'config','routing_mode')=='custom'))core_checked=false;
	}
	if (plugin.active) { active_proxy_count++; push(active_proxies,id); }
	if (installed && core_checked === false) push(uncertain_proxies,id);
	push(plugins,plugin);
}
const release = release_file('/etc/openwrt_release'), os = release_file('/etc/os-release');
const vendor = release.DISTRIB_ID || os.NAME || '', version = release.DISTRIB_RELEASE || os.VERSION_ID || '';
let family = 'unknown', lower = lc(vendor);
for (let name in ['istoreos','immortalwrt','openwrt']) if (index(lower, name) >= 0) { family = name; break; }
let track = 'unvalidated';
if (match(version, /^(24\.10|25\.12)(\.|$)/)) track = 'candidate';
else if (match(version, /^23\.05(\.|$)/)) track = 'legacy_candidate';
else if (index(lc(version), 'snapshot') >= 0) track = 'snapshot';
const opkg = has_command('opkg'), apk = has_command('apk');
const opkg_db = !!(fs.stat(root + '/usr/lib/opkg/status') || fs.stat(root + '/var/lib/opkg/status'));
const apk_db = !!fs.stat(root + '/lib/apk/db/installed');
let manager = 'unknown', arch = 'unknown';
// APK v3 can relocate its database or leave an old OPKG inventory behind.
// A native query for every base package proves an active inventory; merely
// finding an apk executable (or an empty database) does not.
let apk_inventory = apk;
let apk_args = root ? ['--root', root] : [];
if (match(release.DISTRIB_ARCH ?? '', /^[a-zA-Z0-9_+-]+$/))
	apk_args = [...apk_args, '--arch', release.DISTRIB_ARCH];
if (apk) for (let name in ['base-files','luci-base','rpcd'])
	if (command('apk', [...apk_args, 'info', '-e', name]) != name) apk_inventory = false;
if (apk_inventory) manager = 'apk';
else if (opkg_db && apk_db) manager = 'ambiguous';
else if (opkg_db && opkg) manager = 'opkg';
else if (apk_db && apk) manager = 'apk';
else if (opkg && !apk) manager = 'opkg';
else if (apk && !opkg) manager = 'apk';
else if (opkg && apk) manager = 'ambiguous';
if (manager == 'apk') arch = command('apk', ['--print-arch']);
else if (manager == 'opkg') {
	let priority = 0;
	for (let line in split(command('opkg', ['print-architecture']), '\n')) {
		let fields = split(trim(line), /[[:space:]]+/);
		if (fields[0] == 'arch' && fields[1] != 'all' && fields[1] != 'noarch' && int(fields[2]) > priority) {
			arch = fields[1]; priority = int(fields[2]);
		}
	}
}
if (!match(arch, /^[a-zA-Z0-9_+-]+$/)) arch = 'unknown';
let memory = match(fs.readfile(root + '/proc/meminfo') ?? '', /MemTotal:[[:space:]]*([0-9]+)/);
memory = memory ? int(memory[1]) : 0;
let dnsmasq_instances = 0, disabled_instances = 0;
uci.foreach('dhcp', 'dnsmasq', s => {
	if (dnsmasq_instances >= 64) return;
	dnsmasq_instances++;
	if (s.port == '0') disabled_instances++;
});
print(sprintf('%J\n', {schema_version:1, platform:{family, distribution:vendor, version, release_track:track,
	target:release.DISTRIB_TARGET ?? '', cpu:command('uname',['-m']) || 'unknown', kernel:command('uname',['-r']) || 'unknown',
	declared_arch:release.DISTRIB_ARCH ?? '', package_manager:manager, package_arch:arch,
	modern_luci:!!(fs.stat(root + '/www/luci-static/resources/luci.js') || fs.stat(root + '/www/luci-static/resources/view.js')),
	memory_kib:memory, resource_hint:memory ? (memory < 65536 ? 'low_memory' : 'normal') : 'unknown', validation:'not_device_tested'},
	plugins, cores:filter(['dae','daed','mihomo','clash','xray','sing-box'], n => processes[n]),
	resolvers:filter(['dnsmasq','smartdns','unbound','AdGuardHome'], n => processes[n]),
	dns:{dnsmasq_instances, disabled_instances, inventory_truncated:dnsmasq_instances == 64, proxy_count, active_proxy_count, active_proxies, uncertain_proxies,
		active_paths:map(filter(plugins,p=>p.active),p=>({plugin:p.id,core:p.core,identity:p.runtime_identity??null})),
		client_path:'unverified', ipv6_path:'unverified', encrypted_dns_bypass:'possible'}}));
