"""DAG editor HTML builder."""
# ruff: noqa
# fmt: off
from __future__ import annotations
from essence._shared import *  # noqa: F401,F403

# DAG EDITOR HTML BUILDER
# ══════════════════════════════════════════════════════════════════════════════

def _build_dag_editor_html(wf_ids: list) -> str:
    """
    Build the Workflow DAG Editor HTML page as a plain Python string.
    Must live at module level (not inside the FastAPI f-string template)
    so that raw string literals and embedded JS braces work correctly.
    """
    import json as _json
    wf_json = _json.dumps(wf_ids)

    css = (
        "body{font-family:system-ui,sans-serif;background:#0f1117;"
        "color:#e2e8f0;margin:0;padding:20px}"
        "h1{font-size:1.4rem;color:#7dd3fc;margin-bottom:16px}"
        "#sb{width:220px;float:left}"
        "#ca{margin-left:240px}"
        ".wi{padding:8px 12px;cursor:pointer;border-radius:6px;margin-bottom:4px;"
        "background:#1e2535;border:1px solid #334155;font-size:.82rem}"
        ".wi:hover{background:#273451;border-color:#7dd3fc}"
        ".wi.active{background:#1d4ed8;border-color:#60a5fa}"
        "svg{background:#141822;border-radius:10px;border:1px solid #1e2d45}"
        "#det{margin-top:16px;background:#1e2535;border-radius:8px;padding:14px;"
        "border:1px solid #334155;font-size:.82rem;min-height:80px;white-space:pre-wrap}"
    )

    # JavaScript — uses single quotes and plain concatenation to avoid
    # brace/quote collisions with the surrounding Python string.
    js = (
        "const WF_IDS=" + wf_json + ";"
        "const SC={success:'#22c55e',failed:'#ef4444',running:'#f59e0b',"
        "pending:'#3b82f6',skipped:'#6b7280',recovering:'#a855f7',rolled_back:'#f97316'};"
        "async function loadWF(id){"
        "document.querySelectorAll('.wi').forEach(e=>e.classList.remove('active'));"
        "const el=document.getElementById('wf-'+id);if(el)el.classList.add('active');"
        "try{const r=await fetch('/api/workflows/'+id);const d=await r.json();renderDAG(d);}"
        "catch(e){document.getElementById('det').textContent='Error: '+e;}}"
        "function renderDAG(wf){"
        "const svg=document.getElementById('dag'),steps=wf.steps||[];"
        "svg.innerHTML='';"
        "if(!steps.length){svg.innerHTML='<text x=\"360\" y=\"220\" "
        "text-anchor=\"middle\" fill=\"#64748b\" font-size=\"14\">No steps</text>';return;}"
        "const W=720,NW=170,NH=46,GX=26,GY=56;"
        "const cols=Math.min(steps.length,Math.floor(W/(NW+GX)));"
        "const rows=Math.ceil(steps.length/cols);"
        "const svgH=rows*(NH+GY)+50;"
        "svg.setAttribute('height',svgH);svg.setAttribute('viewBox','0 0 '+W+' '+svgH);"
        "steps.forEach((s,i)=>{"
        "const col=i%cols,row=Math.floor(i/cols);"
        "const x=col*(NW+GX)+(W-cols*(NW+GX)+GX)/2,y=row*(NH+GY)+20;"
        "const color=SC[s.status]||'#475569';"
        "const g=document.createElementNS('http://www.w3.org/2000/svg','g');"
        "g.setAttribute('transform','translate('+x+','+y+')');"
        "const rc=document.createElementNS('http://www.w3.org/2000/svg','rect');"
        "rc.setAttribute('width',NW);rc.setAttribute('height',NH);rc.setAttribute('rx',7);"
        "rc.setAttribute('fill',color+'22');rc.setAttribute('stroke',color);"
        "rc.setAttribute('stroke-width','1.5');"
        "const t1=document.createElementNS('http://www.w3.org/2000/svg','text');"
        "t1.setAttribute('x',NW/2);t1.setAttribute('y',17);"
        "t1.setAttribute('text-anchor','middle');t1.setAttribute('fill','#e2e8f0');"
        "t1.setAttribute('font-size','11');t1.setAttribute('font-weight','600');"
        "t1.textContent='#'+s.step_id+' '+(s.action||s.tool||'').slice(0,20);"
        "const t2=document.createElementNS('http://www.w3.org/2000/svg','text');"
        "t2.setAttribute('x',NW/2);t2.setAttribute('y',33);"
        "t2.setAttribute('text-anchor','middle');t2.setAttribute('fill',color);"
        "t2.setAttribute('font-size','10');t2.textContent=s.status;"
        "g.appendChild(rc);g.appendChild(t1);g.appendChild(t2);"
        "g.style.cursor='pointer';"
        "g.addEventListener('click',()=>{"
        "document.getElementById('det').textContent=("
        "'Step '+s.step_id+': '+s.action+'\
Tool: '+s.tool+"
        "'\
Status: '+s.status+'\
Retries: '+(s.retries||0)+"
        "'\
\
Result:\
'+(s.result||'(none)'));});"
        "svg.appendChild(g);});"
        "document.getElementById('det').textContent=("
        "wf.task+'\
'+steps.length+' steps  status: '+wf.status+'  id: '+wf.task_id);}"
        "const list=document.getElementById('wf-list');"
        "WF_IDS.forEach(id=>{"
        "const el=document.createElement('div');el.className='wi';el.id='wf-'+id;"
        "el.textContent=id.replace('wf_','').slice(0,20);el.title=id;"
        "el.addEventListener('click',()=>loadWF(id));list.appendChild(el);});"
        "if(WF_IDS.length)loadWF(WF_IDS[0]);"
    )

    return (
        "<!DOCTYPE html><html lang='en'><head>"
        "<meta charset='UTF-8'>"
        "<title>Essence Workflow DAG Editor</title>"
        "<style>" + css + "</style></head><body>"
        "<h1>&#11041; Essence Workflow DAG Editor</h1>"
        "<div id='sb'>"
        "<div style='font-size:.75rem;color:#64748b;margin-bottom:8px'>Recent workflows</div>"
        "<div id='wf-list'></div></div>"
        "<div id='ca'>"
        "<svg id='dag' width='720' height='440' viewBox='0 0 720 440'></svg>"
        "<div id='det'>Select a workflow to inspect its step graph.</div>"
        "</div>"
        "<script>" + js + "</script></body></html>"
    )


# ══════════════════════════════════════════════════════════════════════════════

# ══════════════════════════════════════════════════════════════════════════════
