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
from html import escape

# -------------------------------
# Streamlit page config
# -------------------------------
st.set_page_config(page_title="YouTube 點唱機（HTML 嵌入）", layout="wide")
st.markdown("<h1 style='margin-bottom:6px;'>🎵 YouTube 點唱機（HTML 嵌入）</h1>", unsafe_allow_html=True)
st.write("上方為固定操作列（播放 / 加入佇列 / 移除），下方為可滑動候選清單；播放器使用 HLS（m3u8）。")

# -------------------------------
# Input area (collapsed) - simplified (removed parallel/batch/debug controls)
# -------------------------------
with st.expander("輸入 YouTube 影片或播放清單網址（每行一個）", expanded=False):
    urls_input = st.text_area("網址（每行一個）", height=120)
    uploaded_cookies = st.file_uploader("（選擇性）上傳 cookies.txt（Netscape 格式）", type=["txt"])
    parse_btn = st.button("開始解析並產生清單")

# Default internal parameters (fixed)
_default_max_workers = 2
_default_batch_size = 6
_debug_mode = False

# -------------------------------
# yt-dlp helper functions
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
            return {"title": info.get("title") or video_url, "url": best.get("url"), "height": best.get("height")}
        else:
            return {"title": info.get("title") or video_url, "url": None, "error": "找不到 m3u8/HLS 格式"}
    except Exception as e:
        if _debug_mode:
            return {"title": video_url, "url": None, "error": f"{str(e)}\n{traceback.format_exc()}"}
        return {"title": video_url, "url": None, "error": str(e)}

def export_m3u8_list(results):
    lines = [f"{r['title']} | {r['url']}" for r in results if r.get("url")]
    return "\n".join(lines)

# -------------------------------
# Parse button logic (uses fixed defaults)
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
            # use fixed defaults
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
        # selected_index records which item in the scroll list is highlighted
        st.session_state["selected_index"] = 0 if playable else None
        st.success(f"解析完成：可播放 {len(playable)} 項，無法取得 {len(unavailable)} 項")

# -------------------------------
# Prepare data for HTML embed
# -------------------------------
playable = st.session_state.get("playable", [])
unavailable = st.session_state.get("unavailable", [])
queue = st.session_state.get("queue", [])
selected_index = st.session_state.get("selected_index", None)
selected_play = st.session_state.get("selected_m3u8", None)

# Convert playable to safe JSON for injection
safe_playable = []
for p in playable:
    safe_playable.append({
        "title": escape(p.get("title", "")[:300]),
        "url": p.get("url")
    })
js_list = json.dumps(safe_playable)

# initial selected index for front-end
init_selected = selected_index if selected_index is not None else 0

