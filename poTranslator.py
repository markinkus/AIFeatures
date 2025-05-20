#!/usr/bin/env python3
"""
translate_po_web.py
===================

Web-based traduttore per file `.po` (inglese msgid → italiano msgstr) via Ollama gemma3:4b.
Interfaccia minimal con Flask + SSE: barra di progresso e log live nel browser,
mostra tutte le righe riscritte e traduce `msgid` in `msgstr`, ignorando il contenuto tedesco originale.

Setup:
    pip install flask requests
    export URL_OLLAMA="http://localhost:11434/api/generate"

Usage:
    python translate_po_web.py
    Apri http://127.0.0.1:5000 nel browser, inserisci input/output e premi Start.
"""
from flask import Flask, Response, render_template_string, request
import os, requests, json

app = Flask(__name__)
OLLAMA_URL = os.getenv("URL_OLLAMA", "http://localhost:11434/api/generate")

HTML = '''
<!doctype html>
<html lang="it">
<head>
  <meta charset="utf-8">
  <title>PO Translator</title>
  <style>
    body { font-family: sans-serif; margin: 2em; }
    #progress { width: 100%; height: 1.5em; background: #eee; margin-bottom:1em; }
    #bar { width:0; height:100%; background: #76c7c0; }
    #log { height:400px; overflow:auto; background:#f9f9f9; padding:1em; white-space: pre-wrap; }
    .translated { color: #007700; font-weight: bold; }
  </style>
</head>
<body>
  <h1>Traduttore .po (EN → IT)</h1>
  <div>
    File input: <input type="text" id="input" placeholder="es. buddyboss-de_DE.po" size="40"><br><br>
    File output: <input type="text" id="output" placeholder="es. traduzione_it.po" size="40"><br><br>
    <button type="button" id="start-btn">Start Translation</button>
  </div>
  <div id="progress"><div id="bar"></div></div>
  <div id="log"></div>
  <script>
    document.getElementById('start-btn').addEventListener('click', () => {
      const inp = document.getElementById('input').value.trim();
      const out = document.getElementById('output').value.trim();
      if (!inp || !out) { alert('Inserisci file di input e output'); return; }
      document.getElementById('log').innerHTML = '';
      document.getElementById('bar').style.width = '0%';
      const es = new EventSource(`/stream?input=${encodeURIComponent(inp)}&output=${encodeURIComponent(out)}`);
      es.onerror = err => console.error('SSE error', err);
      es.onmessage = evt => {
        const msg = JSON.parse(evt.data);
        if (msg.type === 'progress') {
          document.getElementById('bar').style.width = msg.percent + '%';
        } else if (msg.type === 'log') {
          const lineElem = document.createElement('div');
          lineElem.textContent = msg.line;
          if (msg.translated) lineElem.classList.add('translated');
          document.getElementById('log').appendChild(lineElem);
          document.getElementById('log').scrollTop = document.getElementById('log').scrollHeight;
        }
      };
    });
  </script>
</body>
</html>
'''


def llm_request(text: str) -> str:
    payload = {
        "model": "gemma3:4b",
        "prompt": f"Traduci questa frase in italiano, mantieni punteggiatura e placeholder, non fornire commenti: {text}",
        "temperature": 0.1,
        "top_p": 1.0,
        "stream": False,
        "options": {"num_ctx": 3000, "num_predict": 500}
    }
    resp = requests.post(OLLAMA_URL, headers={"Content-Type": "application/json"}, json=payload)
    try:
        text_resp = resp.json().get('response', resp.text)
    except ValueError:
        text_resp = resp.text
    # restituisce l'ultimo rigo netto
    return text_resp.strip().splitlines()[-1]


def translate_stream(input_path, output_path):
    # Leggi tutto l'input
    lines = open(input_path, encoding='utf-8').read().splitlines()
    total = len(lines)
    # Verifica esistenza output e calcola offset
    start_idx = 0
    if os.path.exists(output_path):
        existing = open(output_path, encoding='utf-8').read().splitlines()
        start_idx = len(existing)
    # Apri in append se riprendi, altrimenti in write
    mode = 'a' if start_idx>0 else 'w'
    last_msgid = ''
    translated = False
    with open(output_path, mode, encoding='utf-8') as out_file:
        # Se riprendi, genera eventi SSE per righe già fatte
        for i in range(start_idx):
            percent = int((i+1)/total*100)
            yield f"data: {json.dumps({'type':'progress','percent':percent})}\n\n"
        # Procedi dalle righe successive
        for idx in range(start_idx, total):
            line = lines[idx]
            new_line = line
            translated_flag = False
            if line.startswith('msgid '):
                last_msgid = line.split(' ',1)[1].strip('"')
            elif line.startswith('msgstr '):
                if last_msgid:
                    tr = llm_request(last_msgid)
                    new_line = f'msgstr "{tr}"'
                    translated_flag = True
                last_msgid = ''
            out_file.write(new_line+'\n'); out_file.flush()
            # Eventi SSE
            p = int((idx+1)/total*100)
            yield f"data: {json.dumps({'type':'progress','percent':p})}\n\n"
            yield f"data: {json.dumps({'type':'log','line':new_line,'translated':translated_flag})}\n\n"
    # fine
    yield f"data: {json.dumps({'type':'log','line':'Traduzione completata!','translated':False})}\n\n"

@app.route('/')
def index():
    return render_template_string(HTML)

@app.route('/stream')
def stream():
    inp = request.args.get('input')
    out = request.args.get('output')
    return Response(translate_stream(inp, out), mimetype='text/event-stream')

if __name__ == '__main__':
    app.run(debug=True, threaded=True)
