"""Native PassWall DNS helper and rc.common in a private Linux namespace.

Real ucode/UCI and dnsmasq. API config/node callbacks are fixtures, upstream
domain normalizer and DNS generator are pinned bytes. No proxy/firewall test.
"""
from pathlib import Path
import os,tarfile,io,hashlib,json,subprocess,tempfile,shutil,sys,socket,struct,threading,socketserver,time

import urllib.request
from check_openwrt_runtime import ROOT, SHA256, URL
from prepare_dns_socket import install as install_socket, prepare as prepare_socket
from prepare_timeout import install as install_timeout, prepare as prepare_timeout
sys.path.insert(0,str(ROOT/'tests'))
from test_observe import dns

def main():
 archive=ROOT/'artifacts/runtime/openwrt-24.10.8-x86-64-rootfs.tar.gz'
 helper=ROOT/'artifacts/upstream/PassWall/helper_dnsmasq.lua';api=ROOT/'artifacts/upstream/PassWall/api.lua'
 helper2=ROOT/'artifacts/upstream/Passwall2/helper_dnsmasq.lua'
 deps={'lua_5.1.5-r11_x86_64.ipk':'164795e16b225d71e776a5aa584f6b3af239a0012724da63de58dd808f6a3165','liblua5.1.5_5.1.5-r11_x86_64.ipk':'53875655f43c24dc00fe3f94a1a9e15294be48b80ee32d9fd5d6cddc65b01a70'}
 if '--inside' not in sys.argv:
  if os.geteuid()!=0:raise SystemExit('Run in Linux/WSL as root')
  prepare_socket(ROOT,None)
  # Fetch before unshare, so cold CI caches work without a network inside it.
  def fetch(path,url,digest):
   path.parent.mkdir(parents=True,exist_ok=True)
   raw=path.read_bytes() if path.exists() else urllib.request.urlopen(url,timeout=60).read()
   assert hashlib.sha256(raw).hexdigest()==digest,path
   if not path.exists():path.write_bytes(raw)
  fetch(archive,URL,SHA256)
  ref='ae258e4da46aa34567230f55e401ced4cb7738ad'
  prefix='https://raw.githubusercontent.com/Openwrt-Passwall/openwrt-passwall/'+ref+'/luci-app-passwall/'
  fetch(helper,prefix+'root/usr/share/passwall/helper_dnsmasq.lua','e34aa1f98ecebf38ea691b90b51e3b73ebafebf20bb6f44efaa272a9fc46db81')
  fetch(api,prefix+'luasrc/passwall/api.lua','dfcc760005ec18681b72c9371344247034430d905d40fdafcfef93bcf5f3a7c4')
  fetch(helper2,'https://raw.githubusercontent.com/Openwrt-Passwall/openwrt-passwall2/6e06876b7140a6451139dd8116635f80897ec3b3/luci-app-passwall2/root/usr/share/passwall2/helper_dnsmasq.lua','204ac1202b83ef571579ed21960a85ed5971a12a013c3da7159756eee96726bc')
  fetch(ROOT/'artifacts/upstream/SSRPlus/gfw2ipset.sh','https://raw.githubusercontent.com/fw876/helloworld/2abdc9aa9e19607774dc9789709212b70b8607c6/luci-app-ssr-plus/root/usr/share/shadowsocksr/gfw2ipset.sh','2d456af581fcca037e259c285c202f564bfdba00057186f95d626e0db2700309')
  for name,digest in deps.items():fetch(ROOT/'artifacts/runtime/passwall-extras'/name,'https://archive.openwrt.org/releases/24.10.8/packages/x86_64/base/'+name,digest)
  prepare_timeout(ROOT)
  return subprocess.call(['unshare','--net','--mount','--propagation','private',sys.executable,__file__,'--inside'])
 subprocess.run(['ip','link','set','lo','up'],check=True)
 assert hashlib.sha256(archive.read_bytes()).hexdigest()=='6c94190246a54088e3984fc0c4cbbb518972abb2b10a28e30a0c41554eea8b9d'
 assert hashlib.sha256(helper.read_bytes()).hexdigest()=='e34aa1f98ecebf38ea691b90b51e3b73ebafebf20bb6f44efaa272a9fc46db81'
 assert hashlib.sha256(api.read_bytes()).hexdigest()=='dfcc760005ec18681b72c9371344247034430d905d40fdafcfef93bcf5f3a7c4'
 with tempfile.TemporaryDirectory(prefix='adzero-passwall-native-') as tmp:
  base=Path(tmp)
  with tarfile.open(archive) as t:t.extractall(base,filter='fully_trusted')
  install_socket(ROOT,base)
  install_timeout(ROOT,base)
  for name,digest in deps.items():
   p=ROOT/'artifacts/runtime/passwall-extras'/name;assert hashlib.sha256(p.read_bytes()).hexdigest()==digest
   with tarfile.open(p) as outer:
    member=next(m for m in outer.getmembers() if m.name.endswith('data.tar.gz'))
    with tarfile.open(fileobj=io.BytesIO(outer.extractfile(member).read())) as t:t.extractall(base,filter='fully_trusted')
  for name in ('tmp/etc/passwall_tmp','tmp/etc/passwall/acl/default','usr/share/passwall/rules','etc/passwall/rules','usr/lib/lua/luci/passwall','etc/adzero','var/run/adblock-fast','proc','dev'):(base/name).mkdir(parents=True,exist_ok=True)
  os.mknod(base/'dev/null',0o666|0o20000,os.makedev(1,3));os.mknod(base/'dev/urandom',0o666|0o20000,os.makedev(1,9))
  subprocess.run(['mount','--bind','/proc',str(base/'proc')],check=True)
  try:
   shutil.copytree(ROOT/'luci-app-adzero/root/usr/libexec/adzero',base/'usr/libexec/adzero',dirs_exist_ok=True)
   for f in (base/'usr/libexec/adzero').iterdir():
    if f.is_file():f.chmod(0o755)
   (base/'etc/config/adzero').write_text("config adzero 'main'\n option enabled '1'\n")
   (base/'etc/config/adblock-fast').write_text("config adblock-fast 'config'\n option dns 'dnsmasq.servers'\n")
   (base/'etc/config/dhcp').write_text("config dnsmasq 'fixture'\n")
   (base/'var/etc').mkdir(parents=True,exist_ok=True)
   (base/'var/etc/dnsmasq.conf.fixture').write_text('no-hosts\nuser=root\n')
   (base/'etc/config/passwall').write_text("config global 'main'\n option enabled '1'\n option use_block_list '0'\n")
   (base/'etc/init.d/passwall').write_text('#!/bin/sh\nexit 0\n')
   (base/'var/run/adblock-fast/dnsmasq.servers').write_text('server=/doubleclick.net/\nserver=/ads.example/\n')
   (base/'tmp/inventory.json').write_text(json.dumps({'plugins':[{'id':'passwall','installed':True,'enabled':True,'runtime':'running'}]}))
   env={**os.environ,'ADZERO_ADAPTER_INVENTORY':'/tmp/inventory.json'}
   def run(*args):
    p=subprocess.run(['chroot',tmp,*args],env=env,text=True,capture_output=True,timeout=20)
    assert p.returncode==0,p.stdout+p.stderr
    return p
   assert run('/usr/bin/ucode','-e',"print(system(['/usr/libexec/timeout-coreutils','-k','1','1','/bin/sleep','3']));").stdout.strip()=='124'
   run('/usr/bin/ucode','-S','/usr/libexec/adzero/adapter.uc','--','apply')
   assert 'doubleclick.net' in (base/'etc/passwall/rules/block_host').read_text()
   shutil.copyfile(helper,base/'usr/share/passwall/helper_dnsmasq.lua')
   raw=api.read_text();native_std=raw[raw.index('function get_std_domain(domain)'):raw.index('\nfunction ',raw.index('function get_std_domain(domain)')+1)]
   stub=r'''
local a={CACHE_PATH='/tmp/etc/passwall_tmp'}
local function q(s)return "'"..s:gsub("'","'\\''").."'" end
local function exec(s)local p=io.popen(s);local r=p:read('*a');p:close();return r end
a.sys={exec=exec,call=os.execute}
a.fs={access=function(s)local f=io.open(s);if f then f:close();return true end return false end,
 mkdir=function(s)return os.execute('mkdir -p '..q(s))==0 end,
 symlink=function(s,t)return os.execute('ln -s '..q(s)..' '..q(t))==0 end}
a.datatypes={hostname=function(s)return s:find('%.')~=nil end}
a.remove=function(s)os.execute('rm -rf '..s)end
a.md5_string=function(s)return exec("printf '%s' "..q(s)..' | md5sum'):match('^%x+')end
a.uci_get_c=function()return nil end
a.uci_foreach_c=function()end
a.is_finded=function()return false end
a.vps_domain_exclude=function()return false end
a.log=function()end
a.set_cache_var=function()end
function trim(s)return (s:gsub('^%s*(.-)%s*$','%1'))end
'''+native_std+"\na.get_std_domain=get_std_domain\nreturn a\n"
   (base/'usr/lib/lua/luci/passwall/api.lua').write_text(stub)
   service=base/'etc/init.d/passwall'
   service.write_text('#!/bin/sh /etc/rc.common\nrestart() {\n echo restart >> /tmp/dispatched\n rm -f /tmp/etc/passwall/acl/default/dnsmasq.conf\n lua /tmp/generate.lua\n}\n')
   run('/bin/sh','/etc/init.d/passwall','restart')
   assert not (base/'tmp/dispatched').exists(), 'Old call must reproduce false success'
   binary=base/'tmp/etc/passwall/bin/dnsmasq_default';binary.parent.mkdir(parents=True)
   binary.symlink_to('/usr/sbin/dnsmasq')
   class Upstream(socketserver.BaseRequestHandler):
    def handle(self):
     packet,sock=self.request;p=12
     while packet[p]:p+=packet[p]+1
     p+=1;kind=struct.unpack('!H',packet[p:p+2])[0]
     address=socket.inet_pton(socket.AF_INET if kind==1 else socket.AF_INET6,'1.2.3.4' if kind==1 else '2001:db8::1')
     response=packet[:2]+struct.pack('!HHHHH',0x8180,1,1,0,0)+packet[12:p+4]+b'\xc0\x0c'+struct.pack('!HHIH',kind,1,60,len(address))+address
     sock.sendto(response,self.client_address)
   upstream=socketserver.ThreadingUDPServer(('127.0.0.1',0),Upstream);threading.Thread(target=upstream.serve_forever,daemon=True).start()
   up='127.0.0.1#'+str(upstream.server_address[1]);results=[]
   def query(port,name,kind=1,tcp=False):
    with socket.socket(socket.AF_INET,socket.SOCK_STREAM if tcp else socket.SOCK_DGRAM) as s:
     s.settimeout(2);s.connect(('127.0.0.1',port));packet=dns(name,qtype=kind)
     s.sendall(struct.pack('!H',len(packet))+packet if tcp else packet)
     if tcp:
      size=struct.unpack('!H',s.recv(2))[0];r=b''
      while len(r)<size:r+=s.recv(size-len(r))
     else:r=s.recv(4096)
     return {'rcode':r[3]&15,'answers':struct.unpack('!H',r[6:8])[0]}
   cases=[('plain',None,None),('same-direct','direct_host','doubleclick.net'),('same-proxy','proxy_host','doubleclick.net'),('child-direct','direct_host','sub.doubleclick.net'),('same-cn','chnlist','doubleclick.net')]
   for n,(label,file,domain) in enumerate(cases):
    for name in ('block_ip','direct_host','proxy_host'):(base/'etc/passwall/rules'/name).write_text('')
    for name in ('chnlist','gfwlist'):(base/'usr/share/passwall/rules'/name).write_text('')
    if file:(base/('usr/share/passwall/rules' if file=='chnlist' else 'etc/passwall/rules')/file).write_text(domain+'\n')
    var={'-FLAG':label,'-TMP_DNSMASQ_PATH':'/tmp/etc/passwall/acl/default/dnsmasq.d','-DNSMASQ_CONF_FILE':'/tmp/etc/passwall/acl/default/dnsmasq.conf','-LISTEN_PORT':'11400','-LOCAL_DNS':up,'-TUN_DNS':up,'-DEFAULT_DNS':up,'-USE_DEFAULT_DNS':'remote','-CHINADNS_DNS':'0','-NODE':'chosen','-USE_DIRECT_LIST':'1','-USE_PROXY_LIST':'1','-USE_BLOCK_LIST':'1','-USE_GFW_LIST':'0','-CHN_LIST':'direct','-DEFAULT_PROXY_MODE':'proxy','-NO_PROXY_IPV6':'0','-NFTFLAG':'0','-NO_LOGIC_LOG':'1'}
    (base/'tmp/etc/passwall/acl'/label).mkdir(parents=True)
    literal='{'+','.join('['+json.dumps(k)+']='+json.dumps(v) for k,v in var.items())+'}'
    (base/'tmp/generate.lua').write_text("dofile('/usr/share/passwall/helper_dnsmasq.lua')\nadd_rule("+literal+")\n")
    if n==0:
     env['ADZERO_ADAPTER_REFRESH']='1'
     run('/usr/bin/ucode','-S','/usr/libexec/adzero/adapter.uc','--','apply')
     assert len((base/'tmp/dispatched').read_text().splitlines())==2
    else:run('/bin/sh','/etc/rc.common','/etc/init.d/passwall','restart')
    cache=base/'tmp/etc/passwall_tmp'/('dnsmasq_'+label)
    directives=[]
    for f in cache.glob('*.conf'):
     directives.extend(l for l in f.read_text().splitlines() if l.startswith(('address=','server=')))
     f.write_text('\n'.join(l for l in f.read_text().splitlines() if not l.startswith(('ipset=','nftset=')))+'\n')
    port=11400
    proc=subprocess.Popen(['chroot',tmp,'/tmp/etc/passwall/bin/dnsmasq_default','--no-daemon','-C','/tmp/etc/passwall/acl/default/dnsmasq.conf'],stdout=subprocess.DEVNULL,stderr=subprocess.PIPE,text=True)
    try:
     time.sleep(.12)
     assert proc.poll() is None,proc.stderr.read()
     if n==0:
      status=json.loads(run('/usr/bin/ucode','-S','/usr/libexec/adzero/adapter.uc','--','status').stdout)
      assert status['runtime']['state']=='ready',status
     values={name:query(port,name) for name in ('doubleclick.net','sub.doubleclick.net','www.baidu.com')}
     assert values['doubleclick.net']=={'rcode':3,'answers':0}
     assert values['www.baidu.com']=={'rcode':0,'answers':1}
     assert values['sub.doubleclick.net']['rcode']==(0 if label=='child-direct' else 3)
     if n==0:
      for tcp in (False,True):
       for kind in (1,28):
        for name in values:
         answer=query(port,name,kind,tcp)
         assert answer==({'rcode':0,'answers':1} if name=='www.baidu.com' else {'rcode':3,'answers':0}),answer
      before=(base/'tmp/dispatched').read_text()
      run('/usr/bin/ucode','-S','/usr/libexec/adzero/adapter.uc','--','apply')
      assert (base/'tmp/dispatched').read_text()==before
     results.append({'case':label,'directives':directives,'queries':values})
    finally:proc.terminate();proc.wait(timeout=3)
   # Additional native adapter: upstream SSR Plus+ gfw2ipset generator. Its
   # environment and service body are fixtures, DNS generator bytes are not.
   run('/usr/bin/ucode','-S','/usr/libexec/adzero/adapter.uc','--','remove')
   (base/'tmp/inventory.json').write_text(json.dumps({'plugins':[{'id':'ssrplus','installed':True,'enabled':True,'runtime':'running'}]}))
   (base/'etc/config/shadowsocksr').write_text("config global 'main'\n option global_server 'chosen'\n option run_mode 'router'\nconfig servers 'chosen'\n option type 'ss'\n")
   (base/'usr/share/shadowsocksr').mkdir(parents=True)
   shutil.copyfile(ROOT/'artifacts/upstream/SSRPlus/gfw2ipset.sh',base/'usr/share/shadowsocksr/gfw2ipset.sh')
   (base/'etc/ssrplus').mkdir()
   for name in ['black.list','white.list','netflix.list','oversea_list.conf','gfw_base.conf']:(base/'etc/ssrplus'/name).write_text('')
   (base/'etc/ssrplus/gfw_list.conf').write_text('server=/sub.doubleclick.net/'+up+'\n')
   (base/'etc/ssrplus/deny.list').write_text('user.example\n')
   (base/'etc/init.d/shadowsocksr').write_text('''#!/bin/sh /etc/rc.common
TMP_DNSMASQ_PATH=/tmp/dnsmasq-ssrplus.d
check_run_environment() { USE_TABLES=iptables; }
echolog() { :; }
uci_get_by_type() { uci -q get "shadowsocksr.@$1[0].$2" || echo "$3"; }
restart() {
 echo restart >> /tmp/ssr-dispatched
 mkdir -p /tmp/dnsmasq.fixture.d
 echo conf-dir=/tmp/dnsmasq-ssrplus.d > /tmp/dnsmasq.fixture.d/dnsmasq-ssrplus.conf
 printf 'no-hosts\\nuser=root\\nconf-dir=/tmp/dnsmasq.fixture.d\\n' > /var/etc/dnsmasq.conf.fixture
 sh /usr/share/shadowsocksr/gfw2ipset.sh
}
''')
   run('/usr/bin/ucode','-S','/usr/libexec/adzero/adapter.uc','--','apply')
   assert len((base/'tmp/ssr-dispatched').read_text().splitlines())==2
   proc=subprocess.Popen(['chroot',tmp,'/usr/sbin/dnsmasq','--no-daemon','-C','/var/etc/dnsmasq.conf.fixture','--port=11400','--server='+up],stdout=subprocess.DEVNULL,stderr=subprocess.PIPE,text=True)
   try:
    time.sleep(.12);assert proc.poll() is None,proc.stderr.read()
    status=json.loads(run('/usr/bin/ucode','-S','/usr/libexec/adzero/adapter.uc','--','status').stdout)
    assert status['runtime']['state']=='ready',status
    for tcp in (False,True):
     for kind in (1,28):
      for name in ('doubleclick.net','sub.doubleclick.net','www.baidu.com'):
       answer=query(11400,name,kind,tcp)
       assert answer==({'rcode':0,'answers':1} if name=='www.baidu.com' else {'rcode':3,'answers':0}),answer
    before=(base/'tmp/ssr-dispatched').read_text()
    run('/usr/bin/ucode','-S','/usr/libexec/adzero/adapter.uc','--','apply')
    assert (base/'tmp/ssr-dispatched').read_text()==before
   finally:proc.terminate();proc.wait(timeout=3)
   run('/usr/bin/ucode','-S','/usr/libexec/adzero/adapter.uc','--','remove')
   assert (base/'etc/ssrplus/deny.list').read_text()=='user.example\n'
   # Execute the pinned PassWall2 copy_instance with the same lightweight LuCI
   # IO adapter, then query the real copied native dnsmasq over UDP and TCP.
   native2=helper2.read_text()
   assert hashlib.sha256(helper2.read_bytes()).hexdigest()=='204ac1202b83ef571579ed21960a85ed5971a12a013c3da7159756eee96726bc'
   (base/'usr/lib/lua/luci/passwall2').mkdir(parents=True)
   (base/'usr/lib/lua/luci/passwall2/api.lua').write_text(stub)
   (base/'usr/share/passwall2').mkdir(parents=True)
   shutil.copyfile(helper2,base/'usr/share/passwall2/helper_dnsmasq.lua')
   prefix2=native2[:native2.index('local function backup_servers()')]
   copy2=native2[native2.index('function copy_instance('):native2.index('\nfunction add_rule(')]
   (base/'var/etc/dnsmasq.conf.fixture').write_text('no-hosts\nuser=root\nno-resolv\nservers-file=/var/run/adblock-fast/dnsmasq.servers\n')
   generator=prefix2+copy2+"\nprint(table.concat(copy_instance({LISTEN_PORT=11400,['return']='1'}),'\\n'))\n"
   (base/'tmp/passwall2-copy.lua').write_text(generator)
   config2='/tmp/etc/passwall2/acl/acl_default_dnsmasq.conf'
   (base/Path(config2).relative_to('/')).parent.mkdir(parents=True)
   (base/Path(config2).relative_to('/')).write_text(run('/usr/bin/lua','/tmp/passwall2-copy.lua').stdout)
   binary2=base/'tmp/etc/passwall2/bin/dnsmasq_acl_default';binary2.parent.mkdir(parents=True);binary2.symlink_to('/usr/sbin/dnsmasq')
   (base/'etc/config/passwall2').write_text("config global 'main'\n option enabled '1'\n option dns_redirect '1'\n")
   (base/'etc/init.d/passwall2').write_text('#!/bin/sh\nexit 0\n')
   (base/'tmp/inventory.json').write_text(json.dumps({'plugins':[{'id':'passwall2','installed':True,'active':True,'runtime':'running'}]}))
   proc=subprocess.Popen(['chroot',tmp,'/tmp/etc/passwall2/bin/dnsmasq_acl_default','--no-daemon','-C',config2,'--server='+up],stdout=subprocess.DEVNULL,stderr=subprocess.PIPE,text=True)
   try:
    time.sleep(.12);assert proc.poll() is None,proc.stderr.read()
    run('/usr/bin/ucode','-S','/usr/libexec/adzero/adapter.uc','--','apply')
    state2=json.loads(run('/usr/bin/ucode','-S','/usr/libexec/adzero/adapter.uc','--','status').stdout)
    assert state2['runtime']['state']=='ready',state2
    for tcp in (False,True):
     for kind in (1,28):
      for name in ('doubleclick.net','sub.doubleclick.net','www.baidu.com'):
       answer=query(11400,name,kind,tcp)
       assert answer=={'rcode':0,'answers':1} if name=='www.baidu.com' else answer['answers']==0 and answer['rcode'] in (0,3),(name,tcp,kind,answer)
    run('/usr/bin/ucode','-S','/usr/libexec/adzero/adapter.uc','--','remove')
   finally:proc.terminate();proc.wait(timeout=3)
   upstream.shutdown();upstream.server_close()
   result={'old_call_false_success':True,'native_rc_common':True,'udp_tcp_A_AAAA':24,'ssrplus_native_dns':True,'passwall2_native_dns_queries':12,'cases':results,'device_acceptance':'not_run'}
   print(json.dumps(result,indent=2));(ROOT/'artifacts/runtime/passwall-runtime-result.json').write_text(json.dumps(result,indent=2))
  finally:subprocess.run(['umount',str(base/'proc')],check=True)
 return 0
if __name__=='__main__':raise SystemExit(main())
