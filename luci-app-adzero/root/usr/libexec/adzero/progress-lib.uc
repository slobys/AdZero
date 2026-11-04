'use strict';
// Public task metadata is a fixed vocabulary. Never forward private log text.
import * as fs from 'fs';
export const stages=['queued','waiting_lock','waiting_proxy','checking','downloading','validating','processing','adapting','restarting','confirming_proxy','recovering_proxy','restoring','verifying'];
export const messages={
	nailink_external_edit:'NaiLink 存在外部编辑，已保留关闭请求和恢复记录。请先在 NaiLink 完成或撤销修改，后台会继续核对。',
	nailink_routing_conflict:'NaiLink 流量路由与恢复记录不一致，无法确认可移除的规则。请核对 NaiLink 当前选中的流量路由配置。',
	nailink_recovery_limit:'NaiLink 原生恢复尚未确认，已保留关闭请求和恢复记录。不会循环重启；请检查 NaiLink 运行状态。',
	control_scope:'部分域名由父域规则拦截。请先在“访问控制”查询域名，核对实际命中范围后加入放行；待应用选择已保留。',
	config_conflict:'配置有其他未保存的编辑或已经变化。请重新读取并核对；规则数量统计不会阻止应用。',
	empty_ruleset:'本次草稿没有启用订阅或手动拦截项，未应用、未重载。请保留至少一项规则；暂停全部过滤请使用暂停或保护开关。',
	source_capacity_unknown:'部分来源无法完整预检容量，未提交或重载。请选择域名、Hosts 或基础 DNS Adblock 格式；不会把未知来源按零计算，现有配置保留。',
	nailink_auth:'NaiLink 后端授权未通过。请通过原生服务重启刷新本机授权，再关闭并恢复 AdZero。',
	nailink_gateway:'NaiLink 重载请求尚未确认，规则与恢复记录已保留。后台继续核对；需要退出时可关闭并恢复，旧记录必要时仅恢复 NaiLink 服务，无需重启路由器。',
	nailink_transition:'NaiLink 内核仍在切换或恢复，暂未修改配置。请等待其就绪后重试关闭；无需重启路由器或删除锁。',
	lock_busy:'本次请求未取得操作锁，未执行任何配置修改。请核对当前保护状态，等待其他操作完成后按需重试。',
	source_download:'规则下载失败，已停止应用。请检查该订阅的 HTTPS 地址与网络。',
	source_format:'下载内容未通过规则格式校验，原有选择未被替换。',
	source_check:'订阅预检未完成，请核对本地快照、临时文件或订阅设置；具体错误保留在路由器任务日志。未确认的更改不会视为已应用。',
	source_limit:'规则超过当前设备的单源、预处理或最终加载保护上限。请核对规则页的设备容量；不是已启用规则占用的累计配额，重复域名会自动去重。',
	source_duplicate:'存在完全相同的订阅地址或规则标识，请编辑或删除重复项。不同列表的域名重叠会自动去重。',
	country_data:'国内分类库下载或校验失败，未能开启自动分流。',
	adapter:'代理 DNS 配置未能确认完成，请查看任务日志和当前状态。',
	dns_pool_start_failed:'DNS 并发服务启动请求失败，未切入新的并发 DNS 路径。',
	dns_pool_not_ready:'DNS 并发入口在等待期限内尚未就绪，未切入新的并发 DNS 路径。',
	dns_pool_state_invalid:'DNS 并发入口的健康记录未通过文件或内容校验。',
	dns_pool_profile_mismatch:'DNS 并发入口尚未载入本次 DNS 设置。',
	dns_pool_stale:'DNS 并发入口的健康记录已过期或时间无效。',
	dns_pool_process_exited:'DNS 并发入口的健康记录对应进程已退出或无法确认。',
	dns_pool_bind_failed:'DNS 并发入口监听失败，可能有其他服务占用所需端口。',
	dns_pool_runtime_failed:'DNS 并发进程报告运行错误，请保留任务日志。',
	dns_pool_preflight_failed:'DNS 并发传输预检未完成，尚未切换 DNS 路径。',
	dns_pool_domestic_failed:'国内并发需要的 TCP DNS 路径未通过。UDP 测试成功不代表此路径可用；请保留或改用“单一＋备用”。',
	dns_pool_foreign_failed:'国外并发的代理 DNS 路径未通过。请核对本机 SOCKS5、节点和上游；没有回落直连。',
	dns_passwall_mode:'PassWall 当前 DNS 引擎不支持国外并发。请把国外组改为“单一 + 备用”，国内组仍可并发；不会替换原引擎。',
	dns_socks_required:'此 DNS 组合需要当前代理的本机无认证 SOCKS5 端口。请填写真实监听端口，或关闭国外并发／严格模式。',
	dns_socks_unavailable:'本机 SOCKS5 的 DNS 查询未通过。请核对端口、监听和认证设置；没有回落直连。',
	openclash_hook:'OpenClash 未保留 AdZero 的生成配置，请检查自定义覆写设置；原始错误保留在任务日志。',
	openclash_yaml:'OpenClash 配置读取或字符转换失败，尚未完成代理接入；原配置与恢复记录已保留。',
	openclash_dns:'OpenClash 的 DNS 未开启，无法接入广告过滤。请先在 OpenClash 中启用 DNS。',
	openclash_core:'OpenClash 的 Mihomo 内核不存在或未通过配置检查，请检查原生内核与配置。',
	openclash_confirmation:'未收到 OpenClash 配置生成回执，请检查原生启动日志；本次开启未完成。',
	backend:'过滤后端未能完成规则处理或 DNS 重载，请检查后端状态。',
	backend_memory:'过滤后端报告内存或共享内存不足。请减少启用的列表，保留一份主列表并按需补充。',
	backend_dns:'过滤后端未能完成 DNS 重载或未确认解析服务运行。请保留失败任务日志，不要反复重启代理。',
	backend_processing:'规则解析、排序、输出格式或处理结果校验未通过。请核对订阅格式及任务日志中的固定错误代码。',
	backend_files:'过滤后端未能创建或更新规则输出文件。请核对空间、目录和权限，保留原配置及恢复记录。',
	interrupted:'后台任务已停止且没有完成回执，请重新检测；有恢复提示时先关闭并恢复。'
};
export function message(reason,recovery){
	let text=messages[reason]??'操作未确认完成，原始日志仅保留在路由器。请重新检测；必要时关闭并恢复。';
	if(index(reason??'','dns_pool_')==0){
		if(recovery=='restored')text+=' 本次操作已撤销，原配置已恢复。';
		else if(recovery=='unconfirmed')text+=' 原配置恢复尚未确认，请检查 AdZero 恢复记录。';
	}
	return text;
};
function write(name,value){
	let dir=getenv('ADZERO_JOB_CONTEXT');if(!dir||fs.lstat(dir)?.type!='directory')return;
	let path=dir+'/'+name,tmp=path+'.new';
	if(fs.lstat(path)?.type=='link'||fs.lstat(tmp)?.type=='link')return;
	let file=fs.open(tmp,'w',0600);if(!file)return;
	let written=file.write(value),closed=file.close();if(written==length(value)&&closed)fs.rename(tmp,path);else fs.unlink(tmp);
}
export function stage(value){
	let dir=getenv('ADZERO_JOB_CONTEXT');if(index(stages,value)<0||!dir)return;
	if(trim(fs.readfile(dir+'/stage')??'')==value)return;
	let now=time();write('stage_started',''+now);write('stage',value+'\n');
	if(fs.lstat(dir+'/timeline')?.type=='link')return;
	let log=fs.open(dir+'/timeline','a',0600);if(log){log.write(''+now+' '+value+'\n');log.close();}
};
export function failure(value){
	let dir=getenv('ADZERO_JOB_CONTEXT');if(!dir||messages[trim(fs.readfile(dir+'/reason')??'')])return;
	if(messages[value])write('reason',value);
};
// Numeric positions only: never publish subscription URLs, names or log text.
export function source(index,total){
	if(index==null){let dir=getenv('ADZERO_JOB_CONTEXT');if(dir&&fs.lstat(dir)?.type=='directory'&&fs.lstat(dir+'/source')?.type=='file')fs.unlink(dir+'/source');return;}
	if(type(index)=='int'&&type(total)=='int'&&index>=1&&index<=total&&total<=30)write('source',sprintf('%J',{index,total}));
};
export function source_reason(message){
	if(type(message)!='string')return null;
	if(index(message,'规则容量:')==0||index(message,'50 万')>=0||index(message,'25 MB')>=0)return 'source_limit';
	if(message=='待应用规则中存在重复项。'||message=='该订阅地址已存在。')return 'source_duplicate';
	return null;
};
