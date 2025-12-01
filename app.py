# app.py
import streamlit as st
from yt_dlp import YoutubeDL
import requests
import uuid
import tempfile
import os
from urllib.parse import urlparse
import concurrent.futures
import time

st.set_page_config(page_title="YouTube Live TV（含 Playlist 連播）", layout="wide")
st.title("YouTube Live TV（自動播放、左右鍵切台、Playlist 連播）")
st.write("支援單台播放與把 YouTube playlist 轉成可連續播放的清單（客戶端切換）。上傳 cookies.txt 可處理需驗證的影片。")
st.warning("Cookies 含登入憑證，請僅在私有或受信任環境使用；上傳後程式會暫存並嘗試刪除。")

# --- 預設頻道（可保留或移除） ---
CHANNELS = [
    {"name": "三立新聞", "url": "https://www.youtube.com/live/QsGswQvRmtU?si=0tG0FZcoxq5nftxS"},
    {"name": "民視新聞", "url": "https://www.youtube.com/live/ylYJSBUgaMA?si=yBqbwafsMknTq_gT"},
    {"name": "鏡新聞", "url": "https://www.youtube.com/live/5n0y6b0Q25o?si=ZufSUna9wrqjZuZx"},
    {"name": "非凡新聞", "url": "https://www.youtube.com/live/wAUx3pywTt8?si=9RB3z_JhUsQyGwb-"},
]

ALLOWED_HOSTS = ("youtube.com", "www.youtube.com", "youtu.be")

def is_youtube_url(u: str) -> bool:
    try:
        p = urlparse(u)
        host = (p.hostname or "").lower()
        return any(h in host for h in ALLOWED_HOSTS)
    except Exception:
        return False

def fetch_info(url: str, cookiefile: str = None, timeout: int = 30):
    ydl_opts = {"skip_download": True, "quiet": True, "no_warnings": True, "socket_timeout": timeout}
    if cookiefile:
        ydl_opts["cookiefile"] = cookiefile
    with YoutubeDL(ydl_opts) as ydl:
        return ydl.extract_info(url, download=False)

def choose_best_m3u8(formats: list):
    candidates = []
    for f in formats:
        proto = (f.get("protocol") or "").lower()
        ext = (f.get("ext") or "").lower()
        note = (f.get("format_note") or "").lower()
        url_f = f.get("url")
        if not url_f:
            continue
        if "m3u8" in proto or ext == "m3u8" or "hls" in proto or "hls" in note:
            candidates.append(f)
    if not candidates:
        return None
    def score(f):
        h = f.get("height") or 0
        tbr = f.get("tbr") or 0
        return (int(h), float(tbr))
    candidates.sort(key=score, reverse=True)
    return candidates[0]

def fetch_m3u8_text(url: str, cookies=None, timeout=10):
    headers = {"User-Agent": "Mozilla/5.0 (compatible; yt-dlp/streamlit-app)"}
    resp = requests.get(url, headers=headers, cookies=cookies, timeout=timeout)
    resp.raise_for_status()
    return resp.text

# --- Playlist helpers (fast extract + parallel detail fetch) ---
def fetch_playlist_entries_flat(playlist_url, cookiefile=None, timeout=30):
    """
    Use extract_flat to quickly list playlist entries (fast).
    Returns list of dicts: {"title":..., "url":...}
    """
    ydl_opts = {"skip_download": True, "quiet": True, "no_warnings": True, "extract_flat": True, "socket_timeout": timeout}
    if cookiefile:
        ydl_opts["cookiefile"] = cookiefile
    with YoutubeDL(ydl_opts) as ydl:
        info = ydl.extract_info(playlist_url, download=False)
    entries = info.get("entries") or []
    vids = []
    for e in entries:
        if isinstance(e, dict):
            url = e.get("url") or e.get("webpage_url")
            title = e.get("title") or url
            # normalize relative watch?v= ids
            if url and url.startswith("watch"):
                url = "https://www.youtube.com/" + url
            vids.append({"title": title, "url": url})
        else:
            vids.append({"title": str(e), "url": str(e)})
    return vids

