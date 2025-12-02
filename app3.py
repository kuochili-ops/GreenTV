# app.py
import streamlit as st
from yt_dlp import YoutubeDL
import tempfile
import os
import uuid
import concurrent.futures
import time
import traceback
import json
import re
from html import escape

# -------------------------------
# Page config
# -------------------------------
st.set_page_config(page_title="YouTube 點唱機（單欄）", layout="wide")
st.markdown("<h1 style='margin-bottom:6px;'>🎵 YouTube 點唱機（單欄）</h1>", unsafe_allow_html=True)
st.write("上方為固定操作列（播放 / 加入佇列 / 移除 / 取消靜音），下方為垂直候選清單；播放器使用 HLS（m3u8）。")

# -------------------------------
# Input area (collapsed)
# -------------------------------
with st.expander("輸入 YouTube 影片或播放清單網址（每行一個）", expanded=False):
    urls_input = st.text_area("網址（每行一個）", height=120)
    uploaded_cookies = st.file_uploader("（選擇性）上傳 cookies.txt（Netscape 格式）", type=["txt"])
    parse_btn = st.button("開始解析並產生清單")

# Internal defaults
_default_max_workers = 2
_default_batch_size = 6
_debug_mode = False

# -------------------------------
# yt-dlp helpers
# -------------------------------
def fetch_info(url, cookiefile=None, timeout=30, extract_flat=False, quiet=True):
    opts = {
        "skip_download": True,
        "quiet": quiet is True,
        "no_warnings": quiet is True,
        "socket_timeout": timeout,
    }
    if extract_flat:
        opts["extract_flat"] = True
    if cookiefile:
        opts["cookiefile"] = cookiefile
    with YoutubeDL(opts) as ydl:
        return ydl.extract_info(url, download=False)

def choose_best_m3u8(formats):
    if not formats:
        return None
    candidates = []
    for f in formats:
        proto = (f.get("protocol") or "").lower()
        ext = (f.get("ext") or "").lower()
        note = (f.get("format_note") or "").lower()
        url = f.get("url")
        if not url:
            continue
        if "m3u8" in proto or ext == "m3u8" or "hls" in proto or "hls" in note:
            candidates.append(f)
    if not candidates:
        return None
    candidates.sort(key=lambda f: (int(f.get("height") or 0), float(f.get("tbr") or 0)), reverse=True)
    return candidates[0]

def fetch_playlist_entries_flat(playlist_url, cookiefile=None, timeout=30, quiet=True):
    info = fetch_info(playlist_url, cookiefile=cookiefile, timeout=timeout, extract_flat=True, quiet=quiet)
    entries = info.get("entries") or []
    vids = []
    for e in entries:
        if isinstance(e, dict):
            url = e.get("url") or e.get("webpage_url")
            title = e.get("title") or url
            if url and url.startswith("watch"):
                url = "https://www.youtube.com/" + url
            vids.append({"title": title, "url": url})
        else:
            vids.append({"title": str(e), "url": str(e)})
    return vids

def fetch_best_m3u8_for_video(video_url, cookiefile=None, timeout=25, quiet=True):
    try:
        info = fetch_info(video_url, cookiefile=cookiefile, timeout=timeout, extract_flat=False, quiet=quiet)
        formats = info.get("formats") or []
        best = choose_best_m3u8(formats)
        if best:
            return {"title": info.get("title") or video_url, "url": best.get("url"), "height": best.get("height"), "webpage_url": info.get("webpage_url")}
        else:
            return {"title": info.get("title") or video_url, "url": None, "error": "找不到 m3u8/HLS 格式", "webpage_url": info.get("webpage_url")}
    except Exception as e:
        if _debug_mode:
            return {"title": video_url, "url": None, "error": f"{str(e)}\n{traceback.format_exc()}"}
        return {"title": video_url, "url": None, "error": str(e)}

def export_m3u8_list(results):
    lines = [f"{r['title']} | {r['url']}" for r in results if r.get("url")]
    return "\n".join(lines)

