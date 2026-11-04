'use strict';
// Shared policy compiler. No network calls, shell commands or persistent writes.

export const domestic = ['223.5.5.5', '119.29.29.29'];
export const foreign = '1.1.1.1';
let ip_bytes;
export function addresses(p,group){let g=p[group];return g.mode=='parallel'?[g.active,...filter(g.servers,v=>v!=g.active)]:[g.active];};
export function profile(value) {
	let defaults={domestic:{servers:[...domestic],active:domestic[0]},foreign:{servers:[foreign,'8.8.8.8'],active:foreign}};
	if(value==null)return defaults;
	if(type(value)!='object')die('DNS 设置无效。');
	let result={};
	for(let group in ['domestic','foreign']){
		let item=value[group];
		if(type(item)!='object'||type(item.servers)!='array'||!length(item.servers)||length(item.servers)>8)die('每组 DNS 请保留 1–8 个 IP 地址。');
		let servers=[];
		for(let address in item.servers){
			if(type(address)!='string'||length(address)>45||address!=trim(address))die('DNS 需要 IPv4 或 IPv6 地址。');
			address=lc(address);let bytes;try{bytes=ip_bytes(address);}catch(e){die('DNS 需要有效的 IPv4 或 IPv6 地址。');}
			if(length(bytes)==4&&(ord(bytes,0)==0||ord(bytes,0)==127||ord(bytes,0)>=224)||length(bytes)==16&&(bytes=='\x00\x00\x00\x00\x00\x00\x00\x00\x00\x00\x00\x00\x00\x00\x00\x00'||bytes=='\x00\x00\x00\x00\x00\x00\x00\x00\x00\x00\x00\x00\x00\x00\x00\x01'||ord(bytes,0)==255))die('DNS 不能使用未指定、回环或组播地址。');
			if(length(filter(servers,other=>ip_bytes(other)==bytes)))die('同组 DNS 地址不能重复。');push(servers,address);
		}
		if(type(item.active)!='string'||index(servers,lc(item.active))<0)die('请从每组地址中选择当前使用的 DNS。');
		if(item.mode!=null&&index(['single','parallel'],item.mode)<0)die('DNS 查询策略无效。');
		if(item.mode=='parallel'&&length(servers)>3)die('组内并发最多设置 3 个 DNS，避免请求放大。');
		result[group]={servers,active:lc(item.active)};
		if(item.mode=='parallel')result[group].mode='parallel';
	}
	if(ip_bytes(result.domestic.active)==ip_bytes(result.foreign.active))die('国内和国外 DNS 请选择不同地址，以便分别指定连接路径。');
	if(value.strict!=null&&type(value.strict)!='bool')die('严格 DNS 模式需要明确的开关值。');
	if(value.strict===true)result.strict=true;
	if(value.failover!=null){if(type(value.failover)!='bool')die('自动备用需要明确的开关值。');result.failover=value.failover;}
	if(value.proxy_port!=null){if(type(value.proxy_port)!='int'||value.proxy_port!=0&&(value.proxy_port<1024||value.proxy_port>65535))die('本机 SOCKS5 端口需要 1024–65535。');result.proxy_port=value.proxy_port;}
	for(let d in addresses(result,'domestic'))for(let f in addresses(result,'foreign'))if(ip_bytes(d)==ip_bytes(f))die('国内和国外的生效 DNS 地址不能重叠。');
	return result;
};
export function effective(value){let p=profile(value);return {domestic:addresses(p,'domestic'),foreign:addresses(p,'foreign'),strict:p.strict===true,proxy_port:p.strict||p.foreign.mode=='parallel'?p.proxy_port??0:0};};
function selected(value){let p=profile(value);return {domestic:addresses(p,'domestic'),foreign:p.foreign.active,foreign_all:addresses(p,'foreign'),profile:p};}
export function endpoint(address){return index(address,':')>=0?'['+address+']:53':address+':53';};
function literal(address){return index(address,':')>=0?sprintf('%J',address):address;}
const country_record_re=/^server=\/([a-z0-9.-]+)\/[0-9.]+$/, country_name_re=/^[a-z0-9][a-z0-9.-]*[a-z0-9]$/, number_re=/^[0-9.]+$/;
const octet_re=/^(0|[1-9][0-9]{0,2})$/, ipv6_re=/^[a-f0-9:]+$/, word_re=/^[a-f0-9]{1,4}$/;
export const sources = [
	{name:'domains',url:'https://raw.githubusercontent.com/felixonmars/dnsmasq-china-list/c79474a5445e7ebbde7275648b1078a0582510ab/accelerated-domains.china.conf',sha256:'d5ff2f2fe2a0ed4c4380455351259e32d8635a3d60e513268ab0fec41110339d'},
	{name:'cidrs',url:'https://raw.githubusercontent.com/Loyalsoldier/geoip/bf01519a9e21f366266bd48c6afae430ec8febfe/text/cn.txt',sha256:'95e593f9422de0c5ab719dad70a5f209517a74b74df7372f53335eb903085d20'}
];
export function domain_list(raw) {
	if (type(raw) != 'string' || length(raw) > 5000000) die('国内域名库为空或超过大小限制。');
	let result = {};
	for (let line in split(raw, '\n')) {
		line = trim(line);
		if (!length(line) || substr(line,0,1) == '#') continue;
		let record = match(line, country_record_re);
		if (!record) die('国内域名库格式异常。');
		let name = record[1];
		if (length(name) > 253 || !match(name,country_name_re) || index(name,'..') >= 0 || match(name,number_re)) die('国内域名库含无效域名。');
		for (let label in split(name,'.')) if (length(label)>63 || substr(label,0,1)=='-' || substr(label,-1)=='-') die('国内域名库标签无效。');
		result[name] = true;
	}
	let names = sort(keys(result));
	if (!length(names) || length(names)>200000) die('国内域名库为空或超限。');
	return names;
};
ip_bytes = function(value) {
	if (index(value,':')<0) {
		let parts = split(value,'.');
		if (length(parts)!=4) die('Invalid IPv4 CIDR');
		let result='';
		for (let p in parts) {
			if (!match(p,octet_re) || int(p)>255) die('Invalid IPv4 octet');
			result += chr(int(p));
		}
		return result;
	}
	if (!match(value,ipv6_re)) die('Invalid IPv6 CIDR');
	let halves=split(value,'::'), parts=[];
	if (length(halves)>2) die('Invalid IPv6 contraction');
	let left=length(halves[0])?split(halves[0],':'):[], right=length(halves)==2 && length(halves[1])?split(halves[1],':'):[];
	if (length(halves)==1) parts=left;
	else {
		let missing=8-length(left)-length(right);
		if (missing<1) die('Invalid IPv6 length');
		parts=[...left];for(let i=0;i<missing;i++)push(parts,'0');parts=[...parts,...right];
	}
	if (length(parts)!=8) die('Invalid IPv6 length');
	let result='';
	for (let p in parts) {
		if (!match(p,word_re)) die('Invalid IPv6 word');
		let n=int(p,16);result+=chr(int(n/256))+chr(n%256);
	}
	return result;
};
export function cidr_list(raw) {
	if (type(raw)!='string' || length(raw)>1000000) die('国内 IP 库为空或超过大小限制。');
	let result={};
	for (let line in split(raw,'\n')) {
		line=trim(line);if(!length(line)||substr(line,0,1)=='#')continue;
		let parts=split(line,'/');
		if(length(parts)!=2 || !match(parts[1],octet_re))die('Invalid CIDR prefix');
		let bytes=ip_bytes(parts[0]),prefix=int(parts[1]);
		if(prefix>length(bytes)*8 || prefix==0)die('Unsafe CIDR prefix');
		// Network addresses only, rather than silently broadening a host entry.
		for(let bit=prefix;bit<length(bytes)*8;bit++)if(ord(bytes,int(bit/8)) & (1<<(7-bit%8)))die('CIDR contains host bits');
		result[line]=true;
	}
	let names=sort(keys(result));if(!length(names)||length(names)>30000)die('国内 IP 库为空或超限。');return names;
};
function varint(n) {let s='';while(true){let v=n%128;n=int(n/128);s+=chr(v+(n?128:0));if(!n)break;}return s;}
function field(n,s) {return chr(n*8+2)+varint(length(s))+s;}
export function geoip(cidrs) {
	let parts=[field(1,'ADZERO')];
	for(let cidr in cidrs){let p=split(cidr,'/');push(parts,field(2,field(1,ip_bytes(p[0]))+chr(16)+varint(int(p[1]))));}
	return field(1,join('',parts));
};
export function expand_blockers(blocked,candidates) {
	let result={};for(let name in blocked)result[name]=true;
	for(let name in candidates){
		let parent=name;
		while(length(parent)){
			if(result[parent]){result[name]=true;break;}
			let dot=index(parent,'.');if(dot<0)break;parent=substr(parent,dot+1);
		}
	}
	return sort(keys(result));
};

