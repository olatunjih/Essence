""" — OpenCanvas rich object rendering."""
# ruff: noqa
# fmt: off
from __future__ import annotations
from essence._shared import *  # noqa: F401,F403

# OPENCANVAS — rich object rendering for non-text artifacts
# ══════════════════════════════════════════════════════════════════════════════
# Detects structured artifacts in LLM output (code blocks, diagrams, charts,
# tables, images, SVG, JSON, HTML) and routes them to a split-pane canvas
# panel in the web UI for interactive rendering.
#
# Pipeline:  LLM response → OCArtifactDetector.detect()
#                           → (cleaned_text, artifacts[])
#                              ↓               ↓
#                         Chat panel       Canvas panel
#                     (with placeholders)  (rich renders)
#
# Artifact types: code, diagram, chart, table, image, svg, markdown, html, json
# CDN libs loaded lazily: highlight.js, mermaid.js, Chart.js, marked.js
# All degrade gracefully when CDN is unavailable (offline-safe).

class _OCType(_enum.Enum):
    """Supported artifact types for OpenCanvas rendering."""
    CODE      = "code"
    DIAGRAM   = "diagram"
    CHART     = "chart"
    TABLE     = "table"
    IMAGE     = "image"
    SVG       = "svg"
    MARKDOWN  = "markdown"
    HTML      = "html"
    JSON      = "json"

    @classmethod
    def from_lang(cls, lang: str) -> "_OCType":
        lang = lang.lower().strip()
        _M = {
            "mermaid": cls.DIAGRAM, "plantuml": cls.DIAGRAM, "dot": cls.DIAGRAM,
            "graphviz": cls.DIAGRAM, "chart": cls.CHART, "vegalite": cls.CHART,
            "chartjs": cls.CHART, "json": cls.JSON, "jsonc": cls.JSON,
            "html": cls.HTML, "htm": cls.HTML, "svg": cls.SVG,
            "markdown": cls.MARKDOWN, "md": cls.MARKDOWN,
            "csv": cls.TABLE, "tsv": cls.TABLE,
        }
        return _M.get(lang, cls.CODE)


@_dc.dataclass
class OCArtifact:
    """A single renderable artifact extracted from LLM output."""
    artifact_id:   str     = _dc.field(default_factory=lambda: f"art_{secrets.token_hex(6)}")
    session_id:    str     = ""
    artifact_type: _OCType = _OCType.CODE
    title:         str     = ""
    language:      str     = ""
    content:       str     = ""
    metadata:      dict    = _dc.field(default_factory=dict)
    version:       int     = 1
    created_at:    float   = _dc.field(default_factory=time.time)
    pinned:        bool    = False

    def to_dict(self) -> dict:
        return {
            "id": self.artifact_id, "session": self.session_id,
            "type": self.artifact_type.value, "title": self.title,
            "language": self.language, "content": self.content,
            "metadata": self.metadata, "version": self.version,
            "created": self.created_at, "pinned": self.pinned,
        }


_OC_FENCED_RE = re.compile(r"```(\w[\w+#.-]*)\s*\n(.*?)\n\s*```", re.DOTALL)
_OC_SVG_RE    = re.compile(r"<svg[\s>].*?</svg>", re.DOTALL | re.IGNORECASE)
_OC_B64_RE    = re.compile(r"data:image/(?:png|jpeg|gif|webp|svg\+xml);base64,[A-Za-z0-9+/=]{20,}")
_OC_TBL_RE    = re.compile(r"^\|.+\|\s*$", re.MULTILINE)


