#!/usr/bin/env python3
"""Local screenshot-first lifecycle annotation server (stdlib only)."""
from __future__ import annotations

import argparse
import hashlib
import json
import mimetypes
import os
import re
from datetime import datetime, timezone
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from urllib.parse import unquote, urlparse

ROOT = Path(__file__).resolve().parents[1]
DATA = ROOT / "data"
ANNOTATIONS = ROOT / "annotations" / "page_labels.jsonl"
STAGES = ["OFFER", "ADD_TO_ROUTE_OFFER", "DECLINED", "HEADING_TO_PICKUP", "ARRIVED_AT_STORE",
          "WAITING_FOR_ORDER", "CONFIRM_PICKUP", "PICKED_UP", "HEADING_TO_DROPOFF",
          "ARRIVED_AT_CUSTOMER", "DROP_OFF", "PHOTO", "COMPLETE_DELIVERY", "COMPLETED",
          "PAYOUT", "UNASSIGN", "DASH_HOME", "OTHER", "UNKNOWN"]
CONFIDENCES = ["CERTAIN", "LIKELY", "UNSURE"]
VOLATILE = re.compile(r"\b(?:\d{1,2}:)?\d{1,2}:\d{2}\b|\b\d+\s*(?:seconds?|secs?|s)\b", re.I)
_record_cache: tuple[int, list[dict]] | None = None


def safe_json(path: Path) -> dict:
    try:
        value = json.loads(path.read_text(encoding="utf-8"))
        return value if isinstance(value, dict) else {}
    except (OSError, ValueError):
        return {}


def flatten_text(node: object) -> list[str]:
    result: list[str] = []
    if not isinstance(node, dict): return result
    for key in ("text", "contentDescription"):
        value = node.get(key)
        if isinstance(value, str) and value.strip(): result.append(value.strip())
    for child in node.get("children") or []: result.extend(flatten_text(child))
    return list(dict.fromkeys(result))


def fallback_signature(tree: dict) -> str:
    text = "\n".join(VOLATILE.sub("<countdown>", part.lower()) for part in flatten_text(tree))
    return hashlib.sha256(text.encode()).hexdigest()


def load_labels() -> tuple[dict[str, dict], dict[str, dict]]:
    by_record: dict[str, dict] = {}; by_signature: dict[str, dict] = {}
    if not ANNOTATIONS.exists(): return by_record, by_signature
    for line in ANNOTATIONS.read_text(encoding="utf-8").splitlines():
        try: label = json.loads(line)
        except ValueError: continue
        if label.get("recordId"): by_record[label["recordId"]] = label
        if label.get("reuseForContentHash") and label.get("contentHash"): by_signature[label["contentHash"]] = label
    return by_record, by_signature


def scan_records() -> list[dict]:
    global _record_cache
    by_record, by_signature = load_labels(); records = []
    if not DATA.exists(): return records
    label_mtime = ANNOTATIONS.stat().st_mtime_ns if ANNOTATIONS.exists() else 0
    if _record_cache is not None and _record_cache[0] == label_mtime:
        return _record_cache[1]
    labeled_ids = set(by_record)
    event_paths: list[Path] = []
    # Screenshot-first: directory enumeration is far cheaper than parsing tens of
    # thousands of legacy tree-only events. Explicitly labeled tree-only records
    # remain visible as well.
    for day in sorted((p for p in DATA.iterdir() if p.is_dir()), key=lambda p: p.name):
        with os.scandir(day) as entries:
            for entry in entries:
                if not entry.is_dir(): continue
                folder = Path(entry.path); rel = folder.relative_to(DATA).as_posix()
                has_image = any((folder / name).is_file() for name in ("screenshot.jpg", "screenshot.jpeg", "screenshot.png"))
                if has_image or rel in labeled_ids: event_paths.append(folder / "event.json")
    for event_path in sorted(event_paths):
        folder = event_path.parent; rel = folder.relative_to(DATA).as_posix()
        event = safe_json(event_path); tree = safe_json(folder / "accessibility_tree.json")
        screenshot = next((p for p in (folder / "screenshot.jpg", folder / "screenshot.jpeg", folder / "screenshot.png") if p.exists()), None)
        content_hash = str(event.get("contentHash") or hashlib.sha256(json.dumps(tree, sort_keys=True).encode()).hexdigest())
        signature = str(event.get("annotationSignature") or fallback_signature(tree))
        inherited = False; label = by_record.get(rel)
        if label is None and content_hash in by_signature: label = by_signature[content_hash]; inherited = True
        records.append({"id": rel, "day": folder.parent.name, "folder": folder.name,
            "timestamp": event.get("treeCapturedAt") or event.get("timestamp") or "",
            "contentHash": content_hash, "annotationSignature": signature,
            "assignmentId": event.get("assignmentId"), "captureType": event.get("captureType", "legacy"),
            "eventTypeName": event.get("eventTypeName", ""), "hasScreenshot": screenshot is not None,
            "screenshotUrl": f"/api/screenshot/{rel}" if screenshot else "", "texts": flatten_text(tree)[:80],
            "label": label, "labelInherited": inherited})
    _record_cache = (label_mtime, records)
    return records


