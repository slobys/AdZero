"""Native bridge invariants using production ucode, without router changes."""
import base64,json,os,shlex,subprocess,unittest,socket,socketserver,struct,tempfile,threading,time,http.server
from pathlib import Path
from test_dns_enhance import query
ROOT=Path(__file__).resolve().parents[1]
UCODE=shlex.split(os.environ.get('ADZERO_UCODE',''))
LAN=dict(device='br-lan',address='192.168.2.1',mask=24)
@unittest.skipUnless(UCODE,'requires real ucode')
class Native(unittest.TestCase):
 def call(self,op,args,wire=(),output=False,ok=True):
  args=list(args)
  for i in wire:args[i]=base64.b64encode(args[i]).decode()
  r=subprocess.run(UCODE+[str(ROOT/'scripts/dns_enhance_transform.uc')],input=json.dumps(dict(native=True,operation=op,args=args,wire=list(wire),output_wire=output)),text=True,capture_output=True)
  self.assertEqual(r.returncode==0,ok,r.stderr)
  if ok:return json.loads(r.stdout)
 def test_nailink_bridge_is_narrow_and_exactly_reversible(self):
  dns='routing { request { fallback: asis } }';traffic='fallback: direct\n'
  d=self.call('nai_dns',[dns,LAN]);t=self.call('nai_routing',[traffic,LAN])
  self.assertIn('tcp+udp://192.168.2.1:5355',d)
  self.assertIn('sip(192.168.2.1/24) && dip(192.168.2.1) && dport(53) -> must_direct',t)
  self.assertEqual(self.call('strip_dns',[d,dict(lan=LAN)]),dns)
  self.assertEqual(self.call('strip_routing',[t,dict(lan=LAN)]),traffic)
  self.call('nai_dns',['bind: "127.0.0.1:54"\n'+dns,LAN],ok=False)
  self.call('strip_dns',[dns,dict(lan=LAN)],ok=False)
 def test_mihomo_preserves_configuration_and_requires_native_path(self):
  config=dict(dns=dict(enable=True,listen='0.0.0.0:7874',**{'enhanced-mode':'fake-ip'}),secret='private',**{'external-controller':'0.0.0.0:9090'})
  result=self.call('mihomo',[config,LAN]);self.assertEqual(result['port'],7874)
  self.assertEqual(result['inspector'],dict(address='127.0.0.1',port=9090,secret='private'))
  for change in (dict(tun=dict(enable=True)),{'external-controller':'remote.example:9090'},dict(secret='x\r\nInjected: x')):
   self.call('mihomo',[config|change,LAN],ok=False)
  config['dns']['enhanced-mode']='redir-host';del config['external-controller']
  self.assertNotIn('inspector',self.call('mihomo',[config,LAN]))
 def test_real_resolver_api_alias_records_only(self):
  for kind in (1,28,64,65):
   q=query(kind=kind);v=dict(Status=0,TC=False,Question=[dict(Name='shop.example.',Qtype=kind,Qclass=1)],Answer=[dict(name='shop.example.',type=5,data='edge.example.'),dict(name='edge.example.',type=kind if kind in (64,65) else 5,data='0 ads.example.' if kind in (64,65) else 'ads.example.')])
   raw=base64.b64decode(self.call('api_answer',[q,v],[0],True))
   r=subprocess.run(UCODE+[str(ROOT/'scripts/dns_enhance_transform.uc')],input=json.dumps(dict(operation='decision',args=[base64.b64encode(q).decode(),base64.b64encode(raw).decode(),{'ads.example':True},[],None,True],wire=[0,1])),capture_output=True,text=True)
   self.assertEqual(r.returncode,0,r.stderr);self.assertTrue(json.loads(r.stdout)['blocked'])
 def test_api_question_mismatch_and_service_mode_do_not_block(self):
  q=query(kind=65);v=dict(Status=0,TC=False,Question=[dict(Name='shop.example.',Qtype=65,Qclass=1)],Answer=[dict(name='shop.example.',type=65,data='1 ads.example. alpn=h2')],Additional=[dict(name='shop.example.',type=5,data='ads.example.')])
  raw=base64.b64decode(self.call('api_answer',[q,v],[0],True));self.assertEqual(raw[6:8],b'\0\0')
  for change in (dict(Question=[dict(Name='other.example.',Qtype=65,Qclass=1)]),dict(Question=[]),dict(TC='false')):
   self.call('api_answer',[q,v|change],[0],ok=False)

 def test_client_subnet_context_is_not_replaced_by_an_api_question(self):
  q=query();option=struct.pack('!HH',8,7)+b'\0\1\x18\0\xc0\0\2'
  packet=q[:10]+b'\0\1'+q[12:]+b'\0'+struct.pack('!HHIH',41,1232,0,len(option))+option
  r=subprocess.run(UCODE+[str(ROOT/'scripts/dns_enhance_transform.uc')],input=json.dumps(dict(operation='message',args=[base64.b64encode(packet).decode()],wire=[0])),capture_output=True,text=True)
  self.assertEqual(r.returncode,0,r.stderr);self.assertFalse(json.loads(r.stdout)['api_safe'])