# -------------------------------
# Parse button logic
# -------------------------------
if parse_btn:
    urls = [u.strip() for u in urls_input.splitlines() if u.strip()]
    if not urls:
        st.warning("請輸入至少一個網址")
    else:
        cookiefile_path = None
        if uploaded_cookies:
            tmp = tempfile.NamedTemporaryFile(delete=False)
            tmp.write(uploaded_cookies.getbuffer())
            tmp.flush()
            tmp.close()
            cookiefile_path = tmp.name
            st.info("已上傳 cookies（暫存），解析時會使用它。")

        to_process = []
        with st.spinner("展開並列出影片條目..."):
            for u in urls:
                if "playlist" in u or "list=" in u:
                    try:
                        flat = fetch_playlist_entries_flat(u, cookiefile=cookiefile_path, quiet=not _debug_mode)
                        if not flat:
                            st.warning(f"Playlist {u} 未列出任何條目或為私人/受限。")
                        for e in flat:
                            if e.get("url"):
                                to_process.append({"title": e.get("title"), "url": e.get("url")})
                    except Exception as e:
                        if _debug_mode:
                            st.error(f"列出 playlist 失敗：{u}\n{traceback.format_exc()}")
                        else:
                            st.warning(f"列出 playlist 失敗：{u} → {e}")
                        to_process.append({"title": u, "url": u})
                else:
                    to_process.append({"title": u, "url": u})

        total_estimate = len(to_process)
        st.info(f"總共要解析 {total_estimate} 支影片（分批並行處理）")

        results = []
        if total_estimate > 0:
            overall_progress = st.progress(0)
            status = st.empty()
            done = 0
            max_workers = _default_max_workers
            batch_size = _default_batch_size
            for batch_start in range(0, total_estimate, int(batch_size)):
                batch = to_process[batch_start: batch_start + int(batch_size)]
                status.text(f"處理第 {batch_start + 1} 到 {batch_start + len(batch)} 支影片...")
                with concurrent.futures.ThreadPoolExecutor(max_workers=int(max_workers)) as ex:
                    future_to_item = {ex.submit(fetch_best_m3u8_for_video, item["url"], cookiefile_path, 25, not _debug_mode): item for item in batch}
                    for fut in concurrent.futures.as_completed(future_to_item):
                        item = future_to_item[fut]
                        try:
                            res = fut.result()
                        except Exception as exc:
                            if _debug_mode:
                                res = {"title": item.get("title") or item.get("url"), "url": None, "error": f"{str(exc)}\n{traceback.format_exc()}"}
                            else:
                                res = {"title": item.get("title") or item.get("url"), "url": None, "error": str(exc)}
                        if item.get("title") and (not res.get("title") or res.get("title") == item.get("url")):
                            res["title"] = item.get("title")
                        results.append(res)
                        done += 1
                        overall_progress.progress(min(done / max(total_estimate, 1), 1.0))
                time.sleep(0.2)
            status.text("解析完成")
            time.sleep(0.3)
            status.empty()
            overall_progress.empty()

        if cookiefile_path and os.path.exists(cookiefile_path):
            try:
                os.remove(cookiefile_path)
            except Exception:
                pass

        playable = [r for r in results if r.get("url")]
        unavailable = [r for r in results if not r.get("url")]

        st.session_state["playable"] = playable
        st.session_state["unavailable"] = unavailable
        if "queue" not in st.session_state:
            st.session_state["queue"] = []
        st.session_state["selected_index"] = 0 if playable else None
        st.success(f"解析完成：可播放 {len(playable)} 項，無法取得 {len(unavailable)} 項")

# -------------------------------
# Prepare data for HTML embed (include thumbnail)
# -------------------------------
def youtube_id_from_url(url):
    if not url:
        return None
    m = re.search(r"(?:v=|\/)([0-9A-Za-z_-]{11})(?:[&?#]|$)", url)
    if m:
        return m.group(1)
    return None

playable = st.session_state.get("playable", [])
unavailable = st.session_state.get("unavailable", [])
queue = st.session_state.get("queue", [])
selected_index = st.session_state.get("selected_index", None)
selected_play = st.session_state.get("selected_m3u8", None)