def fetch_best_m3u8_for_video(video_url, cookiefile=None, timeout=30):
    """
    Fetch detailed info for a single video and return best m3u8 info or error.
    """
    try:
        ydl_opts = {"skip_download": True, "quiet": True, "no_warnings": True, "socket_timeout": timeout}
        if cookiefile:
            ydl_opts["cookiefile"] = cookiefile
        with YoutubeDL(ydl_opts) as ydl:
            info = ydl.extract_info(video_url, download=False)
        formats = info.get("formats") or []
        best = choose_best_m3u8(formats)
        if best:
            return {"title": info.get("title") or video_url, "url": best.get("url"), "height": best.get("height") or best.get("tbr")}
        else:
            return {"title": info.get("title") or video_url, "url": None, "error": "找不到 m3u8"}
    except Exception as e:
        return {"title": video_url, "url": None, "error": str(e)}

def process_playlist_parallel(playlist_url, cookiefile=None, max_workers=6):
    """
    1) extract_flat to get entries quickly
    2) parallel fetch each video's best m3u8
    Returns list of items with keys: title, url (or None), height, error (optional)
    """
    entries = fetch_playlist_entries_flat(playlist_url, cookiefile=cookiefile)
    total = len(entries)
    results = []
    if total == 0:
        return results, "找不到任何條目或 playlist 為私人/受限"
    # progress UI handled by caller
    with concurrent.futures.ThreadPoolExecutor(max_workers=max_workers) as ex:
        future_to_entry = {ex.submit(fetch_best_m3u8_for_video, e["url"], cookiefile): e for e in entries if e.get("url")}
        done = 0
        for fut in concurrent.futures.as_completed(future_to_entry):
            e = future_to_entry[fut]
            try:
                res = fut.result()
            except Exception as exc:
                res = {"title": e.get("title"), "url": None, "error": str(exc)}
            results.append(res)
            done += 1
    return results, None

# --- UI inputs ---
uploaded_cookies = st.file_uploader("（選擇性）上傳 YouTube cookies.txt（Netscape 格式）以供抓取時使用", type=["txt"])
playlist_input = st.text_input("（選用）貼上 YouTube playlist 或 channel URL（會嘗試把 playlist 轉成連播清單）", "")

# Button to force re-fetch (useful after uploading cookies)
if st.button("重新抓取並啟動播放器（若已上傳 cookies，請先上傳再按）"):
    # 安全移除可能存在的 session keys
    for k in ["tv_channels", "playlist_items", "playlist_error"]:
        if k in st.session_state:
            del st.session_state[k]

    # 嘗試自動重新整理；若失敗則提示並停止執行，避免未處理的例外
    try:
        st.experimental_rerun()
    except Exception as e:
        st.warning("自動重新整理失敗，請手動重新整理頁面（按 F5 或重新載入）。")
        st.write("（debug）rerun 例外：", str(e))
        st.stop()

