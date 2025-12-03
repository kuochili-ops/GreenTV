# app.py
import streamlit as st
from yt_dlp import YoutubeDL
import tempfile, os, concurrent.futures, time, traceback, json, re
from html import escape

st.set_page_config(page_title="YouTube 點唱機（單欄）", layout="wide")
st.markdown("<h1 style='margin-bottom:6px;'>🎵 YouTube 點唱機（單欄）</h1>", unsafe_allow_html=True)
st.write("選擇面板：播放 / 加入 / 移除 三鈕橫排，封面與播放器也在面板裡；下方垂直候選清單。")

with st.expander("輸入 YouTube 影片或播放清單網址（每行一個）", expanded=False):
    urls_input = st.text_area("網址（每行一個）", height=120)
    uploaded_cookies = st.file_uploader("（選擇性）上傳 cookies.txt", type=["txt"])
    parse_btn = st.button("開始解析並產生清單")

_default_max_workers, _default_batch_size, _debug_mode = 2, 6, False

def fetch_info(url, cookiefile=None, timeout=30, extract_flat=False, quiet=True):
    opts = {"skip_download": True, "quiet": quiet, "no_warnings": quiet, "socket_timeout": timeout}
    if extract_flat: opts["extract_flat"] = True
    if cookiefile: opts["cookiefile"] = cookiefile
    with YoutubeDL(opts) as ydl: return ydl.extract_info(url, download=False)

def choose_best_m3u8(formats):
    candidates = [f for f in formats if f.get("url") and ("m3u8" in (f.get("protocol") or "").lower() or "hls" in (f.get("format_note") or "").lower())]
    if not candidates: return None
    candidates.sort(key=lambda f: (int(f.get("height") or 0), float(f.get("tbr") or 0)), reverse=True)
    return candidates[0]

def fetch_best_m3u8_for_video(video_url, cookiefile=None, timeout=25, quiet=True):
    try:
        info = fetch_info(video_url, cookiefile=cookiefile, timeout=timeout, extract_flat=False, quiet=quiet)
        best = choose_best_m3u8(info.get("formats") or [])
        return {"title": info.get("title") or video_url, "url": best.get("url") if best else None, "webpage_url": info.get("webpage_url")}
    except Exception as e:
        return {"title": video_url, "url": None, "error": str(e)}

def export_m3u8_list(results): return "\n".join([f"{r['title']} | {r['url']}" for r in results if r.get("url")])

if parse_btn:
    urls = [u.strip() for u in urls_input.splitlines() if u.strip()]
    if urls:
        cookiefile_path = None
        if uploaded_cookies:
            tmp = tempfile.NamedTemporaryFile(delete=False)
            tmp.write(uploaded_cookies.getbuffer()); tmp.close()
            cookiefile_path = tmp.name
        to_process = [{"title": u, "url": u} for u in urls]
        results = []
        with concurrent.futures.ThreadPoolExecutor(max_workers=_default_max_workers) as ex:
            future_to_item = {ex.submit(fetch_best_m3u8_for_video, item["url"], cookiefile_path, 25, not _debug_mode): item for item in to_process}
            for fut in concurrent.futures.as_completed(future_to_item):
                item = future_to_item[fut]
                try: res = fut.result()
                except Exception as exc: res = {"title": item["title"], "url": None, "error": str(exc)}
                results.append(res)
        playable = [r for r in results if r.get("url")]
        st.session_state["playable"], st.session_state["selected_index"] = playable, 0 if playable else None
        st.success(f"解析完成：可播放 {len(playable)} 項")

def youtube_id_from_url(url):
    m = re.search(r"(?:v=|\/)([0-9A-Za-z_-]{11})(?:[&?#]|$)", url or "")
    return m.group(1) if m else None

playable = st.session_state.get("playable", [])
selected_index = st.session_state.get("selected_index", None)
safe_playable = []
for p in playable:
    vid = youtube_id_from_url(p.get("webpage_url") or p.get("url"))
    thumb = f"https://i.ytimg.com/vi/{vid}/hqdefault.jpg" if vid else "https://placehold.co/640x360/0b1b2b/ffffff?text=No+Cover"
    safe_playable.append({"title": escape(p.get("title","")), "url": p.get("url"), "thumb": thumb})
js_list = json.dumps(safe_playable)
init_selected = selected_index if selected_index is not None else 0

html_template = '''
<!doctype html><html><head><meta charset="utf-8"><meta name="viewport" content="width=device-width,initial-scale=1">
<style>
body {margin:0;font-family:sans-serif;color:#e6eef8;background:#071021;}
.container {max-width:900px;margin:12px auto;padding:12px;}
.top-panel {background:#0b2a4a;padding:12px;border-radius:8px;color:#fff;}
.btn-row {display:flex;gap:8px;margin-top:8px;}
.btn {padding:8px 12px;border-radius:6px;background:#1f6feb;color:#fff;border:none;cursor:pointer;}
.player-inline {margin-top:12px;}
.cover-small {width:50%;max-width:320px;border-radius:6px;}
.video-inline {width:100%;max-width:480px;margin-top:8px;border-radius:6px;background:black;}
.list-area {margin-top:12px;max-height:520px;overflow:auto;}
.song-item {display:flex;gap:8px;align-items:center;padding:8px;border-radius:6px;margin-bottom:6px;background:rgba(255,255,255,0.05);}
.song-thumb {width:60px;height:34px;object-fit:cover;border-radius:4px;}
.song-meta {flex:1;}
.small-btn {padding:4px 6px;border-radius:4px;background:transparent;border:1px solid rgba(255,255,255,0.2);color:#cfe8ff;cursor:pointer;}
.selected {background:#1f6feb;color:#fff;}
</style></head><body>
<div class="container">
  <div class="top-panel">
    <div id="selectedTitle" style="font-weight:600;">尚未選擇項目</div>
    <div class="btn-row">
      <button id="btnPlay" class="btn">▶ 播放</button>
      <button id="btnQueue" class="btn">＋ 加入佇列</button>
      <button id="btnRemove" class="btn">🗑 移除</button>
    </div>
    <div class="player-inline">
      <img id="coverImg" class="cover-small" src="https://placehold.co/320x180/0b1b2b/ffffff?text=Cover">
      <video id="video" controls playsinline class="video-inline"></video>
    </div>
  </div>
  <div style="margin-top:12px;font-weight:600;color:#cfe8ff;">候選清單</div>
  <div id="listArea" class="list-area"></div>
</div>
<script src="https://cdn.jsdelivr.net/npm/hls.js@1.4.0/dist/hls.min.js"></script>
<script>
const list={JS_LIST};let selectedIndex={INIT_SELECTED};let queue=[];
const listArea=document.getElementById('listArea'),selectedTitle=document.getElementById('selectedTitle'),
playerTitle=document.getElementById('playerTitle'),coverImg=document.getElementById('coverImg'),video=document.getElementById('video');

function renderList(){listArea.innerHTML='';if(!list||list.length===0){listArea.innerHTML='<div>候選清單為空</div>';return;}
list.forEach((item,i)=>{const div=document.createElement('div');div.className='song-item'+(i===selectedIndex?' selected':'');
div.innerHTML=`<img class="song-thumb" src="${item.thumb}"><div class="song-meta">${i+1}. ${item.title}</div><button class="small-btn select-btn" data-i="${i}">選擇</button>`;
listArea.appendChild(div);});attachSelectHandlers();update