safe_playable = []
for p in playable:
    title = p.get("title", "")[:300]
    url = p.get("url")
    # try to get video id from webpage_url or url
    vid = None
    if p.get("webpage_url"):
        vid = youtube_id_from_url(p.get("webpage_url"))
    if not vid:
        vid = youtube_id_from_url(url)
    if vid:
        thumb = f"https://i.ytimg.com/vi/{vid}/hqdefault.jpg"
    else:
        thumb = "https://placehold.co/640x360/0b1b2b/ffffff?text=No+Cover"
    safe_playable.append({
        "title": escape(title),
        "url": url,
        "thumb": thumb
    })
js_list = json.dumps(safe_playable)

init_selected = selected_index if selected_index is not None else 0

# -------------------------------
# HTML template (single-column: top sticky panel, vertical list, player below)
# -------------------------------
html_template = '''
<!doctype html>
<html>
<head>
<meta charset="utf-8">
<meta name="viewport" content="width=device-width,initial-scale=1">
<style>
  body { margin:0; font-family: -apple-system, BlinkMacSystemFont, "Segoe UI", Roboto, "Helvetica Neue", Arial; color:#e6eef8; background:transparent; }
  .container { max-width:1100px; margin:12px auto; padding:12px; box-sizing:border-box; }
  .top-panel { position:sticky; top:12px; background:#0b2a4a; padding:12px; border-radius:8px; margin-bottom:12px; color:#ffffff; z-index:10; }
  .top-row { display:flex; gap:8px; align-items:center; flex-wrap:wrap; }
  .btn { padding:8px 12px; border-radius:6px; background:#1f6feb; color:white; border:none; cursor:pointer; }
  .btn.green { background:#2ecc71; }
  .mute-note { margin-left:12px; color:#ffd; font-size:13px; }
  .player-area { margin-top:12px; background:linear-gradient(180deg,#071021,#0b1b2b); padding:12px; border-radius:8px; }
  video { width:100%; max-width:960px; height:auto; background:black; border-radius:6px; display:block; margin-bottom:8px; }
  .cover { width:100%; max-width:640px; border-radius:6px; display:block; margin-bottom:8px; }
  .controls { display:flex; gap:8px; align-items:center; margin-bottom:8px; flex-wrap:wrap; }
  .list-area { margin-top:12px; max-height:520px; overflow:auto; padding-right:6px; }
  .song-item { display:flex; gap:12px; align-items:center; padding:10px; border-radius:6px; margin-bottom:8px; background:rgba(255,255,255,0.02); color:#e6eef8; }
  .song-thumb { width:120px; height:68px; object-fit:cover; border-radius:6px; flex-shrink:0; }
  .song-meta { flex:1; }
  .small-btn { padding:6px 8px; border-radius:6px; background:transparent; border:1px solid rgba(255,255,255,0.06); color:#cfe8ff; cursor:pointer; }
  .selected { background:#1f6feb; color:#ffffff; }
  @media (max-width:900px) {
    .song-thumb { width:84px; height:48px; }
    .top-row { flex-direction:column; align-items:flex-start; }
  }
</style>
</head>
<body>
<div class="container">
  <div class="top-panel">
    <div class="top-row">
      <div id="selectedTitle" style="font-weight:600;">尚未選擇項目</div>
      <div style="flex:1"></div>
      <button id="btnPlay" class="btn">▶ 播放</button>
      <button id="btnQueue" class="btn">＋ 加入佇列</button>
      <button id="btnRemove" class="btn">🗑 移除</button>
      <button id="btnUnmute" class="btn green">取消靜音</button>
      <div id="muteNote" class="mute-note">預設為有聲播放；若瀏覽器阻擋自動播放，請按播放或取消靜音</div>
    </div>
  </div>

  <div class="player-area">
    <div id="playerTitle" style="font-weight:600; margin-bottom:6px;">播放器</div>
    <img id="coverImg" class="cover" src="https://placehold.co/640x360/0b1b2b/ffffff?text=YouTube+Cover" alt="cover">
    <video id="video" controls playsinline></video>
    <div class="controls">
      <button id="prevBtn" class="small-btn">◀ 上一首</button>
      <button id="nextBtn" class="small-btn">下一首 ▶</button>
      <label style="color:#cfe8ff; margin-left:8px;">音量</label>
      <input id="vol" type="range" min="0" max="100" value="80" style="margin-left:8px;">
      <label style="color:#cfe8ff; margin-left:12px;"><input id="loop" type="checkbox"> 循環</label>
      <label style="color:#cfe8ff; margin-left:8px;"><input id="shuffle" type="checkbox"> 隨機</label>
    </div>
  </div>

  <div style="margin-top:12px; font-weight:600; color:#cfe8ff;">候選清單（垂直）</div>
  <div id="listArea" class="list-area"></div>
</div>

<script src="https://cdn.jsdelivr.net/npm/hls.js@1.4.0/dist/hls.min.js"></script>
<script>
  const list = {JS_LIST};
  let selectedIndex = {INIT_SELECTED};
  let queue = [];
  const listArea = document.getElementById('listArea');
  const selectedTitle = document.getElementById('selectedTitle');
  const playerTitle = document.getElementById('playerTitle');
  const coverImg = document.getElementById('coverImg');
  const video = document.getElementById('video');
  const vol = document.getElementById('vol');
  const loopCheckbox = document.getElementById('loop');
  const shuffleCheckbox = document.getElementById('shuffle');
  const btnUnmute = document.getElementById('btnUnmute');
  const muteNote = document.getElementById('muteNote');

  // autoplay default: not muted
  let autoplayMuted = false;

  function renderList(showThumbs=true) {
    listArea.innerHTML = '';
    if (!list || list.length === 0) {
      listArea.innerHTML = '<div style="color:#cfe8ff;">候選清單為空，請先在上方輸入網址並解析。</div>';
      selectedTitle.innerText = '尚未選擇項目';
      playerTitle.innerText = '播放器';
      coverImg.src = 'https://placehold.co/640x360/0b1b2b/ffffff?text=YouTube+Cover';
      video.src = '';
      return;
    }
    list.forEach((item, i) => {
      const div = document.createElement('div');
      div.className = 'song-item' + (i === selectedIndex ? ' selected' : '');
      const thumb = item.thumb || 'https://placehold.co/640x360/0b1b2b/ffffff?text=No+Cover';
      const metaHtml = `<div class="song-meta"><div style="font-weight:600;">${i+1}. ${item.title}</div></div>`;
      const thumbHtml = `<img class="song-thumb" src="${thumb}" alt="thumb">`;
      div.innerHTML = `${thumbHtml}${metaHtml}<div><button class="small-btn select-btn" data-i="${i}">選擇</button></div>`;
      listArea.appendChild(div);
    });
    attachSelectHandlers();
    updateSelectedUI(false);
  }

  function attachSelectHandlers() {
    document.querySelectorAll('.select-btn').forEach(btn => {
      btn.onclick = (e) => {
        const i = parseInt(e.target.dataset.i);
        selectedIndex = i;
        renderList();
        e.target.closest('.song-item').scrollIntoView({behavior:'smooth', block:'center'});
      };
    });
  }

  function updateSelectedUI(autoplay=true) {
    if (!list || list.length === 0) return;
    const cur = list[selectedIndex];
    selectedTitle.innerText = `選擇：${selectedIndex+1}. ${cur.title}`;
    playerTitle.innerText = cur.title;
    try { coverImg.src = cur.thumb || 'https://placehold.co/640x360/0b1b2b/ffffff?text=No+Cover'; } catch(e){}
    loadHls(cur.url, autoplay);
  }

  function loadHls(url, autoplay=false) {
    if (!url) return;
    try { video.muted = !!autoplayMuted; } catch(e) {}
    if (video.canPlayType('application/vnd.apple.mpegurl')) {
      video.src = url;
      if (autoplay) try { video.play().catch(()=>{}); } catch(e){}
    } else if (Hls.isSupported()) {
      if (window._hls_instance) {
        try { window._hls_instance.destroy(); } catch(e) {}
        window._hls_instance = null;
      }
      const hls = new Hls();
      window._hls_instance = hls;
      hls.loadSource(url);
      hls.attachMedia(video);
      hls.on(Hls.Events.MANIFEST_PARSED, function() {
        if (autoplay) {
          video.play().catch(()=>{});
        }
      });
    } else {
      video.src = url;
      if (autoplay) try { video.play().catch(()=>{}); } catch(e){}
    }
  }

  function renderQueue() {
    // queue display is integrated into list area top if needed; keep simple here
    // (we already show queue items in list if desired)
  }

  // Play button: user interaction -> unmute and play
  document.getElementById('btnPlay').onclick = () => {
    if (!list || list.length === 0) return;
    try { video.muted = false; autoplayMuted = false; } catch(e){}
    try { video.play(); } catch(e){}
  };

  // Queue / Remove
  document.getElementById('btnQueue').onclick = () => {
    if (!list || list.length === 0) return;
    const item = list[selectedIndex];
    if (!queue.find(q => q.url === item.url)) {
      queue.push(item);
    }
    // optional: visual feedback
    muteNote.innerText = `已加入佇列（共 ${queue.length} 首）`;
    setTimeout(()=>{ muteNote.innerText = ''; }, 1600);
  };
  document.getElementById('btnRemove').onclick = () => {
    if (!list || list.length === 0) return;
    list.splice(selectedIndex, 1);
    if (selectedIndex >= list.length) selectedIndex = Math.max(0, list.length - 1);
    renderList();
  };

  // Unmute button: user interaction
  btnUnmute.onclick = () => {
    try {
      video.muted = false;
      autoplayMuted = false;
      muteNote.innerText = '已取消靜音';
      setTimeout(()=>{ muteNote.innerText = ''; }, 2000);
    } catch(e) {}
  };

  // Prev / Next follow stored list order (manual navigation ignores queue)
  document.getElementById('prevBtn').onclick = () => {
    if (!list || list.length === 0) return;
    if (shuffleCheckbox.checked) {
      selectedIndex = Math.floor(Math.random() * list.length);
    } else {
      selectedIndex = (selectedIndex - 1 + list.length) % list.length;
    }
    renderList();
  };
  document.getElementById('nextBtn').onclick = () => {
    if (!list || list.length === 0) return;
    if (shuffleCheckbox.checked) {
      selectedIndex = Math.floor(Math.random() * list.length);
    } else {
      selectedIndex = (selectedIndex + 1) % list.length;
    }
    renderList();
  };

  vol.oninput = () => { video.volume = vol.value / 100.0; };

  // Auto-advance on ended:
  // 1) If queue has items, play queue.shift()
  // 2) Else advance in list (respect shuffle and loop)
  video.addEventListener('ended', () => {
    if (queue.length > 0) {
      const next = queue.shift();
      const idx = list.findIndex(x => x.url === next.url);
      if (idx >= 0) {
        selectedIndex = idx;
        renderList();
        loadHls(list[selectedIndex].url, true);
      } else {
        loadHls(next.url, true);
      }
      return;
    }

    if (!list || list.length === 0) return;
    if (shuffleCheckbox.checked) {
      selectedIndex = Math.floor(Math.random() * list.length);
    } else {
      selectedIndex = (selectedIndex + 1) % list.length;
    }

    if (!loopCheckbox.checked && !shuffleCheckbox.checked && selectedIndex === 0) {
      return;
    }

    renderList();
    loadHls(list[selectedIndex].url, true);
  });

  // initial render
  renderList();
</script>
</body>
</html>
'''

# inject data
html_template = html_template.replace("{JS_LIST}", js_list).replace("{INIT_SELECTED}", str(init_selected))

# render
st.components.v1.html(html_template, height=900, scrolling=True)

# Streamlit-side controls
st.markdown("---")
col_a, col_b, col_c = st.columns([1,1,1])
with col_a:
    if st.button("下載 m3u8 清單"):
        st.download_button("下載 m3u8 清單", export_m3u8_list(playable), file_name="m3u8_list.txt")
with col_b:
    if st.button("清空所有資料"):
        for k in ["playable", "unavailable", "queue", "selected_index", "selected_m3u8"]:
            if k in st.session_state:
                st.session_state.pop(k, None)
        try:
            if hasattr(st, "experimental_rerun"):
                st.experimental_rerun()
            else:
                st.stop()
        except Exception:
            st.stop()
with col_c:
    st.write("提示：清單為垂直排列；上一/下一鍵依清單順序切換；若自動播放被阻擋，請按播放或取消靜音。")
