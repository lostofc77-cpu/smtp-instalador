#!/usr/bin/env python3
# Painel de Faturas v4 - porta 25, HTML com mes automatico, lote
import json, os, random, re, secrets, smtplib, subprocess, threading, time, datetime
from email import policy
from email.message import EmailMessage
from email.parser import BytesParser
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from urllib.parse import urlparse, parse_qs

PORT = int(os.environ.get("PAINEL_PORT", "8080"))
ACCESS_CODE = os.environ.get("PAINEL_CODE", "ALTERE-ESTE-CODIGO")
MAIL_DOMAIN = "cardfatpromo.com"
FROM_ADDR = "faturas@" + MAIL_DOMAIN
FROM_NAME = "Sua Fatura do Mes Chegou"
SESSIONS = set()
JOBS = {}
MAX_UPLOAD = 5 * 1024 * 1024
MAX_RCPTS = 200
MAX_BODY = 60000
PAUSE_S = 2
EMAIL_RE = re.compile(r"^[^@\s]+@[^@\s]+\.[^@\s]+$")
MESES = ["janeiro", "fevereiro", "março", "abril", "maio", "junho",
         "julho", "agosto", "setembro", "outubro", "novembro", "dezembro"]

DATA_DIR = os.environ.get("PAINEL_DATA", "/root/painel")
TEMPLATES_FILE = os.path.join(DATA_DIR, "templates.json")
STATS_FILE = os.path.join(DATA_DIR, "stats.json")
os.makedirs(DATA_DIR, exist_ok=True)
FILE_LOCK = threading.Lock()

def load_json(path, default):
    try:
        with open(path, encoding="utf-8") as f:
            return json.load(f)
    except Exception:
        return default

def save_json(path, data):
    with FILE_LOCK:
        tmp = path + ".tmp"
        with open(tmp, "w", encoding="utf-8") as f:
            json.dump(data, f, ensure_ascii=False)
        os.replace(tmp, path)

def bump_stats(ok):
    st = load_json(STATS_FILE, {"total_enviados": 0, "total_falhas": 0, "ultimo": ""})
    if ok:
        st["total_enviados"] += 1
    else:
        st["total_falhas"] += 1
    st["ultimo"] = datetime.datetime.now().isoformat(timespec="seconds")
    save_json(STATS_FILE, st)

def mes_ano():
    h = datetime.date.today()
    return MESES[h.month - 1], str(h.year)

def data_dia():
    h = datetime.date.today()
    return h.strftime("%d/%m/%Y"), str(h.day)

def nova_chave():
    return str(random.randint(100000000, 999999999))

def sh(cmd):
    try:
        r = subprocess.run(cmd, capture_output=True, text=True, timeout=20)
        return (r.stdout + r.stderr).strip()
    except Exception as e:
        return "erro: " + str(e)

def get_status():
    services = {s: sh(["systemctl", "is-active", s]) == "active"
                for s in ("postfix", "dovecot", "opendkim")}
    q = sh(["postqueue", "-p"])
    m = re.search(r"(\d+) Requests", q)
    queue = m.group(1) if m else "0"
    try:
        with open("/var/log/mail.log", errors="ignore") as f:
            log = f.readlines()[-300:]
    except Exception:
        log = []
    def tail(status, n=6):
        out = []
        for line in reversed(log):
            if "status=" + status in line:
                to = re.search(r"to=<([^>]+)>", line)
                out.append(to.group(1) if to else line.strip()[-60:])
                if len(out) >= n: break
        return out
    cert_days = None
    pem = "/etc/letsencrypt/live/mail." + MAIL_DOMAIN + "/fullchain.pem"
    if os.path.exists(pem):
        mm = re.search(r"notAfter=(.+)", sh(["openssl", "x509", "-noout", "-enddate", "-in", pem]))
        if mm:
            try:
                d = datetime.datetime.strptime(mm.group(1).strip(), "%b %d %H:%M:%S %Y %Z")
                cert_days = (d - datetime.datetime.utcnow()).days
            except Exception:
                pass
    return {"services": services, "queue": queue,
            "sent": tail("sent"), "bounced": tail("bounced"), "deferred": tail("deferred"),
            "cert_days": cert_days,
            "stats": load_json(STATS_FILE, {"total_enviados": 0, "total_falhas": 0, "ultimo": ""})}

