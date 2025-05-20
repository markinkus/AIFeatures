#!/usr/bin/env python3
"""
review_and_fix_po.py
====================

Script web per rivedere e correggere le traduzioni di un file .po:
per ogni msgstr esistente verifica con LLM se il testo è in italiano (Sì/No).
Se No → traduce msgstr in italiano e sostituisce il vecchio.
Riprende se output esiste e forza traduzione fino a 3 tentativi se msgstr == msgid.

Uso:
    pip install flask requests
    export URL_OLLAMA="http://localhost:11434/api/generate"
    python review_and_fix_po.py

Apri http://127.0.0.1:5000 nel browser.
"""
import os, requests, json
from flask import Flask, Response, render_template_string, request

app = Flask(__name__)
OLLAMA_URL = os.getenv("URL_OLLAMA", "http://localhost:11434/api/generate")
RETRY_N = 3
HTML = '''
<!doctype html>
<html lang="it">
<head>
  <meta charset="utf-8">
  <title>PO Review & Fix</title>
  <style>
    body { font-family: sans-serif; margin: 2em; }
    #progress { width: 100%; height: 1.5em; background: #eee; margin-bottom:1em; }
    #bar { width:0; height:100%; background: #f76c6c; }
    #log { height:400px; overflow:auto; background:#f9f9f9; padding:1em; white-space: pre-wrap; }
    .ok { color: #007700; }
    .fix { color: #cc0000; font-weight: bold; }
  </style>
</head>
<body>
  <h1>Revisione .po (controllo msgstr Italianità)</h1>
  <div>
    File input: <input type="text" id="input" size="40" placeholder="origine.po"><br><br>
    File output: <input type="text" id="output" size="40" placeholder="fissato.po"><br><br>
    <button id="start">Avvia revisione</button>
  </div>
  <div id="progress"><div id="bar"></div></div>
  <div id="log"></div>
  <script>
    document.getElementById('start').onclick = () => {
      const inp = document.getElementById('input').value.trim();
      const out = document.getElementById('output').value.trim();
      if(!inp||!out){ alert('Specificare input e output'); return; }
      document.getElementById('bar').style.width='0%';
      document.getElementById('log').innerHTML='';
      const es = new EventSource(`/stream?input=${encodeURIComponent(inp)}&output=${encodeURIComponent(out)}`);
      es.onmessage = e=>{
        const m = JSON.parse(e.data);
        if(m.type==='progress'){
          document.getElementById('bar').style.width = m.percent+'%';
        } else {
          const d = document.createElement('div');
          d.textContent = m.line;
          d.className = m.fixed? 'fix':'ok';
          document.getElementById('log').appendChild(d);
          document.getElementById('log').scrollTop = document.getElementById('log').scrollHeight;
        }
      };
    };
  </script>
</body>
</html>
'''

# Detection italianità
def llm_detect(text: str) -> bool:
    prompt = f"Rispondi SOLO 'Sì' o 'No': la frase seguente è in italiano? {text}"
    payload = {
        "model": "gemma3:4b",
        "prompt": prompt,
        "temperature": 0.0,
        "top_p": 1.0,
        "stream": False,
        "options": {"num_ctx": 3000, "num_predict": 10}
    }
    r = requests.post(OLLAMA_URL, headers={"Content-Type":"application/json"}, json=payload)
    try:
        resp = r.json().get('response', r.text).strip().splitlines()[0]
    except ValueError:
        resp = r.text.strip().splitlines()[0]
    return resp.lower().startswith('s')

# Traduzione msgstr
def llm_translate(text: str) -> str:
    prompt = f"Traduci questa frase in italiano, mantieni punteggiatura e placeholder, non fornire commenti: {text}"
    payload = {
        "model": "gemma3:4b",
        "prompt": prompt,
        "temperature": 0.1,
        "top_p": 1.0,
        "stream": False,
        "options": {"num_ctx": 3000, "num_predict": 200}
    }
    r = requests.post(OLLAMA_URL, headers={"Content-Type":"application/json"}, json=payload)
    try:
        return r.json().get('response', r.text).strip().splitlines()[-1]
    except ValueError:
        return r.text.strip().splitlines()[-1]

# Generatore SSE con resume e retry
def review_stream(input_path, output_path):
    lines = open(input_path, encoding='utf-8').read().splitlines()
    total = len(lines)
    # resume se esiste output
    start_idx = 0
    if os.path.exists(output_path):
        existing = open(output_path, encoding='utf-8').read().splitlines()
        start_idx = len(existing)
    mode = 'a' if start_idx>0 else 'w'
    with open(output_path, mode, encoding='utf-8') as out:
        # eventi per righe già processate
        for i in range(start_idx):
            yield f"data: {json.dumps({'type':'progress','percent':int((i+1)/total*100)})}\n\n"
        # processa dal start_idx
        last_msgid = ''
        for idx in range(start_idx, total):
            line = lines[idx]
            out_line = line
            fixed = False
            if line.startswith('msgid '):
                last_msgid = line.split(' ',1)[1].strip('"')
            elif line.startswith('msgstr '):
                orig = line.split(' ',1)[1].strip('"')
                if orig:
                    # forziamo retry se uguale a msgid
                    attempts = 0
                    to_translate = orig
                    while attempts < RETRY_N and (not llm_detect(to_translate) or to_translate == last_msgid):
                        to_translate = llm_translate(last_msgid or orig)
                        attempts += 1
                    if attempts>0 and to_translate != orig:
                        out_line = f'msgstr "{to_translate}"'
                        fixed = True
                last_msgid = ''
            out.write(out_line+'\n'); out.flush()
            p = int((idx+1)/total*100)
            yield f"data: {json.dumps({'type':'progress','percent':p})}\n\n"
            yield f"data: {json.dumps({'line':out_line,'fixed':fixed})}\n\n"
    yield f"data: {json.dumps({'line':'Revisione completata!','fixed':False})}\n\n"

@app.route('/')
def index():
    return render_template_string(HTML)

@app.route('/stream')
def stream():
    inp = request.args.get('input')
    out = request.args.get('output')
    return Response(review_stream(inp, out), mimetype='text/event-stream')

if __name__ == '__main__':
    app.run(debug=True, threaded=True)