// Dae block locations, ignoring quoted strings, escapes and comments.
function sections(text) {
	let found=[],stack=[],word='',last='',quote='',comment=false;
	for(let i=0;i<length(text);i++){
		let c=substr(text,i,1);
		if(comment){if(c=='\n')comment=false;continue;}
		if(quote){if(c=='\\'){i++;continue;}if(c==quote)quote='';continue;}
		if(c=='#'){comment=true;continue;}
		if(c=='"'||c=="'"){quote=c;word=last='';continue;}
		if(match(c,/[A-Za-z0-9_]/)){word+=c;continue;}
		if(length(word)){last=word;word='';}
		if(c=='{'){let node={name:last,open:i,path:join('.',map(stack,s=>s.name))+(length(stack)?'.':'')+last};push(stack,node);last='';}
		else if(c=='}'){let node=pop(stack);if(!node)die('Unbalanced dae config');node.close=i;push(found,node);last='';}
		else if(!match(c,/[[:space:]]/))last='';
	}
	if(length(stack)||quote)die('Incomplete dae config');return found;
}
function owned(name,text) {return '# ADZERO SPLIT '+name+' BEGIN v1\n'+text+'\n# ADZERO SPLIT '+name+' END v1\n';}
export function dae_dns(raw,settings) {
	let addresses=selected(settings),domestic=addresses.domestic,foreign=addresses.foreign;
	if(index(raw,'# ADZERO SPLIT ')>=0)return raw; // journal equality is checked by caller
	let blocks=sections(raw),edits=[],paths=['upstream','routing','routing.request'];
	for(let path in paths){let hit=filter(blocks,b=>b.path==path);if(length(hit)!=1)die('Expected one dae DNS '+path);}
	let responses=filter(blocks,b=>b.path=='routing.response');
	if(length(responses)>1)die('Expected at most one dae DNS routing.response');
	// dae's official default profile omits response; its default is accept.
	// Add no new response policy when none existed. Only protect our upstreams
	// from an existing response retry chain when the user actually has one.
	for(let block in blocks){
		if(block.path=='upstream')push(edits,{at:block.close,text:'\n'+owned('UPSTREAM','adzero_cn: "'+(addresses.profile.domestic.mode=='parallel'||addresses.profile.strict?'tcp://127.0.0.1:5356':'udp://'+endpoint(domestic[0]))+'"\nadzero_global: "'+(addresses.profile.foreign.mode=='parallel'||addresses.profile.strict?'tcp://127.0.0.1:5357':'tcp://'+endpoint(foreign))+'"')});
		if(block.path=='routing.request')push(edits,{at:addresses.profile.strict?block.open+1:block.close,text:'\n'+owned('REQUEST',(addresses.profile.strict?'qname(ext:"adzero-geosite.dat:adzero") -> reject\n':'')+'qname(ext:"adzero-cn-geosite.dat:adzero") -> adzero_cn\nqname(regex: ".*") -> adzero_global')});
		if(block.path=='routing.response')push(edits,{at:block.open+1,text:'\n'+owned('RESPONSE','upstream(adzero_cn, adzero_global) -> accept')});
	}
	for(let e in sort(edits,(a,b)=>b.at-a.at))raw=substr(raw,0,e.at)+e.text+substr(raw,e.at);
	return raw;
};
export function dae_routing(raw,target,settings) {
	let addresses=selected(settings),domestic=addresses.domestic,foreign=addresses.foreign;
	if(type(target)!='string'||!length(target)||length(target)>100||match(target,/[[:space:]{}()"'#:,<>;&|=\/\\]/)||index(['direct','must_direct','block','reject'],target)>=0)die('没有可识别的默认代理策略。');
	if(index(raw,'# ADZERO SPLIT ')>=0)return raw;
	// Insert before fallback, after the user's explicit rules. DNS upstream
	// transport overrides are first, so foreign DNS cannot accidentally go direct.
	let fallback=match(raw,/(^|\n)([ \t]*fallback[ \t]*:)/);
	if(!fallback)die('Expected a standalone dae fallback line');
	let at=index(raw,fallback[0]);
	let direct='dip('+join(', ',map([...domestic,'127.0.0.0/8','10.0.0.0/8','172.16.0.0/12','192.168.0.0/16','::1/128','fc00::/7','fe80::/10'],v=>index(v,':')>=0?sprintf('%J',v):v))+') -> direct';
	let prefix=owned('TRANSPORT','dip('+join(', ',map(addresses.foreign_all,literal))+') && dport(53) -> '+target+'\ndip('+join(', ',map(domestic,literal))+') && dport(53) -> direct');
	let defaults=owned('ROUTING',direct+'\ndomain(ext:"adzero-cn-geosite.dat:adzero") -> direct\ndip(ext:"adzero-cn-geoip.dat:adzero") -> direct');
	return prefix+substr(raw,0,at)+'\n'+defaults+substr(raw,at);
};
export function dae_routing_references(raw) {
	return type(raw) != 'string' || match(raw, /ADZERO (SPLIT |ALIAS BRIDGE )|adzero-[a-z0-9_-]+[.]dat/) != null;
};
export function restore_copied_dae_routing(current, recorded) {
	if (type(current) != 'string' || dae_routing_references(recorded.before)) return null;
	let blocks = [
		/(^|\n)(# ADZERO SPLIT TRANSPORT BEGIN v1\n[^#]*# ADZERO SPLIT TRANSPORT END v1\n)/,
		/(^|\n)(# ADZERO SPLIT ROUTING BEGIN v1\n[^#]*# ADZERO SPLIT ROUTING END v1\n)/
	];
	for (let snapshot in [recorded.after, recorded.previous_after]) {
		if (type(snapshot) != 'string') continue;
		let remaining = '\n' + current;
		for (let pattern in blocks) {
			let block = match(snapshot, pattern)?.[2];
			let parts = block ? split(remaining, '\n' + block) : [];
			if (length(parts) != 2) {
				remaining = null;
				break;
			}
			remaining = parts[0] + '\n' + parts[1];
		}
		if (!dae_routing_references(remaining)) return substr(remaining, 1);
	}
	return null;
};
export function mihomo(config,domain_path,ip_path,settings) {
	let addresses=selected(settings),domestic=addresses.domestic,foreign=addresses.foreign;
	if(config.dns?.enable!=true)die('代理 DNS 未开启。');
	let rules=config.rules??[],matches=filter(rules,r=>match(r,/^MATCH,[^,]+$/));
	if(length(matches)!=1 || substr(rules[length(rules)-1],0,6)!='MATCH,')die('需要唯一的末尾 MATCH 代理策略。');
	let target=substr(matches[0],6),names=map(config['proxy-groups']??[],g=>g.name);
	if(index(names,target)<0 || index(['DIRECT','REJECT','PASS','GLOBAL'],target)>=0 || match(target,/[#\r\n]/))die('无法自动确认当前默认代理策略。');
	let providers=config['rule-providers']??={};
	for(let entry in [['adzero-cn-domain',domain_path,'domain'],['adzero-cn-ip',ip_path,'ipcidr']]){
		let name=entry[0],path=entry[1],behavior=entry[2];
		if(providers[name] && providers[name].path!=path)die('Reserved split provider exists');
		providers[name]={type:'file',behavior,format:'text',path};
	}
	let own=['RULE-SET,adzero-cn-domain,DIRECT','RULE-SET,adzero-cn-ip,DIRECT,no-resolve',
		'IP-CIDR,127.0.0.0/8,DIRECT,no-resolve','IP-CIDR,10.0.0.0/8,DIRECT,no-resolve','IP-CIDR,172.16.0.0/12,DIRECT,no-resolve','IP-CIDR,192.168.0.0/16,DIRECT,no-resolve',
		'IP-CIDR6,::1/128,DIRECT,no-resolve','IP-CIDR6,fc00::/7,DIRECT,no-resolve','IP-CIDR6,fe80::/10,DIRECT,no-resolve'];
	rules=filter(rules,r=>index(own,r)<0);pop(rules);config.rules=[...rules,...own,matches[0]];config.mode='rule';
	let dns=config.dns;
	dns['default-nameserver']=[...domestic];dns['proxy-server-nameserver']=[...domestic];dns['direct-nameserver']=[...domestic];
	if(addresses.profile.strict){delete dns['direct-nameserver'];dns['direct-nameserver-follow-policy']=true;}
	dns.nameserver=map(addresses.foreign_all,f=>'tcp://'+endpoint(f)+'#'+target);delete dns.fallback;delete dns['fallback-filter'];
	let policies={};
	// Keep the existing ad reject provider before the China domain provider.
	if(dns['nameserver-policy']?.['rule-set:adzero-dns'])policies['rule-set:adzero-dns']=dns['nameserver-policy']['rule-set:adzero-dns'];
	if(!addresses.profile.strict)for(let key in keys(dns['nameserver-policy']??{}))if(key!='rule-set:adzero-dns'&&key!='rule-set:adzero-cn-domain')policies[key]=dns['nameserver-policy'][key];
	policies['rule-set:adzero-cn-domain']=map(domestic,d=>'udp://'+endpoint(d)+'#DIRECT');dns['nameserver-policy']=policies;
	let fake=dns['fake-ip-filter-mode']=='rule'?'RULE-SET,adzero-cn-domain,real-ip':'rule-set:adzero-cn-domain';
	dns['fake-ip-filter']=[fake,...filter(dns['fake-ip-filter']??[],f=>f!=fake)];
	return {config,target};
};