@unittest.skipUnless(UCODE,'requires real ucode/socket')
class HTTPRelay(unittest.TestCase):
 def test_async_private_api_chunked_exemptions_and_failure_preserve_fakeip(self):
  from urllib.parse import urlparse,parse_qs
  state=dict(chunked=False,error=False,delay=0,unrelated=False);requests=[]
  class Handler(http.server.BaseHTTPRequestHandler):
   def do_GET(self):
    requests.append(self.headers.get('Authorization'))
    if self.headers.get('Authorization')!='Bearer secret':self.send_error(401);return
    time.sleep(state['delay'])
    if state['error']:self.send_error(503);return
    args=parse_qs(urlparse(self.path).query);domain=args['name'][0];kind={'A':1,'AAAA':28,'SVCB':64,'HTTPS':65}[args['type'][0]]
    raw=json.dumps(dict(Status=0,TC=False,Question=[dict(Name=domain+'.',Qtype=kind,Qclass=1)],Answer=[dict(name='unrelated.example.' if state['unrelated'] else domain+'.',type=5,data='ads.example.')])).encode()
    try:
     self.send_response(200)
     self.send_header('Transfer-Encoding','chunked') if state['chunked'] else self.send_header('Content-Length',str(len(raw)))
     self.end_headers()
     if state['chunked']:
      for p in (raw[:17],raw[17:]):self.wfile.write(f'{len(p):x}\r\n'.encode()+p+b'\r\n')
      self.wfile.write(b'0\r\n\r\n')
     else:self.wfile.write(raw)
    except (OSError,ConnectionError):pass
   def log_message(self,*a):pass
  httpd=http.server.ThreadingHTTPServer(('127.0.0.1',0),Handler);httpd.daemon_threads=True
  self.addCleanup(httpd.server_close);self.addCleanup(httpd.shutdown)
  threading.Thread(target=httpd.serve_forever,daemon=True).start()
  def native_answer(q):return q[:2]+struct.pack('!5H',0x81a0,1,1,0,0)+q[12:]+b'\xc0\x0c'+struct.pack('!HHIH',1,1,60,4)+socket.inet_aton('198.18.0.2')
  class UDP(socketserver.BaseRequestHandler):
   def handle(self):q,s=self.request;s.sendto(native_answer(q),self.client_address)
  dns=socketserver.ThreadingUDPServer(('127.0.0.1',0),UDP);self.addCleanup(dns.server_close);self.addCleanup(dns.shutdown)
  threading.Thread(target=dns.serve_forever,daemon=True).start()
  with tempfile.TemporaryDirectory() as d:
   reserve=socket.socket();reserve.bind(('127.0.0.1',0));port=reserve.getsockname()[1];reserve.close()
   cfg=Path(d)/'config.json';config=dict(context=dict(lan=dict(address='127.0.0.1'),listen_port=port,port=dns.server_address[1],inspector=dict(address='127.0.0.1',port=httpd.server_port,secret='secret')),settings=dict(cname=True,devices=[]),rules={'ads.example':True},allowed=[])
   cfg.write_text(json.dumps(config));p=subprocess.Popen(UCODE+[str(ROOT/'scripts/dns_relay_fixture.uc'),str(cfg)],stdout=subprocess.PIPE,stderr=subprocess.PIPE,text=True)
   try:
    def configure():
     # The fixture reads between poll steps: never expose a truncated JSON
     # file, and wait for its acknowledgement instead of a fixed sleep.
     applied=Path(str(cfg)+'.applied');applied.unlink(missing_ok=True)
     staged=Path(str(cfg)+'.next');staged.write_text(json.dumps(config));staged.replace(cfg)
     deadline=time.monotonic()+2
     while not applied.exists():
      self.assertIsNone(p.poll(),'relay exited while applying fixture configuration')
      self.assertLess(time.monotonic(),deadline,'fixture configuration was not acknowledged')
      time.sleep(.01)
    check=subprocess.run(UCODE+[str(ROOT/'scripts/dns_http_fixture.uc')],input=json.dumps(dict(context=config['context'],query=base64.b64encode(query()).decode())),text=True,capture_output=True,timeout=4)
    self.assertEqual(check.returncode,0,check.stdout+check.stderr)
    time.sleep(.15)
    if p.poll() is not None:self.fail(p.communicate())
    def ask(domain='shop.example'):
     q=query(domain)
     with socket.socket(socket.AF_INET,socket.SOCK_DGRAM) as s:s.settimeout(2.5);s.sendto(q,('127.0.0.1',port));raw=s.recv(65535)
     return q,raw
    for chunked in (False,True):
     state['chunked']=chunked;q,r=ask();self.assertEqual(r[6:8],b'\0\0');self.assertFalse(r[3]&32)
    state['unrelated']=True;q,r=ask();self.assertEqual(r,native_answer(q));state['unrelated']=False
    config['allowed']=['shop.example'];configure()
    q,r=ask();self.assertEqual(r,native_answer(q))
    config['allowed']=[];configure()
    state['error']=True;q,r=ask();self.assertEqual(r,native_answer(q));state['error']=False
    state['delay']=2;started=time.monotonic();q,r=ask();self.assertEqual(r,native_answer(q));self.assertLess(time.monotonic()-started,2)
    self.assertTrue(requests);self.assertEqual(set(requests),{'Bearer secret'})
   finally:
    p.terminate();out,err=p.communicate(timeout=5)
    if err:print(err)
   self.assertNotIn('secret',err);self.assertNotIn('Type error',err)