class OCArtifactDetector:
    """
    Parse LLM output and extract renderable artifacts.
    Returns (cleaned_text_with_placeholders, list[OCArtifact]).
    """

    @staticmethod
    def detect(text: str, session_id: str = "") -> "tuple[str, list[OCArtifact]]":
        artifacts: "list[OCArtifact]" = []
        cleaned = text
        for m in _OC_FENCED_RE.finditer(text):
            lang, body = m.group(1).strip(), m.group(2).strip()
            if not body:
                continue
            at = _OCType.from_lang(lang)
            title = OCArtifactDetector._title(body, lang, at)
            art = OCArtifact(session_id=session_id, artifact_type=at,
                             title=title, language=lang, content=body,
                             metadata={"source": "fenced"})
            artifacts.append(art)
            cleaned = cleaned.replace(m.group(0),
                f"\n[canvas:{art.artifact_id}|{at.value}|{title}]\n", 1)
        for m in _OC_SVG_RE.finditer(cleaned):
            art = OCArtifact(session_id=session_id, artifact_type=_OCType.SVG,
                             title="SVG Graphic", language="svg", content=m.group(0))
            artifacts.append(art)
            cleaned = cleaned.replace(m.group(0),
                f"\n[canvas:{art.artifact_id}|svg|SVG Graphic]\n", 1)
        for m in _OC_B64_RE.finditer(cleaned):
            art = OCArtifact(session_id=session_id, artifact_type=_OCType.IMAGE,
                             title="Image", content=m.group(0))
            artifacts.append(art)
            cleaned = cleaned.replace(m.group(0),
                f"\n[canvas:{art.artifact_id}|image|Image]\n", 1)
        for tbl in OCArtifactDetector._tables(cleaned):
            art = OCArtifact(session_id=session_id, artifact_type=_OCType.TABLE,
                             title="Table", language="markdown", content=tbl)
            artifacts.append(art)
            cleaned = cleaned.replace(tbl,
                f"\n[canvas:{art.artifact_id}|table|Table]\n", 1)
        return cleaned, artifacts

    @staticmethod
    def _title(body: str, lang: str, at: "_OCType") -> str:
        fl = body.split("\n")[0].strip()
        if at == _OCType.CODE:
            for p in ("def ", "class ", "function ", "const ", "export "):
                if fl.startswith(p):
                    return f"{lang}: {fl.split(chr(40))[0].split(chr(123))[0].replace(p, '').strip()}"[:60]
            for mk in ("# ", "// ", "/* ", "-- "):
                if fl.startswith(mk):
                    return fl[len(mk):].strip()[:60]
            return f"{lang} code"
        if at == _OCType.DIAGRAM:
            for kw, t in [("sequenceDiagram", "Sequence Diagram"), ("classDiagram", "Class Diagram"),
                          ("erDiagram", "ER Diagram"), ("gantt", "Gantt Chart"), ("pie", "Pie Chart")]:
                if kw in body[:120]:
                    return t
            return "Diagram"
        if at == _OCType.JSON:
            return "JSON Data"
        return at.value.capitalize()

    @staticmethod
    def _tables(text: str) -> "list[str]":
        tables: "list[str]" = []
        lines = text.split("\n")
        i = 0
        while i < len(lines):
            if _OC_TBL_RE.match(lines[i]):
                if i + 1 < len(lines) and re.match(r"^\|[\s:|-]+\|\s*$", lines[i + 1]):
                    s = i
                    i += 2
                    while i < len(lines) and _OC_TBL_RE.match(lines[i]):
                        i += 1
                    if i - s >= 3:
                        tables.append("\n".join(lines[s:i]))
                    continue
            i += 1
        return tables


class OCArtifactStore:
    """Per-session artifact storage with JSON persistence. Thread-safe."""

    def __init__(self, workspace: Path):
        self._dir = workspace / "canvas"
        self._dir.mkdir(parents=True, exist_ok=True)
        self._path = self._dir / "artifacts.json"
        self._lock = threading.RLock()
        self._data: "dict[str, list[OCArtifact]]" = {}
        self._load()

    def _load(self) -> None:
        if self._path.exists():
            try:
                raw = json.loads(self._path.read_text(encoding="utf-8"))
                for sid, arts in raw.items():
                    self._data[sid] = [OCArtifact(
                        artifact_id=a.get("id", ""), session_id=a.get("session", ""),
                        artifact_type=_OCType(a.get("type", "code")),
                        title=a.get("title", ""), language=a.get("language", ""),
                        content=a.get("content", ""), metadata=a.get("metadata", {}),
                        version=a.get("version", 1), created_at=a.get("created", 0),
                        pinned=a.get("pinned", False)) for a in arts]
            except Exception:
                self._data = {}

    def _save(self) -> None:
        try:
            self._path.write_text(json.dumps(
                {s: [a.to_dict() for a in al] for s, al in self._data.items()},
                indent=2, default=str), encoding="utf-8")
        except Exception:
            pass

    def add(self, art: "OCArtifact") -> "OCArtifact":
        with self._lock:
            sid = art.session_id or "default"
            self._data.setdefault(sid, []).append(art)
            if len(self._data[sid]) > 100:
                self._data[sid] = self._data[sid][-100:]
            self._save()
        return art

    def get(self, aid: str) -> "OCArtifact | None":
        with self._lock:
            for al in self._data.values():
                for a in al:
                    if a.artifact_id == aid:
                        return a
        return None

    def list_session(self, sid: str) -> "list[OCArtifact]":
        with self._lock:
            return list(self._data.get(sid, []))

    def delete(self, aid: str) -> bool:
        with self._lock:
            for al in self._data.values():
                for i, a in enumerate(al):
                    if a.artifact_id == aid:
                        al.pop(i)
                        self._save()
                        return True
        return False

    def pin(self, aid: str, pinned: bool = True) -> bool:
        with self._lock:
            a = self.get(aid)
            if a:
                a.pinned = pinned
                self._save()
                return True
        return False

    def clear_session(self, sid: str) -> int:
        with self._lock:
            n = len(self._data.pop(sid, []))
            self._save()
            return n