# -------------------------------
# HTML template (ordinary triple-quoted string, placeholders {JS_LIST} and {INIT_SELECTED})
# - top-panel background set to opaque; selected item style adjusted
# -------------------------------
html_template = '''
<!doctype html>
<html>
<head>
<meta charset="utf-8">
<meta name="viewport" content="width=device-width,initial-scale=1">
<style>
  body { margin:0; font-family: -apple-system, BlinkMacSystemFont, "Segoe UI", Roboto, "Helvetica Neue", Arial; color:#e6eef8; background:transparent; }
  .wrap { display:flex; gap:18px; padding:12px; box-sizing:border-box; }
  .left { width:36%; min-width:260px; background:#0f1724; padding:12px; border-radius:10px; box-sizing:border-box; }
  .right { flex:1; background:linear-gradient(180deg,#071021,#0b1b2b); padding:18px; border-radius:10px; box-sizing:border-box; color:#fff; }
  /* top-panel now opaque to avoid overlap with list text */
  .top-panel { position:sticky; top:12px; background:#0b2a4a; padding:12px; border-radius:8px; margin-bottom:12px; color:#ffffff; }
  .scroll-area { max-height:520px; overflow:auto; padding-right:6px; }
  .song-item { padding:10px; border-radius:6px; margin-bottom:8px; background:rgba(255,255,255,0.02); display:flex; align-items:center; justify-content:space-between; color:#e6eef8; }
  .song-meta { flex:1; padding-right:12px; color:#e6eef8; }
  .queue-item { padding:6px 8px; border-radius:6px; background:rgba(255,255,255,0.02); margin-bottom:6px; color:#e6eef8; }
  .btn { padding:8px 12px; border-radius:6px; background:#1f6feb; color:white; border:none; cursor:pointer; }
  .small-btn { padding:6px 8px; border-radius:6px; background:transparent; border:1px solid rgba(255,255,255,0.06); color:#cfe8ff; cursor:pointer; }
  /* selected item: use solid background to avoid text overlap */
  .selected { background:#1f6feb; color:#ffffff; outline: none; }
  video { background:black; border-radius:6px; }
  @media (max-width:900px) {
    .wrap { flex-direction:column; }
    .left { width:100%; }
    .right { width:100%; }
  }
</style>
</head>
<body>
<div class="wrap">
  <div class="left">
    <div class="top-panel">
      <div id="selectedTitle" style="font-weight:600; margin-bottom:8px;">尚未選擇項目</div>
      <div style="display:flex; gap:8px;">
        <button id="btnPlay" class="btn">▶ 播放</button>
        <button id="btnQueue" class="btn">＋ 加入佇列</button>
        <button id="btnRemove" class="btn">🗑 移除</button>
      </div>
    </div>

    <div style="margin-top:8px; font-weight:600; color:#cfe8ff;">候選清單（滑動視窗）</div>
    <div id="scrollList" class="scroll-area" style="margin-top:8px;"></div>

    <div style="margin-top:12px; font-weight:600; color:#cfe8ff;">播放佇列</div>
    <div id="queueList" style="margin-top:8px;"></div>
  </div>

  <div class="right">
    <div id="playerTitle" style="font-size:18px; font-weight:600; margin-bottom:8px;">播放器</div>
    <div id="cover" style="margin-bottom:12px;">
      <img id="coverImg" src="https://placehold.co/640x360/0b1b2b/ffffff?text=YouTube+Cover" alt="cover" style="width:100%; max-width:640px; border-radius:6px;">
    </div>
    <div>
      <video id="video" controls playsinline style="width:100%; max-width:960px; height:auto;"></video>
    </div>
    <div style="margin-top:12px; display:flex; gap:8px; align-items:center;">
      <button id="prevBtn" class="small-btn">◀ 上一首</button>
      <button id="nextBtn" class="small-btn">下一首 ▶</button>
      <label style="margin-left:12px; color:#cfe8ff;">音量</label>
      <input id="vol" type="range" min="0" max="100" value="80" style="margin-left:8px;">
      <label style="margin-left:12px; color:#cfe8ff;"><input id="loop" type="checkbox"> 循環</label>
      <label style="margin-left:8px; color:#cfe8ff;"><input id="shuffle" type="checkbox"> 隨機</label>
    </div>
  </div>
</div>

<script src="https://cdn.jsdelivr.net/npm/hls.js@1.4.0/dist/hls.min.js"></script>
<script>
  // 由 Python 注入的資料佔位符（稍後用 replace 注入）
  const list = {JS_LIST};
  let selectedIndex = {INIT_SELECTED};
  let queue = [];
  const scrollList = document.getElementById('scrollList');
  const queueList = document.getElementById('queueList');
  const selectedTitle = document.getElementById('selectedTitle');
  const playerTitle = document.getElementById('playerTitle');
  const video = document.getElementById('video');
  const vol = document.getElementById('vol');
  const loopCheckbox = document.getElementById('loop');
  const shuffleCheckbox = document.getElementById('shuffle');

  function renderList() {
    scrollList.innerHTML = '';
    if (!list || list.length === 0) {
      scrollList.innerHTML = '<div style="color:#cfe8ff;">候選清單為空，請先在左上輸入網址並解析。</div>';
      selectedTitle.innerText = '尚未選擇項目';
      playerTitle.innerText = '播放器';
      video.src = '';
      return;
    }
    list.forEach((item, i) => {
      const div = document.createElement('div');
      div.className = 'song-item' + (i === selectedIndex ? ' selected' : '');
      div.innerHTML = `<div style="flex:1; padding-right:12px;">${i+1}. ${item.title}</div>
                       <div><button class="small-btn select-btn" data-i="${i}">選擇</button></div>`;
      scrollList.appendChild(div);
    });
    attachSelectHandlers();
    updateSelectedUI();
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

  function updateSelectedUI() {
    if (!list || list.length === 0) return;
    const cur = list[selectedIndex];
    selectedTitle.innerText = `選擇：${selectedIndex+1}. ${cur.title}`;
    playerTitle.innerText = cur.title;
    loadHls(cur.url);
  }

  function loadHls(url) {
    if (!url) return;
    if (video.canPlayType('application/vnd.apple.mpegurl')) {
      video.src = url;
    } else if (Hls.isSupported()) {
      if (window._hls_instance) {
        try { window._hls_instance.destroy(); } catch(e) {}
        window._hls_instance = null;
      }
      const hls = new Hls();
      window._hls_instance = hls;
      hls.loadSource(url);
      hls.attachMedia(video);
    } else {
      video.src = url;
    }
  }

  function renderQueue() {
    queueList.innerHTML = '';
    if (!queue.length) {
      queueList.innerHTML = '<div style="color:#cfe8ff;">佇列為空</div>';
      return;
    }
    queue.forEach((q, idx) => {
      const d = document.createElement('div');
      d.className = 'queue-item';
      d.innerText = `${idx+1}. ${q.title}`;
      queueList.appendChild(d);
    });
  }

  document.getElementById('btnPlay').onclick = () => { if (!list.length) return; try { video.play(); } catch(e) {} };
  document.getElementById('btnQueue').onclick = () => { if (!list.length) return; const item = list[selectedIndex]; if (!queue.find(q => q.url === item.url)) { queue.push(item); renderQueue(); } };
  document.getElementById('btnRemove').onclick = () => { if (!list.length) return; list.splice(selectedIndex, 1); if (selectedIndex >= list.length) selectedIndex = Math.max(0, list.length - 1); renderList(); renderQueue(); };

  document.getElementById('prevBtn').onclick = () => {
    if (!list.length) return;
    if (shuffleCheckbox.checked) selectedIndex = Math.floor(Math.random() * list.length);
    else selectedIndex = (selectedIndex - 1 + list.length) % list.length;
    renderList();
  };
  document.getElementById('nextBtn').onclick = () => {
    if (!list.length) return;
    if (shuffleCheckbox.checked) selectedIndex = Math.floor(Math.random() * list.length);
    else selectedIndex = (selectedIndex + 1) % list.length;
    renderList();
  };

  vol.oninput = () => { video.volume = vol.value / 100.0; };

  video.addEventListener('ended', () => {
    if (!list.length) return;
    if (shuffleCheckbox.checked) selectedIndex = Math.floor(Math.random() * list.length);
    else selectedIndex = (selectedIndex + 1) % list.length;
    if (!loopCheckbox.checked && selectedIndex === 0 && !shuffleCheckbox.checked) return;
    renderList();
  });

  renderList();
  renderQueue();
</script>
</body>
</html>
'''

# 注入資料（用 replace，不用 f-string）
html_template = html_template.replace("{JS_LIST}", js_list).replace("{INIT_SELECTED}", str(init_selected))

# -------------------------------
# Render HTML component
# -------------------------------
st.components.v1.html(html_template, height=720, scrolling=True)

# Streamlit-side controls
st.markdown("---")
col_a, col_b, col_c = st.columns([1,1,1])
with col_a:
    if st.button("下載 m3u8 清單"):
        st.download_button("下載 m3u8 清單", export_m3u8_list(playable), file_name="m3u8_list.txt")
with col_b:
    # 安全的清空所有資料按鈕（避免在某些環境呼叫不存在的 experimental_rerun）
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
    st.write("提示：滑動清單點「選擇」，再用上方操作列控制播放或加入佇列。")