def nome_do_email(e):
    local = e.split("@")[0]
    primeiro = re.split(r"[._\-\+0-9]+", local)[0] or local
    return primeiro[:20].capitalize()

def parse_recipients(raw):
    out, seen = [], set()
    for line in raw.splitlines():
        line = line.strip()
        if not line or line.startswith("#"):
            continue
        parts = [p.strip() for p in re.split(r"[;,\t]+", line) if p.strip()]
        emails = [p for p in parts if EMAIL_RE.match(p)]
        nome = " ".join(p for p in parts if not EMAIL_RE.match(p))
        for e in emails:
            if e.lower() in seen:
                continue
            seen.add(e.lower())
            out.append((nome or nome_do_email(e), e))
    return out

def send_one(to, subject, body_html, pdf_name, pdf_bytes):
    msg = EmailMessage()
    msg["From"] = FROM_NAME + " <" + FROM_ADDR + ">"
    msg["To"] = to
    msg["Subject"] = subject
    plain = re.sub(r"\s+", " ", re.sub(r"<[^>]+>", " ", body_html)).strip() or "Sua fatura"
    msg.set_content(plain)
    msg.add_alternative(body_html, subtype="html")
    if pdf_bytes:
        msg.add_attachment(pdf_bytes, maintype="application", subtype="pdf", filename=pdf_name)
    with smtplib.SMTP("127.0.0.1", 25, timeout=90) as s:
        s.send_message(msg)

def start_job(recipients, subject, body_html, pdf_name, pdf_bytes):
    jid = secrets.token_hex(8)
    job = {"total": len(recipients), "done": 0, "results": [], "running": True}
    if len(JOBS) > 30:
        for k in sorted(JOBS)[:-30]:
            JOBS.pop(k, None)
    JOBS[jid] = job
    mes, ano = mes_ano()
    data_str, dia = data_dia()
    def aplica(txt, nome, to, chave):
        return (txt.replace("{{nome}}", nome).replace("{{email}}", to)
                   .replace("{{mes}}", mes).replace("{{ano}}", ano)
                   .replace("{{data}}", data_str).replace("{{dia}}", dia)
                   .replace("{{chave}}", chave))
    def worker():
        for nome, to in recipients:
            chave = nova_chave()
            try:
                send_one(to, aplica(subject, nome, to, chave), aplica(body_html, nome, to, chave),
                         pdf_name, pdf_bytes)
                job["results"].append([to, True, ""])
                bump_stats(True)
            except Exception as e:
                job["results"].append([to, False, str(e)[:150]])
                bump_stats(False)
            job["done"] += 1
            if job["done"] < job["total"]:
                time.sleep(PAUSE_S)
        job["running"] = False
    threading.Thread(target=worker, daemon=True).start()
    return jid

PAGE = """<!doctype html><html lang="pt-br"><head><meta charset="utf-8">
<meta name="viewport" content="width=device-width,initial-scale=1">
<title>Painel de Faturas</title>
<link href="https://fonts.googleapis.com/css2?family=IBM+Plex+Mono:wght@400;500;700&display=swap" rel="stylesheet">
<style>
:root{--bg:#0a0d10;--panel:#12161c;--line:#232a33;--txt:#c9d4df;--dim:#6b7784;
--amber:#ffb020;--ok:#3ddc84;--bad:#ff5252;--mono:'IBM Plex Mono',ui-monospace,monospace}
*{box-sizing:border-box;margin:0}
body{background:var(--bg);color:var(--txt);font-family:var(--mono);font-size:14px;
background-image:linear-gradient(var(--line) 1px,transparent 1px),linear-gradient(90deg,var(--line) 1px,transparent 1px);
background-size:44px 44px}
.login{min-height:100vh;display:flex;align-items:center;justify-content:center}
.card{width:340px;text-align:center;background:var(--panel);border:1px solid var(--line);
border-top:2px solid var(--amber);padding:18px}
h2{font-size:12px;letter-spacing:2px;color:var(--amber);margin-bottom:6px;text-transform:uppercase}
label{display:block;color:var(--dim);font-size:11px;margin:12px 0 5px;letter-spacing:1px}
input{width:100%;background:#0d1116;border:1px solid var(--line);color:var(--txt);font:inherit;
padding:9px 10px;outline:none;text-align:center;letter-spacing:4px;font-size:16px}
input:focus{border-color:var(--amber)}
.btn{margin-top:16px;width:100%;background:var(--amber);color:#161000;border:none;font:inherit;
font-weight:700;letter-spacing:2px;padding:12px;cursor:pointer;text-transform:uppercase}
.btn:hover{filter:brightness(1.12)}
</style></head><body>
<div class="login"><div class="card"><h2>Painel de Faturas</h2>
<form method="post" action="/login"><label>Código de acesso</label>
<input name="code" type="password" autofocus autocomplete="off">
<button class="btn">Entrar</button></form></div></div>
</body></html>"""