_oc_store: "OCArtifactStore | None" = None
_oc_store_lock = threading.Lock()


def get_oc_store(workspace: "Path | None" = None) -> "OCArtifactStore | None":
    "Return the module-level OpenCanvas artifact store singleton."
    global _oc_store
    if _oc_store is None and workspace:
        with _oc_store_lock:
            if _oc_store is None:
                _oc_store = OCArtifactStore(workspace)
    return _oc_store


def register_oc_routes(app: Any, workspace: Path) -> None:
    """
    Register OpenCanvas /api/canvas/* routes on a FastAPI app.
    Called from the server app lifespan.
    """
    from fastapi import HTTPException, Request as _FReq
    store = get_oc_store(workspace) or OCArtifactStore(workspace)

    @app.get("/api/canvas/artifacts")
    async def _oc_list(session_id: str = "default"):
        arts = store.list_session(session_id)
        return {"session": session_id, "count": len(arts),
                "artifacts": [a.to_dict() for a in arts]}

    @app.get("/api/canvas/artifacts/{aid}")
    async def _oc_get(aid: str):
        a = store.get(aid)
        if not a:
            raise HTTPException(404, "Artifact not found")
        return a.to_dict()

    @app.post("/api/canvas/detect")
    async def _oc_detect(req: _FReq):
        body = await req.json()
        text = body.get("text", "")
        sid = body.get("session_id", "default")
        cleaned, arts = OCArtifactDetector.detect(text, sid)
        for a in arts:
            store.add(a)
        return {"cleaned_text": cleaned,
                "artifacts": [a.to_dict() for a in arts]}

    @app.delete("/api/canvas/sessions/{sid}")
    async def _oc_clear(sid: str):
        return {"cleared": store.clear_session(sid), "session": sid}

    @app.delete("/api/canvas/artifacts/{aid}")
    async def _oc_del(aid: str):
        return {"deleted": aid, "ok": store.delete(aid)}

    @app.post("/api/canvas/artifacts/{aid}/pin")
    async def _oc_pin(aid: str, req: _FReq):
        body = await req.json()
        return {"ok": store.pin(aid, body.get("pinned", True))}

    log.debug("opencanvas_routes_registered")