HTML = r'''<!doctype html><html><head><meta charset="utf-8"><meta name="viewport" content="width=device-width,initial-scale=1">
<title>DoorDash Lifecycle Annotation</title><style>
:root{color-scheme:dark;--bg:#0d0f11;--panel:#17191c;--panel2:#202328;--line:#33373d;--text:#f5f5f2;--muted:#9da2aa;--red:#ff554b;--green:#62d48d}*{box-sizing:border-box}body{margin:0;background:var(--bg);color:var(--text);font:14px Inter,system-ui,sans-serif}button,input,select,textarea{font:inherit;color:inherit}.app{height:100vh;display:grid;grid-template-columns:340px minmax(420px,1fr) 330px}.side,.form{background:var(--panel);overflow:auto}.side{border-right:1px solid var(--line)}.form{border-left:1px solid var(--line);padding:18px}.head{position:sticky;top:0;z-index:2;background:#17191cf5;padding:18px;border-bottom:1px solid var(--line)}h1{font-size:20px;margin:2px 0 12px}.stats,.muted{color:var(--muted)}input,select,textarea{width:100%;background:#101215;border:1px solid var(--line);border-radius:7px;padding:10px;margin-top:8px}.list{padding:8px}.item{width:100%;text-align:left;background:transparent;border:1px solid transparent;border-radius:7px;padding:11px;cursor:pointer}.item:hover{background:var(--panel2)}.item.active{background:#282222;border-color:var(--red)}.item .row,.nav{display:flex;justify-content:space-between;gap:10px}.badge{font-size:11px;padding:2px 6px;border-radius:9px;background:#30343a}.badge.done{color:var(--green)}main{overflow:auto;padding:20px}.viewer{max-width:900px;margin:auto}.shot{display:block;max-width:100%;max-height:68vh;margin:auto;border-radius:10px;box-shadow:0 8px 40px #0008}.empty{padding:80px 20px;text-align:center;color:var(--muted)}.timeline{display:grid;grid-template-columns:repeat(5,1fr);gap:7px;margin:16px 0}.tick{border:1px solid var(--line);background:var(--panel);border-radius:7px;padding:8px;overflow:hidden;cursor:pointer}.tick.current{border-color:var(--red)}.tick span{display:block;white-space:nowrap;overflow:hidden;text-overflow:ellipsis}.field{margin:16px 0}.field label{font-size:12px;text-transform:uppercase;color:var(--muted);font-weight:700}.stagegrid{display:grid;grid-template-columns:1fr 1fr;gap:6px;margin-top:8px}.stage{margin:0;padding:8px;background:#111316;border:1px solid var(--line);border-radius:6px;cursor:pointer;font-size:11px}.stage.on{background:#592b28;border-color:var(--red)}textarea{height:100px;resize:vertical}.save{background:var(--red);border:0;border-radius:7px;padding:11px;width:100%;font-weight:800;cursor:pointer}.reuse{display:flex;gap:8px;align-items:center;margin:12px 0}.reuse input{width:auto;margin:0}.nav button{background:var(--panel2);border:1px solid var(--line);padding:8px 12px;border-radius:6px}.texts{margin-top:14px;color:#c8cbd0;line-height:1.6}.toast{min-height:20px;color:var(--green);margin-top:10px}@media(max-width:950px){.app{grid-template-columns:260px 1fr}.form{grid-column:1/-1;border:1px solid var(--line)}main{min-height:600px}}
</style></head><body><div class="app"><aside class="side"><div class="head"><div class="muted">PAGE LABELER</div><h1>DoorDash 生命周期</h1><div id="stats" class="stats"></div><input id="search" placeholder="搜索时间、文本、订单"><select id="filter"><option value="all">全部页面</option><option value="pending">仅待标注</option><option value="done">仅已标注</option><option value="screenshot">仅有截图</option></select></div><div id="list" class="list"></div></aside><main><div id="viewer" class="viewer"></div></main><aside class="form"><div class="nav"><button id="prev">← 上一条</button><button id="next">下一条 →</button></div><div class="field"><label>Stage</label><div id="stages" class="stagegrid"></div></div><div class="field"><label>Confidence</label><select id="confidence"></select></div><div class="field"><label>订单关联提示</label><input id="orderHint" placeholder="assignment / order / merchant"></div><div class="field"><label>Notes</label><textarea id="notes" placeholder="记录判断依据或疑点"></textarea></div><label class="reuse"><input type="checkbox" id="reuse" checked>相同 contentHash 复用标签</label><button id="save" class="save">保存标签 (Ctrl/⌘+S)</button><div id="toast" class="toast"></div></aside></div>
<script>
let records=[],shown=[],index=0,stage='UNKNOWN'; const $=id=>document.getElementById(id), esc=s=>String(s??'').replace(/[&<>"']/g,c=>({'&':'&amp;','<':'&lt;','>':'&gt;','"':'&quot;',"'":'&#39;'}[c]));
async function init(){let cfg=await fetch('/api/config').then(r=>r.json());records=await fetch('/api/records').then(r=>r.json());$('stages').innerHTML=cfg.stages.map(x=>`<button class="stage" data-stage="${x}">${x}</button>`).join('');$('confidence').innerHTML=cfg.confidences.map(x=>`<option>${x}</option>`).join('');document.querySelectorAll('.stage').forEach(b=>b.onclick=()=>{stage=b.dataset.stage;paintStages()});apply()}
function apply(){let q=$('search').value.toLowerCase(),f=$('filter').value;shown=records.filter(r=>(!q||JSON.stringify(r).toLowerCase().includes(q))&&(f==='all'||f==='pending'&&!r.label||f==='done'&&r.label||f==='screenshot'&&r.hasScreenshot));index=Math.max(0,Math.min(index,shown.length-1));renderList();render()}
function renderList(){let done=records.filter(r=>r.label).length;$('stats').textContent=`${done}/${records.length} 已标注 · ${records.filter(r=>r.hasScreenshot).length} 截图`;$('list').innerHTML=shown.map((r,i)=>`<button class="item ${i===index?'active':''}" data-i="${i}"><div class="row"><b>${esc(r.label?.stage||'待标注')}</b><span class="badge ${r.label?'done':''}">${r.hasScreenshot?'截图':'Tree'}</span></div><div class="muted">${esc(r.day)} · ${esc(r.folder)} · ${esc(r.captureType)}</div></button>`).join('')||'<div class="empty">没有记录</div>';document.querySelectorAll('.item').forEach(b=>b.onclick=()=>{index=+b.dataset.i;renderList();render()})}
function current(){return shown[index]} function go(n){if(shown.length){index=(index+n+shown.length)%shown.length;renderList();render()}}
function render(){let r=current();if(!r){$('viewer').innerHTML='<div class="empty">请选择页面</div>';return}let label=r.label||{};stage=label.stage||'UNKNOWN';$('confidence').value=label.confidence||'UNSURE';$('notes').value=label.notes||'';$('orderHint').value=label.orderHint||r.assignmentId||'';$('reuse').checked=label.reuseForContentHash!==false;paintStages();let pos=records.findIndex(x=>x.id===r.id),near=records.slice(Math.max(0,pos-2),pos+3);$('viewer').innerHTML=`<div class="timeline">${near.map(x=>`<button class="tick ${x.id===r.id?'current':''}" data-id="${esc(x.id)}"><span>${esc(x.folder)}</span><span class="muted">${esc(x.label?.stage||'—')}</span></button>`).join('')}</div>${r.hasScreenshot?`<img class="shot" src="${r.screenshotUrl}" alt="DoorDash screenshot">`:'<div class="empty">此记录没有截图；可根据 Tree 文本标注</div>'}<div class="texts"><b>${esc(r.id)}</b> · ${esc(r.eventTypeName)}${r.labelInherited?' · 标签由相同 contentHash 复用':''}<br>${r.texts.map(esc).join(' · ')}</div>`;document.querySelectorAll('.tick').forEach(b=>b.onclick=()=>{let j=shown.findIndex(x=>x.id===b.dataset.id);if(j>=0){index=j;renderList();render()}})}
function paintStages(){document.querySelectorAll('.stage').forEach(b=>b.classList.toggle('on',b.dataset.stage===stage))}
async function save(){let r=current();if(!r)return;let body={recordId:r.id,contentHash:r.contentHash,annotationSignature:r.annotationSignature,stage,confidence:$('confidence').value,notes:$('notes').value.trim(),orderHint:$('orderHint').value.trim(),reuseForContentHash:$('reuse').checked};let res=await fetch('/api/labels',{method:'POST',headers:{'Content-Type':'application/json'},body:JSON.stringify(body)});if(!res.ok){$('toast').textContent=await res.text();return}records=await fetch('/api/records').then(x=>x.json());$('toast').textContent='已保存';apply();setTimeout(()=>$('toast').textContent='',1200)}
$('search').oninput=apply;$('filter').onchange=apply;$('prev').onclick=()=>go(-1);$('next').onclick=()=>go(1);$('save').onclick=save;document.onkeydown=e=>{if((e.ctrlKey||e.metaKey)&&e.key==='s'){e.preventDefault();save()}else if(!/INPUT|TEXTAREA|SELECT/.test(e.target.tagName)&&e.key==='ArrowLeft')go(-1);else if(!/INPUT|TEXTAREA|SELECT/.test(e.target.tagName)&&e.key==='ArrowRight')go(1)};init();
</script></body></html>'''