# --- Build lists if not present ---
if "tv_channels" not in st.session_state or "playlist_items" not in st.session_state:
    cookiefile_path = None
    if uploaded_cookies:
        tmp = tempfile.NamedTemporaryFile(delete=False)
        tmp.write(uploaded_cookies.getbuffer())
        tmp.flush()
        tmp.close()
        cookiefile_path = tmp.name
        st.info("已上傳 cookies（暫存），抓取階段會使用它（若需要）。")

    # fetch default channels (synchronous, small number)
    channels_res = []
    for ch in CHANNELS:
        name = ch["name"]
        url = ch["url"]
        item = {"name": name, "input_url": url, "error": None, "best_url": None, "height": None}
        try:
            info = fetch_info(url, cookiefile=cookiefile_path)
            formats = info.get("formats") or []
            best = choose_best_m3u8(formats)
            if best:
                item["best_url"] = best.get("url")
                item["height"] = best.get("height") or best.get("tbr") or None
            else:
                item["error"] = "找不到 m3u8/HLS 格式"
        except Exception as e:
            item["error"] = str(e)
        channels_res.append(item)

    # process playlist if provided (use parallel processing and show progress)
    playlist_items = []
    playlist_error = None
    if playlist_input:
        st.info("開始解析 playlist 條目（快速列出後並行解析每支影片）...")
        progress_bar = st.progress(0)
        status_text = st.empty()
        try:
            # first get flat entries count
            try:
                flat_entries = fetch_playlist_entries_flat(playlist_input, cookiefile=cookiefile_path)
            except Exception as e:
                flat_entries = []
                playlist_error = f"無法列出 playlist 條目：{e}"
            total = len(flat_entries)
            status_text.text(f"找到 {total} 支影片，開始並行解析（每支影片會嘗試找 m3u8）")
            if total > 0:
                # parallel fetch with progress updates
                results = []
                with concurrent.futures.ThreadPoolExecutor(max_workers=6) as ex:
                    futures = []
                    for e in flat_entries:
                        if e.get("url"):
                            futures.append(ex.submit(fetch_best_m3u8_for_video, e["url"], cookiefile_path))
                        else:
                            results.append({"title": e.get("title"), "url": None, "error": "無影片 URL"})
                    done = 0
                    for fut in concurrent.futures.as_completed(futures):
                        res = fut.result()
                        results.append(res)
                        done += 1
                        progress_bar.progress(min(done / max(total, 1), 1.0))
                playlist_items = results
            else:
                playlist_items = []
        except Exception as e:
            playlist_error = str(e)
        finally:
            progress_bar.progress(1.0)
            status_text.text("解析完成")
            time.sleep(0.3)
            status_text.empty()

    # cleanup cookie temp
    if cookiefile_path and os.path.exists(cookiefile_path):
        try:
            os.remove(cookiefile_path)
        except Exception:
            pass

    st.session_state["tv_channels"] = channels_res
    st.session_state["playlist_items"] = playlist_items
    st.session_state["playlist_error"] = playlist_error

# --- Prepare player list ---
channels = st.session_state.get("tv_channels", [])
playlist_items = st.session_state.get("playlist_items", [])
playlist_error = st.session_state.get("playlist_error", None)

playable_playlist = [p for p in playlist_items if p.get("url")]
playable_channels = [c for c in channels if c.get("best_url")]

st.markdown("### 播放器")
if playlist_input and playlist_error:
    st.warning(f"Playlist 解析警告：{playlist_error}")

if playable_playlist:
    st.info(f"偵測到 playlist，將以 playlist 連播模式播放，共 {len(playable_playlist)} 支影片（會依序播放）")
    player_list = playable_playlist
else:
    player_list = playable_channels

if not player_list:
    st.warning("目前沒有可播放的 m3u8 項目。請檢查是否需要 cookies 或該影片是否使用 HLS。")
    if playlist_items:
        st.markdown("**Playlist 中不可用的項目**")
        for p in playlist_items:
            if not p.get("url"):
                st.write(f"- {p.get('title')}: {p.get('error')}")
    if channels:
        st.markdown("**頻道中不可用的項目**")
        for c in channels:
            if not c.get("best_url"):
                st.write(f"- {c.get('name')}: {c.get('error')}")