# -CSS  OpenCanvas embedded stylesheet
_OC_CSS = (
    ".oc-panel{flex:0 0 45%;min-width:360px;max-width:60%;background:#0d1117;"
    "border-left:1px solid #30363d;display:flex;flex-direction:column;overflow:hidden}"
    ".oc-panel[hidden]{display:none}"
    ".oc-hdr{display:flex;justify-content:space-between;align-items:center;"
    "padding:8px 14px;background:#161b22;border-bottom:1px solid #30363d}"
    ".oc-hdr .t{font-weight:600;color:#58a6ff;font-size:14px}"
    ".oc-hdr button{background:none;border:1px solid #30363d;color:#8b949e;"
    "cursor:pointer;padding:2px 8px;border-radius:4px;margin-left:4px;font-size:12px}"
    ".oc-hdr button:hover{background:#21262d;color:#c9d1d9}"
    ".oc-arts{flex:1;overflow-y:auto;padding:12px;display:flex;flex-direction:column;gap:12px}"
    ".oc-empty{display:flex;flex-direction:column;align-items:center;justify-content:center;"
    "flex:1;color:#484f58;text-align:center;padding:40px}"
    ".oc-c{background:#161b22;border:1px solid #30363d;border-radius:8px;overflow:hidden}"
    ".oc-c:hover{border-color:#58a6ff}"
    ".oc-ch{display:flex;justify-content:space-between;align-items:center;"
    "padding:8px 12px;background:#0d1117;border-bottom:1px solid #21262d;font-size:12px}"
    ".oc-ct{color:#58a6ff;font-weight:600;text-transform:uppercase;font-size:10px}"
    ".oc-cn{color:#c9d1d9;flex:1;margin-left:8px;overflow:hidden;text-overflow:ellipsis;white-space:nowrap}"
    ".oc-ca button{background:none;border:none;color:#8b949e;cursor:pointer;padding:2px 6px;font-size:13px}"
    ".oc-ca button:hover{color:#58a6ff}"
    ".oc-cb{padding:12px;max-height:500px;overflow:auto}"
    ".oc-pre{background:#0d1117;color:#c9d1d9;font-family:monospace;font-size:13px;"
    "line-height:1.5;padding:12px;border-radius:6px;overflow-x:auto;white-space:pre}"
    ".oc-dia{display:flex;justify-content:center;padding:16px;background:#fff;"
    "border-radius:6px;min-height:200px}"
    ".oc-dia svg{max-width:100%;height:auto}"
    ".oc-tbl{width:100%;border-collapse:collapse;font-size:13px}"
    ".oc-tbl th{background:#21262d;color:#58a6ff;padding:8px 12px;text-align:left;"
    "border-bottom:2px solid #30363d;font-weight:600}"
    ".oc-tbl td{padding:6px 12px;border-bottom:1px solid #21262d;color:#c9d1d9}"
    ".oc-tbl tr:hover td{background:#161b22}"
    ".oc-img{max-width:100%;border-radius:6px}"
    ".oc-j{font-family:monospace;font-size:12px;color:#c9d1d9;white-space:pre-wrap}"
    ".oc-j .k{color:#79c0ff}.oc-j .s{color:#a5d6ff}"
    ".oc-j .n{color:#d2a8ff}.oc-j .b{color:#ff7b72}.oc-j .nl{color:#8b949e}"
    ".oc-ref{display:inline-flex;align-items:center;gap:4px;background:#1c2128;"
    "border:1px solid #30363d;border-radius:4px;padding:2px 8px;cursor:pointer;"
    "font-size:12px;color:#58a6ff}"
    ".oc-ref:hover{background:#21262d}"
    "@media(max-width:768px){.oc-panel{flex:0 0 50%;min-width:100%;"
    "border-left:none;border-top:1px solid #30363d}}"
)

