'use strict';
// Passive DNS decoding. No sockets, DNS lookups, configuration or filesystem IO.
function be16(s,n){if(n<0||n+2>length(s))die('short');return ord(s,n)*256+ord(s,n+1);}
function be32(s,n){return be16(s,n)*65536+be16(s,n+2);}
function ip(s,n,v6){let a=[];for(let i=0;i<(v6?16:4);i+=v6?2:1)push(a,v6?sprintf('%x',be16(s,n+i)):''+ord(s,n+i));return join(v6?':':'.',a);}
function name(s,start){
	let n=start,end=null,parts=[],seen={};
	for(let i=0;i<64;i++){
		if(n>=length(s)||seen[n])die('name');seen[n]=true;let size=ord(s,n++);
		if(!size){if(end==null)end=n;let value=lc(join('.',parts));if(length(value)>253)die('name');return {value,end};}
		if((size&192)==192){if(n>=length(s))die('pointer');if(end==null)end=n+1;n=((size&63)*256)+ord(s,n);continue;}
		if(size>63||n+size>length(s))die('label');let label=substr(s,n,size);if(!match(label,/^[A-Za-z0-9_-]+$/))die('label');push(parts,label);n+=size;
	}die('name');
}
export function dns(raw){
	try{
		if(length(raw)<12||be16(raw,4)!=1)return null;
		let flags=be16(raw,2),q=name(raw,12),qtype=be16(raw,q.end),qclass=be16(raw,q.end+2);
		if((flags&0x7800)||qclass!=1||index([1,28],qtype)<0||!length(q.value))return null;
		let response=!!(flags&0x8000),rcode=flags&15,positive=false,blackhole=false;
		if(response){
			let p=q.end+4,answers=be16(raw,6);if(answers>256)return null;
			for(let i=0;i<answers;i++){
				let rr=name(raw,p);p=rr.end;let kind=be16(raw,p),cls=be16(raw,p+2),size=be16(raw,p+8);p+=10;
				if(p+size>length(raw))return null;
				if(cls==1&&((kind==1&&size==4)||(kind==28&&size==16))){
					let zero=true;for(let j=0;j<size;j++)if(ord(raw,p+j))zero=false;
					let loopback=size==4&&substr(raw,p,4)=='\x7f\x00\x00\x01';
					if(size==16&&ord(raw,p+15)==1){loopback=true;for(let j=0;j<15;j++)if(ord(raw,p+j))loopback=false;}
					if(zero||loopback)blackhole=true;else positive=true;
				}p+=size;
			}
		}
		return {id:be16(raw,0),name:q.value,qtype,response,rcode,positive:response&&!(flags&0x0200)&&rcode==0&&positive,
			blocked:response&&!(flags&0x0200)&&(rcode==3||rcode==5||rcode==0&&!positive&&(be16(raw,6)==0||blackhole))};
	}catch(e){return null;}
};
export function rules_match(rules,domain){
	for(let n=domain;index(n,'.')>=0;n=substr(n,index(n,'.')+1))if(rules[n])return true;
	return false;
};
export function decoder(callback){
	let buffer='',endian=null,link=null,flows={},clock=0,nanoseconds=false,captured_us=null;
	function word(s,p){if(endian)return ord(s,p)+ord(s,p+1)*256+ord(s,p+2)*65536+ord(s,p+3)*16777216;return be32(s,p);}
	function message(raw,from,to,sport,dport,protocol){let d=dns(raw);if(d)callback({...d,from,to,sport,dport,protocol,captured_us});}
	function packet(raw){
		try{
			let p=0,proto;
			if(link==1){proto=be16(raw,12);p=14;for(let v=0;v<2&&index([0x8100,0x88a8],proto)>=0;v++){proto=be16(raw,p+2);p+=4;}}
			else if(link==113){proto=be16(raw,14);p=16;}
			else if(link==276){proto=be16(raw,0);p=20;}
			else if(link==101||link==12){proto=(ord(raw,0)>>4)==6?0x86dd:0x0800;}
			else return;
			let from,to,transport,end;
			if(proto==0x0800){
				let h=(ord(raw,p)&15)*4;if(h<20||(ord(raw,p)>>4)!=4||(be16(raw,p+6)&0x3fff))return;
				end=p+be16(raw,p+2);if(end>length(raw)||end<p+h)return;
				from=ip(raw,p+12,false);to=ip(raw,p+16,false);transport=ord(raw,p+9);p+=h;
			}else if(proto==0x86dd){
				if((ord(raw,p)>>4)!=6)return;end=p+40+be16(raw,p+4);if(end>length(raw))return;
				from=ip(raw,p+8,true);to=ip(raw,p+24,true);transport=ord(raw,p+6);p+=40;
				for(let i=0;i<8&&index([0,43,60,51],transport)>=0;i++){
					let next=ord(raw,p),size=(ord(raw,p+1)+(transport==51?2:1))*(transport==51?4:8);p+=size;transport=next;if(p>end)return;
				}if(transport==44)return;
			}else return;
			if(p+8>end)return;let sport=be16(raw,p),dport=be16(raw,p+2);if(sport!=53&&dport!=53)return;
			if(transport==17){let size=be16(raw,p+4);if(size<20||p+size>end)return;message(substr(raw,p+8,size-8),from,to,sport,dport,'udp');}
			else if(transport==6){
				if(p+20>end)return;let h=(ord(raw,p+12)>>4)*4;if(h<20||p+h>end)return;
				let seq=be32(raw,p+4),flags=ord(raw,p+13),data=substr(raw,p+h,end-p-h),key=join('|',[from,to,sport,dport]);
				if(flags&4){delete flows[key];return;}if(flags&2){delete flows[key];seq=(seq+1)%4294967296;}
				if(!length(data))return;
				let flow=flows[key];if(!flow){if(length(keys(flows))>=128)return;flow=flows[key]={next:seq,buffer:'',at:clock};}
				let overlap=flow.next-seq;if(overlap<0){delete flows[key];return;}if(overlap>=length(data))return;
				data=substr(data,overlap);flow.next=(seq+overlap+length(data))%4294967296;flow.buffer+=data;flow.at=clock;
				if(length(flow.buffer)>131074){delete flows[key];return;}
				while(length(flow.buffer)>=2){let size=be16(flow.buffer,0);if(size<12){delete flows[key];return;}if(length(flow.buffer)<size+2)break;
					message(substr(flow.buffer,2,size),from,to,sport,dport,'tcp');flow.buffer=substr(flow.buffer,size+2);}
			}
		}catch(e){} // Truncated, fragmented or malformed traffic is never evidence.
	}
	return {
		feed:function(chunk){
			buffer+=chunk;if(length(buffer)>1048576)die('capture_buffer');
			if(endian===null){
				if(length(buffer)<24)return;let magic=be32(buffer,0);
				if(index([0xd4c3b2a1,0x4d3cb2a1],magic)>=0)endian=true;
				else if(index([0xa1b2c3d4,0xa1b23c4d],magic)>=0)endian=false;else die('capture_format');
				nanoseconds=index([0x4d3cb2a1,0xa1b23c4d],magic)>=0;
				link=word(buffer,20);if(index([1,12,101,113,276],link)<0)die('capture_link');buffer=substr(buffer,24);
			}
			while(length(buffer)>=16){let size=word(buffer,8);if(size>262144)die('capture_size');if(length(buffer)<16+size)return;
				let fraction=word(buffer,4),scale=nanoseconds?1000000000:1000000;
				// Packet capture time survives IPC/collector delays. Convert nano
				// fractions to microseconds before combining, within exact integers.
				captured_us=fraction<scale?word(buffer,0)*1000000+int(fraction/(nanoseconds?1000:1)):null;
				packet(substr(buffer,16,size));buffer=substr(buffer,16+size);}
		},
		tick:function(now){clock=now;for(let key in keys(flows))if(now-flows[key].at>15)delete flows[key];}
	};
};
// Bounded, opt-in recent blockers. No client addresses or raw packets retained.
export function recent(){
	let token=null,deadline=0,items=[],total=0;
	function clear(){token=null;deadline=0;items=[];total=0;}
	return {
		configure:function(id,until,now){if(!id||until<=now||until>now+301){clear();return;}if(token!=id){items=[];total=0;}token=id;deadline=until;},
		event:function(d,now,wall){if(!token||now>=deadline){clear();return;}if(type(d.name)!='string'||length(d.name)>253||!match(d.name,/^[a-z0-9_.-]+$/)||index([1,28],d.qtype)<0)return;let item=filter(items,i=>i.domain==d.name)[0]??{domain:d.name,count:0,qtypes:[]};item.count++;item.at=wall;if(index(item.qtypes,d.qtype)<0)push(item.qtypes,d.qtype);items=[item,...filter(items,i=>i.domain!=d.name)];if(length(items)>30)pop(items);total++;},
		snapshot:function(now){if(now>=deadline)clear();return {enabled:!!token,remaining:token?int(deadline-now):0,total,items};}
	};
};
export function tracker(onblocked){
	let pending={},pending_count=0,seen={},seen_count=0,rules={},generation='',active=false,verify=null;
	let counts={queries:0,blocked:0,answered:0,overflow:0,matched:0,matched_answered:0,matched_positive:0,matched_error:0};
	let current={};for(let k in keys(counts))current[k]=0;
	function bucket(){return {samples:0,total_us:0,maximum_us:0,slow:0};}
	let normal=bucket(),blocked=bucket(),unanswered={udp:0,tcp:0};
	function clear_latency(){normal=bucket();blocked=bucket();unanswered={udp:0,tcp:0};}
	function latency_sample(req,d){
		if(type(req.captured_us)!='int'||type(d.captured_us)!='int'||req.captured_us<0||d.captured_us<0)return;
		let elapsed=d.captured_us-req.captured_us;
		if(elapsed<0||elapsed>15000000)return; // clock steps/late samples aren't latency evidence
		let value=req.blocked&&d.blocked?blocked:!req.blocked&&d.positive?normal:null;
		if(!value)return;
		value.samples++;value.total_us+=elapsed;value.maximum_us=max(value.maximum_us,elapsed);if(elapsed>=500000)value.slow++;
	}
	function latency_value(value){return {samples:value.samples,average_ms:value.samples?value.total_us/(value.samples*1000.0):null,maximum_ms:value.samples?value.maximum_us/1000.0:null,slow:value.slow};}
	function count(k){counts[k]++;current[k]++;}
	function key(d,response){return join('|',[d.protocol,response?d.to:d.from,response?d.from:d.to,response?d.dport:d.sport,response?d.sport:d.dport,d.id,d.name,d.qtype]);}
	return {
		counts,current,
		reset:function(){let keep={};for(let id in keys(pending))if(pending[id].test)keep[id]=pending[id];pending=keep;pending_count=length(keep);seen={};seen_count=0;for(let k in keys(counts)){counts[k]=0;current[k]=0;}clear_latency();},
		configure:function(list,id,on){if(id!=generation){pending={};pending_count=0;seen={};seen_count=0;verify=null;for(let k in keys(current))current[k]=0;clear_latency();}rules=list;generation=id;active=on;},
		latency:function(){return {scope:'lan_observed',period:'current_connection',slow_ms:500,wait_seconds:15,normal:latency_value(normal),blocked:latency_value(blocked),unanswered:{...unanswered}};},
		verification:function(value){if(type(value)=='object')verify=value;return verify;},
		event:function(d,now){
			if(!active)return;let id=key(d,d.response),testname=match(d.name,/^([a-f0-9]{32})\.adzero\.invalid$/);
			if(testname){if(!d.response&&verify&&now<verify.deadline&&testname[1]==verify.nonce){verify.client=d.from;verify.server=d.to;verify.marker=true;}return;}
			if(d.response){
				let req=pending[id];if(!req||now-req.at>15)return;delete pending[id];pending_count--;seen[id]=now;seen_count++;
				if(req.test){if(verify&&req.generation==generation){if(d.name==verify.sample&&d.blocked)verify.blocked=true;if(d.name==verify.control&&d.positive)verify.normal=true;}return;}
				latency_sample(req,d);
				count('answered');if(req.blocked){count('matched_answered');if(d.positive)count('matched_positive');if(d.rcode==2)count('matched_error');if(d.blocked){count('blocked');if(onblocked)onblocked(d,now);}}return;
			}
			if(pending[id]||seen[id]!=null&&now-seen[id]<15)return;
			if(pending_count>=4096||seen_count>=32768){count('overflow');return;}
			let test=verify&&verify.marker&&verify.client==d.from&&verify.server==d.to&&now<verify.deadline&&index([verify.sample,verify.control],d.name)>=0;
			let blocked=rules_match(rules,d.name);
			pending[id]={at:now,blocked,test:!!test,generation,captured_us:d.captured_us,protocol:d.protocol};pending_count++;if(!test){count('queries');if(blocked)count('matched');}
		},
		tick:function(now){for(let id in keys(pending))if(now-pending[id].at>15){let req=pending[id];if(!req.test&&index(['udp','tcp'],req.protocol)>=0)unanswered[req.protocol]++;delete pending[id];pending_count--;}for(let id in keys(seen))if(now-seen[id]>15){delete seen[id];seen_count--;}}
	};
};