DASH = """<!doctype html><html lang="pt-br"><head><meta charset="utf-8">
<meta name="viewport" content="width=device-width,initial-scale=1">
<title>Painel de Faturas</title>
<link href="https://fonts.googleapis.com/css2?family=IBM+Plex+Mono:wght@400;500;700&display=swap" rel="stylesheet">
<style>
:root{--bg:#0a0d10;--panel:#12161c;--line:#232a33;--txt:#c9d4df;--dim:#6b7784;
--amber:#ffb020;--ok:#3ddc84;--bad:#ff5252;--mono:'IBM Plex Mono',ui-monospace,monospace}
*{box-sizing:border-box;margin:0}
body{background:var(--bg);color:var(--txt);font-family:var(--mono);font-size:14px;
background-image:linear-gradient(var(--line) 1px,transparent 1px),linear-gradient(90deg,var(--line) 1px,transparent 1px);
background-size:44px 44px}
body::after{content:'';position:fixed;inset:0;pointer-events:none;
background:radial-gradient(ellipse at 50% -10%,rgba(255,176,32,.07),transparent 55%)}
.wrap{max-width:1060px;margin:0 auto;padding:26px 20px 60px;position:relative}
header{display:flex;justify-content:space-between;align-items:flex-end;border-bottom:2px solid var(--amber);
padding-bottom:14px;margin-bottom:22px}
h1{font-size:17px;letter-spacing:2px;color:var(--amber);font-weight:700}
h1 .cursor{animation:bl 1s steps(1) infinite}
@keyframes bl{50%{opacity:0}}
.sub{color:var(--dim);font-size:11px;margin-top:4px;letter-spacing:1px}
button.link{background:none;border:none;color:var(--dim);font:inherit;cursor:pointer;text-decoration:underline}
.chips{display:flex;gap:10px;flex-wrap:wrap;margin-bottom:22px}
.chip{background:var(--panel);border:1px solid var(--line);padding:9px 14px;min-width:112px;flex:1}
.chip .k{color:var(--dim);font-size:10px;letter-spacing:1px;text-transform:uppercase}
.chip .v{margin-top:5px;font-weight:700;display:flex;align-items:center;gap:7px;font-size:13px}
.dot{width:8px;height:8px;border-radius:50%;background:var(--bad);box-shadow:0 0 8px var(--bad)}
.dot.on{background:var(--ok);box-shadow:0 0 8px var(--ok)}
.dot.warn{background:var(--amber);box-shadow:0 0 8px var(--amber)}
.grid{display:grid;grid-template-columns:1.15fr .85fr;gap:18px}
@media(max-width:880px){.grid{grid-template-columns:1fr}}
.card{background:var(--panel);border:1px solid var(--line);border-top:2px solid var(--amber);padding:18px}
.card h2{font-size:12px;letter-spacing:2px;color:var(--amber);margin-bottom:6px;text-transform:uppercase}
label{display:block;color:var(--dim);font-size:11px;margin:12px 0 5px;letter-spacing:1px}
.cnt{color:var(--amber)}
input,textarea{width:100%;background:#0d1116;border:1px solid var(--line);color:var(--txt);
font:inherit;padding:9px 10px;outline:none}
input:focus,textarea:focus{border-color:var(--amber)}
input[type=file]{padding:7px}
textarea{resize:vertical}
#corpo{min-height:200px;font-size:12px}
#lista{min-height:130px}
#prev{width:100%;height:300px;background:#fff;border:1px solid var(--line)}
.prow{display:flex;justify-content:space-between;align-items:center;margin:12px 0 5px}
.prow label{margin:0}
.mini{background:none;border:1px solid var(--amber);color:var(--amber);font:inherit;font-size:10px;
letter-spacing:1px;padding:5px 12px;cursor:pointer;text-transform:uppercase}
.mini:hover{background:var(--amber);color:#161000}
.btn{margin-top:16px;width:100%;background:var(--amber);color:#161000;border:none;font:inherit;
font-weight:700;letter-spacing:2px;padding:12px;cursor:pointer;text-transform:uppercase}
.btn:hover{filter:brightness(1.12)}
#retry{background:var(--bad);color:#fff;display:none}
.bar{height:10px;background:#0d1116;border:1px solid var(--line);margin-top:16px}
#barf{height:100%;width:0;background:var(--amber);transition:width .5s}
.ptxt{font-size:11px;color:var(--dim);margin-top:6px;letter-spacing:1px}
#res{margin-top:12px;font-size:12px;padding:10px;border:1px dashed var(--line);display:none;white-space:pre-wrap}
#res.ok{border-color:var(--ok);color:var(--ok)}#res.bad{border-color:var(--bad);color:var(--bad)}
.logbox h3{font-size:11px;letter-spacing:2px;margin:16px 0 8px;text-transform:uppercase}
.logbox h3.ok{color:var(--ok)}.logbox h3.bad{color:var(--bad)}.logbox h3.warn{color:var(--amber)}
ul{list-style:none;max-height:220px;overflow:auto}
li{padding:5px 8px;border-left:2px solid var(--line);margin-bottom:5px;font-size:12px;color:var(--dim);
background:#0d1116;word-break:break-all}
li.ok{border-color:var(--ok)}li.bad{border-color:var(--bad)}li.warn{border-color:var(--amber)}
.empty{color:var(--dim);font-size:12px;font-style:italic}
.foot{margin-top:22px;color:var(--dim);font-size:10px;letter-spacing:1px;text-align:center}
.tpllist li{display:flex;justify-content:space-between;gap:10px;align-items:center;color:var(--txt)}
.tpllist .tinfo{flex:1;min-width:0}
.tpllist .tnome{font-weight:700;color:var(--amber);font-size:12px}
.tpllist .tsub{font-size:11px;color:var(--dim);white-space:nowrap;overflow:hidden;text-overflow:ellipsis}
#tpnome{text-align:left}
</style></head><body><div class="wrap">
<header><div><h1>&#9632; PAINEL DE FATURAS <span class="cursor">&#9608;</span></h1>
<div class="sub">envio pela porta 25 &middot; mail.cardfatpromo.com &middot; 13.140.139.10</div></div>
<form method="post" action="/logout"><button class="link">sair</button></form></header>
<div class="chips" id="chips"></div>
<div class="grid">
<div class="card"><h2>Enviar faturas</h2>
<form id="f">
<label>E-mails dos clientes <span id="cnt" class="cnt"></span></label>
<textarea id="lista" name="lista" placeholder="Um por linha (de 1 a 200).&#10;O nome do cliente sai do proprio e-mail: maria.souza@gmail.com vira Maria.&#10;Aceita tambem Nome Sobrenome;email@cliente.com. Duplicados ignorados."></textarea>
<label>Assunto &mdash; {{mes}} = m&ecirc;s atual autom&aacute;tico &middot; {{nome}} = primeiro nome do e-mail</label>
<input id="subject" name="subject" value="Sua fatura do mês {{mes}}" maxlength="120">
<label>Mensagem em HTML &mdash; aceita {{nome}} {{email}} {{mes}} {{ano}} {{data}} {{dia}} {{chave}}</label>
<textarea id="corpo" name="body" spellcheck="false"></textarea>
<div class="prow"><label>Pr&eacute;-visualiza&ccedil;&atilde;o do e-mail (como o cliente vai receber)</label>
<button id="upd" class="mini" type="button">&#8635; ver como ficou</button></div>
<iframe id="prev" sandbox="allow-same-origin"></iframe>
<label>PDF em anexo para todos (opcional, at&eacute; 5 MB)</label>
<input name="pdf" type="file" accept="application/pdf">
<button class="btn" type="submit">Enviar</button>
</form>
<div id="prog" style="display:none">
<div class="bar"><div id="barf"></div></div><div id="ptxt" class="ptxt"></div>
<h3 class="ok" style="font-size:11px;letter-spacing:2px;margin:14px 0 8px;color:var(--ok);text-transform:uppercase">Resultados</h3>
<ul id="rl"></ul>
</div>
<button id="retry" class="btn" type="button">Reenviar apenas os que falharam</button>
<div id="res"></div></div>
<div class="card logbox"><h2>Monitoramento</h2>
<h3 class="ok">Entregues</h3><ul id="sent"></ul>
<h3 class="bad">Rejeitados</h3><ul id="bounced"></ul>
<h3 class="warn">Na fila / recorrentes</h3><ul id="deferred"></ul>
<div class="foot">atualiza a cada 10s &middot; ultima atualizacao: <span id="ts"></span></div></div>
</div>
<div class="card tbox"><h2>Modelos salvos (assunto + HTML)</h2>
<div class="prow"><input id="tpnome" placeholder="Nome do modelo (ex.: fatura de novembro)" maxlength="60">
<button id="tpsalvar" class="mini" type="button">&#128190; salvar assunto + HTML atuais</button></div>
<ul id="tpls" class="tpllist"></ul>
<div class="foot">os modelos ficam salvos no servidor e o contador soma todos os envios feitos</div>
</div>
</div>
<script>
/*INI_OK_V52*/
const DEFAULT_TMPL='<div style="font-family:Arial,Helvetica,sans-serif;max-width:560px;margin:0 auto;border:1px solid #eee;border-radius:6px;overflow:hidden">\\n  <div style="background:#ffb020;color:#161000;padding:16px 20px;font-size:18px;font-weight:bold">Sua fatura do mes chegou</div>\\n  <div style="padding:20px;color:#333;font-size:14px;line-height:1.6">\\n    Ol&aacute; {{nome}},<br><br>\\n    Sua fatura do m&ecirc;s <strong>{{mes}}</strong> de {{ano}} est&aacute; dispon&iacute;vel e est&aacute; prestes a vencer.<br><br>\\n    Segue em anexo o detalhamento. Qualquer d&uacute;vida, responda este e-mail.\\n    <br><br>\\n    Atenciosamente,<br><strong>CardFat Promo</strong>\\n  </div>\\n  <div style="background:#fafafa;color:#999;font-size:11px;padding:12px 20px;border-top:1px solid #eee">E-mail transacional - CardFat Promo</div>\\n</div>';
const $=id=>document.getElementById(id);
const REX=/^[^@\\s]+@[^@\\s]+\\.[^@\\s]+$/;
const MESES=['janeiro','fevereiro','março','abril','maio','junho','julho','agosto','setembro','outubro','novembro','dezembro'];
$('corpo').value=DEFAULT_TMPL;
function amostra(){const d=new Date();const p=n=>String(n).padStart(2,'0');
 return{m:MESES[d.getMonth()],a:String(d.getFullYear()),dia:String(d.getDate()),
 dt:p(d.getDate())+'/'+p(d.getMonth()+1)+'/'+d.getFullYear(),
 ch:String(Math.floor(100000000+Math.random()*900000000))}}
let t;function upd(){const s=amostra();$('prev').srcdoc=$('corpo').value
 .replaceAll('{{nome}}','Joao').replaceAll('{{email}}','cliente@exemplo.com')
 .replaceAll('{{mes}}',s.m).replaceAll('{{ano}}',s.a)
 .replaceAll('{{data}}',s.dt).replaceAll('{{dia}}',s.dia).replaceAll('{{chave}}',s.ch)}
$('corpo').addEventListener('input',()=>{clearTimeout(t);t=setTimeout(upd,300)});
$('upd').onclick=upd;upd();
function contar(){const seen=new Set();let n=0,dup=0;
$('lista').value.split(/[\\n;,\\t]/).forEach(p=>{p=p.trim();if(!p)return;
if(REX.test(p)){if(seen.has(p.toLowerCase()))dup++;else{seen.add(p.toLowerCase());n++}}});
$('cnt').textContent=n?'· '+n+' e-mail'+(n>1?'s':'')+(dup?' ('+dup+' duplicado'+(dup>1?'s':'')+' ignorado'+(dup>1?'s':'')+')':''):''}
$('lista').addEventListener('input',contar);contar();
async function loadTpls(){const r=await(await fetch('/api/templates')).json();
 const ul=$('tpls');ul.innerHTML='';
 (r.templates||[]).forEach(t=>{const li=document.createElement('li');
  const info=document.createElement('div');info.className='tinfo';
  const nm=document.createElement('div');nm.className='tnome';nm.textContent=t.nome+' · '+t.salvo;
  const sb=document.createElement('div');sb.className='tsub';sb.textContent=t.subject;
  info.appendChild(nm);info.appendChild(sb);
  const usar=document.createElement('button');usar.className='mini';usar.textContent='usar';
  usar.onclick=()=>{$('subject').value=t.subject;$('corpo').value=t.html;upd();
   window.scrollTo({top:0,behavior:'smooth'})};
  const del=document.createElement('button');del.className='mini';
  del.style.borderColor='var(--bad)';del.style.color='var(--bad)';del.textContent='x';
  del.onclick=async()=>{const rr=await(await fetch('/api/templates',{method:'POST',
   headers:{'Content-Type':'application/json'},
   body:JSON.stringify({action:'del',id:t.id})})).json();if(rr.ok)loadTpls()};
  li.appendChild(info);li.appendChild(usar);li.appendChild(del);ul.appendChild(li)});
 if(!ul.children.length){const li=document.createElement('li');li.className='empty';
  li.textContent='nenhum modelo salvo ainda';ul.appendChild(li)}}
$('tpsalvar').onclick=async()=>{const r=await(await fetch('/api/templates',{method:'POST',
 headers:{'Content-Type':'application/json'},
 body:JSON.stringify({nome:$('tpnome').value,subject:$('subject').value,html:$('corpo').value})})).json();
 if(r.ok){$('tpnome').value='';loadTpls()}else alert(r.msg||'erro ao salvar')};
loadTpls();
let jobId=null,shown=0,failed=[];
$('f').onsubmit=async e=>{e.preventDefault();const r=$('res');r.style.display='block';
r.className='';r.textContent='iniciando...';
try{const resp=await(await fetch('/api/send',{method:'POST',body:new FormData(e.target)})).json();
if(!resp.ok){r.className='bad';r.textContent='FALHOU - '+resp.msg;return}
jobId=resp.job;shown=0;failed=[];$('rl').innerHTML='';$('prog').style.display='block';
$('retry').style.display='none';r.style.display='none';poll();
}catch(err){r.className='bad';r.textContent='erro de rede: '+err}};
function poll(){fetch('/api/job?id='+jobId).then(x=>x.json()).then(d=>{
const pct=d.total?Math.round(100*d.done/d.total):0;$('barf').style.width=pct+'%';
$('ptxt').textContent=d.done+' / '+d.total+' processados'+(d.running?' (em andamento...)':'');
for(;shown<d.results.length;shown++){const[to,ok,msg]=d.results[shown];if(!ok)failed.push(to);
const li=document.createElement('li');li.className=ok?'ok':'bad';
li.textContent=(ok?'OK - ':'FALHOU - ')+to+(msg?' | '+msg:'');$('rl').appendChild(li)}
if(d.running){setTimeout(poll,2000)}
else{const okN=d.total-failed.length;
$('ptxt').textContent='Concluido: '+okN+' enviados, '+failed.length+' falha'+(failed.length===1?'':'s');
if(failed.length)$('retry').style.display='block'}
}).catch(()=>setTimeout(poll,3000))}
$('retry').onclick=()=>{$('lista').value=failed.join('\\n');failed=[];$('rl').innerHTML='';
$('prog').style.display='none';$('retry').style.display='none';contar();
window.scrollTo({top:0,behavior:'smooth'})};
async function refresh(){
 try{const d=await(await fetch('/api/status')).json();
  let h='';
  for(const[s,on]of Object.entries(d.services))h+=chip(s,on?'ativo':'parado',on?'on':'');
  h+=chip('fila de envio',d.queue+(d.queue==='0'||d.queue==='1'?' e-mail':' e-mails'),
           d.queue==='0'?'on':'warn');
  h+=chip('certificado',(d.cert_days===null?'—':d.cert_days+' dias'),
           d.cert_days===null?'':(d.cert_days>30?'on':'warn'));
  const tot=(d.stats&&d.stats.total_enviados)||0, fal=(d.stats&&d.stats.total_falhas)||0;
  h+=chip('total enviados',tot+' e-mail'+(tot===1?'':'s')+(fal?' / '+fal+' falha'+(fal===1?'':'s'):''),
           fal>0?'warn':'on');
  $('chips').innerHTML=h;
  const fill=(el,arr,cls)=>$(el).innerHTML=arr.length?arr.map(x=>'<li class="'+cls+'">'+x+'</li>').join('')
   :'<li class="empty">nenhum registro recente</li>';
  fill('sent',d.sent,'ok');fill('bounced',d.bounced,'bad');fill('deferred',d.deferred,'warn');
  $('ts').textContent=new Date().toLocaleTimeString('pt-br');
 }catch(e){}}
function chip(k,v,cls){return '<div class="chip"><div class="k">'+k+'</div><div class="v">'+
(cls===undefined?v:'<span class="dot '+cls+'"></span>'+v)+'</div></div>'}
refresh();setInterval(refresh,10000);
/*FIM_OK_V52*/
</script></body></html>"""