# -JS  OpenCanvas embedded rendering engine (minified)
_OC_JS = (
    'const OC=(()=>{let S="default",A=[],V=false;'
    'const CDN={h:"https://cdn.jsdelivr.net/gh/highlightjs/cdn-release@11/build/highlight.min.js",'
    'hc:"https://cdn.jsdelivr.net/gh/highlightjs/cdn-release@11/build/styles/github-dark.min.css",'
    'm:"https://cdn.jsdelivr.net/npm/mermaid@11/dist/mermaid.min.js",'
    'c:"https://cdn.jsdelivr.net/npm/chart.js@4/dist/chart.umd.min.js",'
    'k:"https://cdn.jsdelivr.net/npm/marked@14/marked.min.js"},L={};'
    'function ls(k,u){if(L[k])return L[k];L[k]=new Promise(r=>{let s=document.createElement("script");'
    's.src=u;s.onload=r;s.onerror=()=>r();document.head.appendChild(s)});return L[k]}'
    'function lc(u){if(!document.querySelector(`link[href="${u}"]`)){let l=document.createElement("link");'
    'l.rel="stylesheet";l.href=u;document.head.appendChild(l)}}'
    'function E(s){return s.replace(/&/g,"&amp;").replace(/</g,"&lt;").replace(/>/g,"&gt;")}'
    'const R={'
    'async code(a,c){lc(CDN.hc);await ls("h",CDN.h);let p=document.createElement("pre"),'
    'co=document.createElement("code");co.className="oc-pre language-"+(a.language||"plaintext");'
    'co.textContent=a.content;p.appendChild(co);c.appendChild(p);if(window.hljs)window.hljs.highlightElement(co)},'
    'async diagram(a,c){await ls("m",CDN.m);let d=document.createElement("div");d.className="oc-dia";'
    'if(window.mermaid){try{window.mermaid.initialize({startOnLoad:false,theme:"dark"});'
    'let id="oc-"+a.id.replace(/[^a-z0-9]/gi,"");let{svg}=await window.mermaid.render(id,a.content);'
    'd.innerHTML=svg}catch(e){d.innerHTML="<pre style=color:#ff7b72>"+e.message+"</pre>"}}else{'
    'd.innerHTML="<pre class=oc-pre>"+E(a.content)+"</pre>"}c.appendChild(d)},'
    'async chart(a,c){await ls("c",CDN.c);let w=document.createElement("div");'
    'if(window.Chart){try{let sp=JSON.parse(a.content),cv=document.createElement("canvas");'
    'cv.width=600;cv.height=400;w.appendChild(cv);c.appendChild(w);new Chart(cv,sp);return}'
    'catch(e){w.innerHTML="<pre style=color:#ff7b72>"+e.message+"</pre>"}}else{'
    'w.innerHTML="<pre class=oc-pre>"+E(a.content)+"</pre>"}c.appendChild(w)},'
    'table(a,c){let ls=a.content.trim().split("\n").filter(l=>l.trim());if(ls.length<2){c.textContent=a.content;return}'
    'let t=document.createElement("table");t.className="oc-tbl";'
    'let pr=l=>l.replace(/^\\|/,"").replace(/\\|$/,"").split("|").map(c=>c.trim());'
    'let th=document.createElement("thead"),hr=document.createElement("tr");'
    'pr(ls[0]).forEach(h=>{let td=document.createElement("th");td.textContent=h;hr.appendChild(td)});'
    'th.appendChild(hr);t.appendChild(th);let tb=document.createElement("tbody");'
    'for(let i=2;i<ls.length;i++){if(/^\\|[\\s:|-]+\\|$/.test(ls[i]))continue;'
    'let tr=document.createElement("tr");pr(ls[i]).forEach(v=>{let td=document.createElement("td");'
    'td.textContent=v;tr.appendChild(td)});tb.appendChild(tr)}t.appendChild(tb);c.appendChild(t)},'
    'image(a,c){let img=document.createElement("img");img.className="oc-img";img.src=a.content;'
    'img.alt=a.title;img.loading="lazy";c.appendChild(img)},'
    'svg(a,c){let d=document.createElement("div");d.className="oc-dia";d.innerHTML=a.content;c.appendChild(d)},'
    'async markdown(a,c){await ls("k",CDN.k);let d=document.createElement("div");d.style.color="#c9d1d9";'
    'd.innerHTML=window.marked?window.marked.parse(a.content):E(a.content);c.appendChild(d)},'
    'html(a,c){let f=document.createElement("iframe");f.sandbox="allow-scripts";'
    'f.style.cssText="width:100%;min-height:300px;border:1px solid #30363d;border-radius:6px;background:#fff";'
    'c.appendChild(f);f.srcdoc=a.content},'
    'json(a,c){let d=document.createElement("div");d.className="oc-j";try{d.innerHTML=J(JSON.parse(a.content))}'
    'catch{d.textContent=a.content}c.appendChild(d)}'
    '};'
    'function J(o,i=0){let p="&nbsp;".repeat(i*2);if(o===null)return \'<span class="nl">null</span>\';'
    'if(typeof o=="boolean")return`<span class="b">${o}</span>`;'
    'if(typeof o=="number")return`<span class="n">${o}</span>`;'
    'if(typeof o=="string")return`<span class="s">"${E(o)}"</span>`;'
    'if(Array.isArray(o)){if(!o.length)return"[]";return`[<br>${o.map(v=>p+"&nbsp;&nbsp;"+J(v,i+1)).join(",<br>")}<br>${p}]`}'
    'let ks=Object.keys(o);if(!ks.length)return"{}";'
    'return`{<br>${ks.map(k=>`${p}&nbsp;&nbsp;<span class="k">"${E(k)}"</span>: ${J(o[k],i+1)}`).join(",<br>")}<br>${p}}`}'
    'function card(a){let c=document.createElement("div");c.className="oc-c";c.dataset.id=a.id;'
    'c.innerHTML=`<div class="oc-ch"><span class="oc-ct">${a.type}</span>'
    '<span class="oc-cn">${E(a.title||a.type)}</span>'
    '<span class="oc-ca"><button onclick="OC.cp(\'${a.id}\')" title=Copy>\\u{1F4CB}</button>'
    '<button onclick="OC.rm(\'${a.id}\')" title=Remove>\u2715</button></span></div>'
    '<div class="oc-cb" id="oc-b-${a.id}"></div>`;return c}'
    'async function add(a){A.push(a);let p=document.getElementById("oc-arts"),'
    'e=document.getElementById("oc-empty");if(!p)return;if(e)e.style.display="none";'
    'let c=card(a);p.appendChild(c);let b=c.querySelector(".oc-cb");'
    'let r=R[a.type]||R.code;await r(a,b);if(!V)show();b.scrollIntoView({behavior:"smooth",block:"nearest"})}'
    'function show(){let p=document.getElementById("oc-panel");if(p){p.hidden=false;V=true}}'
    'function hide(){let p=document.getElementById("oc-panel");if(p){p.hidden=true;V=false}}'
    'function toggle(){V?hide():show()}'
    'async function clear(){let p=document.getElementById("oc-arts"),e=document.getElementById("oc-empty");'
    'if(p)p.innerHTML="";if(e)e.style.display="flex";A=[];'
    'try{await fetch(`/api/canvas/sessions/${S}`,{method:"DELETE"})}catch{}}'
    'function cp(id){let a=A.find(x=>x.id===id);if(a)navigator.clipboard.writeText(a.content)}'
    'async function rm(id){A=A.filter(a=>a.id!==id);'
    'let c=document.querySelector(`.oc-c[data-id="${id}"]`);if(c)c.remove();'
    'if(!A.length){let e=document.getElementById("oc-empty");if(e)e.style.display="flex"}'
    'try{await fetch(`/api/canvas/artifacts/${id}`,{method:"DELETE"})}catch{}}'
    'async function proc(text,sid){S=sid||S;try{let r=await fetch("/api/canvas/detect",'
    '{method:"POST",headers:{"Content-Type":"application/json"},'
    'body:JSON.stringify({text,session_id:S})});let d=await r.json();'
    'for(let a of(d.artifacts||[]))await add(a);return d.cleaned_text||text}catch{return text}}'
    'function refs(html){return html.replace(/\\[canvas:([a-z0-9_]+)\\|([a-z]+)\\|([^\\]]+)\\]/gi,'
    '(_,id,t,title)=>`<span class="oc-ref" onclick="OC.scrollTo(\'${id}\')">\\u{1F4CE} ${E(title)}</span>`)}'
    'function scrollTo(id){show();let b=document.getElementById(`oc-b-${id}`);'
    'if(b)b.scrollIntoView({behavior:"smooth"})}'
    'function setSid(s){S=s}'
    'return{add,show,hide,toggle,clear,cp,rm,proc,refs,scrollTo,setSid}})();'
)

_OC_PANEL_HTML = (
    '<div id="oc-panel" class="oc-panel" hidden>'
    '<div class="oc-hdr"><span class="t">\U0001f3a8 Canvas</span><div>'
    '<button onclick="OC.toggle()">\u229e</button>'
    '<button onclick="OC.clear()">\U0001f5d1</button>'
    '<button onclick="OC.hide()">\u2715</button>'
    '</div></div>'
    '<div id="oc-arts" class="oc-arts"></div>'
    '<div id="oc-empty" class="oc-empty">'
    '<p>\U0001f4cb Artifacts will appear here</p>'
    '<p style="font-size:12px;color:#30363d;margin-top:8px">'
    'Code, diagrams, charts, tables from AI responses render here.</p>'
    '</div></div>'
)

log.debug("opencanvas_section_loaded")