else:
    player_id = "player_" + uuid.uuid4().hex[:8]
    # build minimal player_list for JS (title, url, height)
    js_list = [{"name": it.get("title") or it.get("name"), "url": it.get("url"), "height": it.get("height")} for it in player_list]

    html = f"""
    <div style="display:flex;flex-direction:column;align-items:center;">
      <div id="{player_id}_title" style="font-weight:600;margin-bottom:8px;">正在播放：{js_list[0]['name']}</div>
      <video id="{player_id}" controls autoplay playsinline style="width:100%;max-width:960px;height:auto;background:black;"></video>
      <div style="margin-top:8px;">
        <button id="{player_id}_prev">◀ 上一則</button>
        <button id="{player_id}_next">下一則 ▶</button>
        <span id="{player_id}_info" style="margin-left:12px;"></span>
      </div>
      <div id="{player_id}_overlay" style="display:none;margin-top:8px;color:#c33;font-size:14px;">
        自動播放被瀏覽器阻擋，請按播放並取消靜音以聽聲音。
      </div>
    </div>

    <script src="https://cdn.jsdelivr.net/npm/hls.js@1.4.0/dist/hls.min.js"></script>
    <script>
    (function(){{
        const list = {js_list!r};
        let idx = 0;
        const video = document.getElementById("{player_id}");
        const title = document.getElementById("{player_id}_title");
        const info = document.getElementById("{player_id}_info");
        const prevBtn = document.getElementById("{player_id}_prev");
        const nextBtn = document.getElementById("{player_id}_next");
        const overlay = document.getElementById("{player_id}_overlay");

        function updateInfo() {{
            const cur = list[idx];
            title.innerText = "正在播放：" + cur.name;
            info.innerText = (cur.height ? (cur.height + "p") : "") ;
        }}

        function attachHls(url) {{
            if (video.canPlayType('application/vnd.apple.mpegurl')) {{
                video.src = url;
            }} else if (Hls.isSupported()) {{
                if (window._hls_instance) {{
                    try {{ window._hls_instance.destroy(); }} catch(e){{}}
                    window._hls_instance = null;
                }}
                const hls = new Hls();
                window._hls_instance = hls;
                hls.loadSource(url);
                hls.attachMedia(video);
            }} else {{
                video.src = url;
            }}
        }}

        async function loadSrc(url) {{
            // 嘗試非靜音自動播放
            video.muted = false;
            attachHls(url);
            try {{
                await video.play();
                overlay.style.display = "none";
            }} catch (err) {{
                overlay.style.display = "block";
            }}
        }}

        function gotoIndex(newIdx) {{
            if (newIdx < 0) newIdx = list.length - 1;
            if (newIdx >= list.length) newIdx = 0;
            idx = newIdx;
            updateInfo();
            loadSrc(list[idx].url);
        }}

        prevBtn.addEventListener('click', ()=> gotoIndex(idx-1));
        nextBtn.addEventListener('click', ()=> gotoIndex(idx+1));

        // 當影片播放結束時，自動切下一個（playlist 模式或單台模式皆適用）
        video.addEventListener('ended', () => {{
            gotoIndex(idx+1);
        }});

        // 鍵盤左右鍵切台（在全螢幕也有效）
        document.addEventListener('keydown', function(e) {{
            const tag = (document.activeElement && document.activeElement.tagName) || '';
            if (tag === 'INPUT' || tag === 'TEXTAREA' || document.activeElement && document.activeElement.isContentEditable) {{
                return;
            }}
            if (e.key === 'ArrowLeft') {{
                gotoIndex(idx-1);
            }} else if (e.key === 'ArrowRight') {{
                gotoIndex(idx+1);
            }}
        }});

        // 初始載入（從第一項開始）
        updateInfo();
        loadSrc(list[0].url);
    }})();
    </script>
    """
    st.components.v1.html(html, height=600)

    # 下載 M3U（簡單清單）按鈕：把可用項目輸出為 .m3u（每行一個 URL）
    m3u_lines = []
    for it in player_list:
        if it.get("url"):
            m3u_lines.append(it["url"])
    if m3u_lines:
        m3u_text = "\n".join(m3u_lines)
        st.download_button("下載 M3U 清單（每行為一個 m3u8 URL）", data=m3u_text, file_name="playlist.m3u", mime="audio/x-mpegurl")

# 顯示不可用項目（若有）
if playlist_items:
    bad = [p for p in playlist_items if not p.get("url")]
    if bad:
        st.markdown("**Playlist 中不可用的項目**")
        for p in bad:
            st.write(f"- {p.get('title')}: {p.get('error')}")

if channels:
    badc = [c for c in channels if not c.get("best_url")]
    if badc:
        st.markdown("**頻道中不可用的項目**")
        for c in badc:
            st.write(f"- {c.get('name')}: {c.get('error')}")