class Handler(BaseHTTPRequestHandler):
    def send(self, status: int, body: bytes, content_type: str) -> None:
        self.send_response(status); self.send_header("Content-Type", content_type); self.send_header("Content-Length", str(len(body)))
        self.send_header("Cache-Control", "no-store"); self.end_headers(); self.wfile.write(body)

    def do_GET(self) -> None:
        path = urlparse(self.path).path
        if path == "/": return self.send(200, HTML.encode(), "text/html; charset=utf-8")
        if path == "/api/config": return self.json(200, {"stages": STAGES, "confidences": CONFIDENCES})
        if path == "/api/records": return self.json(200, scan_records())
        if path.startswith("/api/screenshot/"):
            record_id = unquote(path.removeprefix("/api/screenshot/")); folder = (DATA / record_id).resolve()
            try: folder.relative_to(DATA.resolve())
            except ValueError: return self.send(403, b"Forbidden", "text/plain")
            image = next((p for p in (folder / "screenshot.jpg", folder / "screenshot.jpeg", folder / "screenshot.png") if p.is_file()), None)
            if image: return self.send(200, image.read_bytes(), mimetypes.guess_type(image.name)[0] or "application/octet-stream")
        self.send(404, b"Not found", "text/plain")

    def do_POST(self) -> None:
        if urlparse(self.path).path != "/api/labels": return self.send(404, b"Not found", "text/plain")
        try:
            length = int(self.headers.get("Content-Length", "0")); payload = json.loads(self.rfile.read(length))
            if payload.get("stage") not in STAGES or payload.get("confidence") not in CONFIDENCES: raise ValueError("Invalid stage or confidence")
            ids = {r["id"] for r in scan_records()}
            if payload.get("recordId") not in ids: raise ValueError("Unknown recordId")
            payload["savedAt"] = datetime.now(timezone.utc).isoformat(); payload["schemaVersion"] = 1
            ANNOTATIONS.parent.mkdir(parents=True, exist_ok=True)
            with ANNOTATIONS.open("a", encoding="utf-8") as output: output.write(json.dumps(payload, ensure_ascii=False) + "\n")
            self.json(200, {"ok": True})
        except (ValueError, TypeError, json.JSONDecodeError) as error: self.send(400, str(error).encode(), "text/plain; charset=utf-8")

    def json(self, status: int, value: object) -> None: self.send(status, json.dumps(value, ensure_ascii=False).encode(), "application/json; charset=utf-8")
    def log_message(self, fmt: str, *args: object) -> None: print(f"[{self.log_date_time_string()}] {fmt % args}")


def main() -> None:
    parser = argparse.ArgumentParser(); parser.add_argument("--host", default="127.0.0.1"); parser.add_argument("--port", type=int, default=8765)
    args = parser.parse_args(); server = ThreadingHTTPServer((args.host, args.port), Handler)
    print(f"DoorDash labeler: http://{args.host}:{args.port}"); print(f"Data: {DATA}"); print(f"Labels: {ANNOTATIONS}")
    try: server.serve_forever()
    except KeyboardInterrupt: pass
    finally: server.server_close()


if __name__ == "__main__": main()