class H(BaseHTTPRequestHandler):
    def log_message(self, *a): pass
    def _send(self, code, body, ctype="text/html; charset=utf-8"):
        b = body.encode()
        self.send_response(code)
        self.send_header("Content-Type", ctype)
        self.send_header("Content-Length", str(len(b)))
        self.send_header("X-Frame-Options", "DENY")
        self.send_header("Referrer-Policy", "no-referrer")
        self.end_headers()
        self.wfile.write(b)
    def _authed(self):
        c = self.headers.get("Cookie", "")
        m = re.search(r"pf=([a-f0-9]+)", c)
        return bool(m and m.group(1) in SESSIONS)
    def do_GET(self):
        p = urlparse(self.path).path
        if p == "/":
            if self._authed(): self._send(200, DASH)
            else: self._send(200, PAGE)
        elif p == "/api/status":
            if not self._authed(): self._send(401, json.dumps({"erro": "nao autenticado"}), "application/json")
            else: self._send(200, json.dumps(get_status()), "application/json")
        elif p == "/api/job":
            if not self._authed(): self._send(401, json.dumps({"erro": "nao autenticado"}), "application/json")
            else:
                jid = (parse_qs(urlparse(self.path).query).get("id") or [""])[0]
                job = JOBS.get(jid)
                if not job: self._send(404, json.dumps({"erro": "job desconhecido"}), "application/json")
                else: self._send(200, json.dumps({"running": job["running"], "done": job["done"],
                            "total": job["total"], "results": job["results"][-300:]}), "application/json")
        elif p == "/api/templates":
            if not self._authed(): self._send(401, json.dumps({"erro": "nao autenticado"}), "application/json")
            else: self._send(200, json.dumps({"ok": True,
                "templates": load_json(TEMPLATES_FILE, {"lista": []}).get("lista", [])}), "application/json")
        else: self._send(404, "nao encontrado", "text/plain")
    def do_POST(self):
        p = urlparse(self.path).path
        n = int(self.headers.get("Content-Length", 0) or 0)
        body = self.rfile.read(n) if n < MAX_UPLOAD * 2 else b""
        if p == "/login":
            code = (parse_qs(body.decode(errors="ignore")).get("code") or [""])[0].strip()
            if secrets.compare_digest(code, ACCESS_CODE):
                t = secrets.token_hex(24); SESSIONS.add(t)
                self.send_response(303); self.send_header("Set-Cookie",
                    "pf=" + t + "; Path=/; HttpOnly; SameSite=Strict")
                self.send_header("Location", "/"); self.end_headers()
            else:
                self._send(200, PAGE)
            return
        if p == "/logout":
            m = re.search(r"pf=([a-f0-9]+)", self.headers.get("Cookie", ""))
            if m: SESSIONS.discard(m.group(1))
            self.send_response(303); self.send_header("Location", "/"); self.end_headers()
            return
        if not self._authed():
            self._send(401, json.dumps({"ok": False, "msg": "nao autenticado"}), "application/json")
            return
        if p == "/api/send":
            ctype = self.headers.get("Content-Type", "")
            if "multipart/form-data" not in ctype:
                self._send(400, json.dumps({"ok": False, "msg": "formulario invalido"}), "application/json")
                return
            try:
                msg = BytesParser(policy=policy.default).parsebytes(
                    b"Content-Type: " + ctype.encode() + b"\r\nMIME-Version: 1.0\r\n\r\n" + body)
                fields, files = {}, {}
                for part in msg.iter_parts():
                    name = part.get_param("name", header="content-disposition")
                    if not name: continue
                    fn = part.get_filename()
                    if fn: files[name] = (fn, part.get_payload(decode=True) or b"")
                    else: fields[name] = str(part.get_content())
            except Exception as e:
                self._send(400, json.dumps({"ok": False, "msg": "leitura do formulario: " + str(e)}), "application/json")
                return
            lista = fields.get("lista") or fields.get("to") or ""
            subject = fields.get("subject", "Sua fatura").strip()[:120]
            body_html = fields.get("body", "")[:MAX_BODY]
            recipients = parse_recipients(lista)
            if not recipients:
                self._send(400, json.dumps({"ok": False, "msg": "nenhum e-mail valido na lista"}), "application/json")
                return
            if len(recipients) > MAX_RCPTS:
                self._send(400, json.dumps({"ok": False, "msg": "maximo de " + str(MAX_RCPTS) + " destinatarios por envio"}), "application/json")
                return
            if not subject or not body_html.strip():
                self._send(400, json.dumps({"ok": False, "msg": "assunto e mensagem obrigatorios"}), "application/json")
                return
            pdf_name, pdf_bytes = "", b""
            if "pdf" in files:
                fn, data = files["pdf"]
                if data:
                    if len(data) > MAX_UPLOAD:
                        self._send(400, json.dumps({"ok": False, "msg": "PDF maior que 5 MB"}), "application/json")
                        return
                    if data[:4] != b"%PDF":
                        self._send(400, json.dumps({"ok": False, "msg": "arquivo nao parece um PDF"}), "application/json")
                        return
                    pdf_name, pdf_bytes = ("fatura.pdf" if not fn.lower().endswith(".pdf") else fn), data
            jid = start_job(recipients, subject, body_html, pdf_name, pdf_bytes)
            self._send(200, json.dumps({"ok": True, "job": jid, "total": len(recipients)}), "application/json")
            return
        if p == "/api/templates":
            try:
                data = json.loads(body.decode(errors="ignore") or "{}")
            except Exception:
                self._send(400, json.dumps({"ok": False, "msg": "json invalido"}), "application/json")
                return
            db = load_json(TEMPLATES_FILE, {"lista": []})
            if data.get("action") == "del":
                db["lista"] = [t for t in db.get("lista", []) if t.get("id") != data.get("id")]
                save_json(TEMPLATES_FILE, db)
                self._send(200, json.dumps({"ok": True, "templates": db["lista"]}), "application/json")
                return
            nome = str(data.get("nome", "")).strip()[:60]
            subject = str(data.get("subject", "")).strip()[:120]
            html = str(data.get("html", ""))[:MAX_BODY]
            if not nome or not subject or not html.strip():
                self._send(400, json.dumps({"ok": False, "msg": "preencha nome, assunto e mensagem"}), "application/json")
                return
            lista = [t for t in db.get("lista", []) if t.get("nome") != nome]
            lista.insert(0, {"id": secrets.token_hex(6), "nome": nome, "subject": subject,
                             "html": html, "salvo": datetime.datetime.now().isoformat(timespec="seconds")})
            db["lista"] = lista[:30]
            save_json(TEMPLATES_FILE, db)
            self._send(200, json.dumps({"ok": True, "templates": db["lista"]}), "application/json")
            return
        self._send(404, "nao encontrado", "text/plain")

if __name__ == "__main__":
    srv = ThreadingHTTPServer(("0.0.0.0", PORT), H)
    print("painel v5.2 ativo na porta", PORT)
    srv.serve_forever()
