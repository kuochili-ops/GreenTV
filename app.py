# app.py
import streamlit as st
from yt_dlp import YoutubeDL
import requests
import uuid
import tempfile
import os
from urllib.parse import urlparse

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

# --- UI: cookies 上傳與 playlist 輸入 ---
uploaded_cookies = st.file_uploader("（選擇性）上傳 YouTube cookies.txt（Netscape 格式）以供抓取時使用", type=["txt"])
playlist_input = st.text_input("（選用）貼上 YouTube playlist 或 channel URL（會嘗試把 playlist 轉成連播清單）", "")

# --- 抓取頻道與 playlist（自動在 session_state） ---
def build_tv_list(channels, playlist_url=None, cookiefile_path=None):
    results = []
    # 先處理單台清單（預設頻道）
    for ch in channels:
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
        results.append(item)

    # 若有 playlist_url，嘗試抓取 playlist entries 並為每支影片找最佳 m3u8
    playlist_items = []
    if playlist_url:
        try:
            # 先用 yt-dlp 抓取 playlist metadata（包含 entries）
            ydl_opts = {"skip_download": True, "quiet": True, "no_warnings": True}
            if cookiefile_path:
                ydl_opts["cookiefile"] = cookiefile_path
            with YoutubeDL(ydl_opts) as ydl:
                info = ydl.extract_info(playlist_url, download=False)
            entries = info.get("entries") or []
            for e in entries:
                # 每個 entry 可能是 dict 或 url string
                try:
                    # 若 entry 是 dict 且包含 id 或 webpage_url，組成影片 URL
                    if isinstance(e, dict):
                        vid_url = e.get("webpage_url") or e.get("url")
                        title = e.get("title") or vid_url
                    else:
                        vid_url = e
                        title = vid_url
                    if not vid_url:
                        continue
                    # 取得該影片的 formats
                    try:
                        vinfo = fetch_info(vid_url, cookiefile=cookiefile_path)
                        formats = vinfo.get("formats") or []
                        best = choose_best_m3u8(formats)
                        if best:
                            playlist_items.append({
                                "name": title,
                                "url": best.get("url"),
                                "height": best.get("height") or best.get("tbr") or None
                            })
                        else:
                            playlist_items.append({
                                "name": title,
                                "url": None,
                                "error": "找不到 m3u8"
                            })
                    except Exception as ve:
                        playlist_items.append({
                            "name": title,
                            "url": None,
                            "error": str(ve)
                        })
                except Exception:
                    continue
        except Exception as e:
            st.warning(f"抓取 playlist 失敗：{e}")

    return results, playlist_items

# 若尚未抓取，執行一次（會在 session_state 儲存）
if "tv_channels" not in st.session_state:
    cookiefile_path = None
    if uploaded_cookies:
        tmp = tempfile.NamedTemporaryFile(delete=False)
        tmp.write(uploaded_cookies.getbuffer())
        tmp.flush()
        tmp.close()
        cookiefile_path = tmp.name
        st.info("已上傳 cookies（暫存），抓取階段會使用它（若需要）。")

    channels_res, playlist_res = build_tv_list(CHANNELS, playlist_url=playlist_input or None, cookiefile_path=cookiefile_path)

    # 清理暫存 cookie 檔
    if cookiefile_path and os.path.exists(cookiefile_path):
        try:
            os.remove(cookiefile_path)
        except Exception:
            pass

    st.session_state["tv_channels"] = channels_res
    st.session_state["playlist_items"] = playlist_res

# --- 前端播放器資料準備 ---
channels = st.session_state.get("tv_channels", [])
playlist_items = st.session_state.get("playlist_items", [])

# 合併來源：若有 playlist 並且 playlist_items 有可用 url，優先使用 playlist播放模式
playable_playlist = [p for p in playlist_items if p.get("url")]
playable_channels = [c for c in channels if c.get("best_url")]

# UI 顯示與播放器行為
st.markdown("### 播放器")
if playable_playlist:
    st.info(f"偵測到 playlist，將以 playlist 連播模式播放，共 {len(playable_playlist)} 支影片（會依序播放）")
    player_list = playable_playlist  # 使用 playlist
else:
    player_list = playable_channels  # 使用單台清單（頻道）

if not player_list:
    st.warning("目前沒有可播放的 m3u8 項目。請檢查是否需要 cookies 或該影片是否使用 HLS。")
    # 顯示錯誤項目
    if playlist_items:
        st.markdown("**Playlist 中不可用的項目**")
        for p in playlist_items:
            if not p.get("url"):
                st.write(f"- {p.get('name')}: {p.get('error')}")
    if channels:
        st.markdown("**頻道中不可用的項目**")
        for c in channels:
            if not c.get("best_url"):
                st.write(f"- {c.get('name')}: {c.get('error')}")
else:
    # 產生 player_list 給前端
    player_id = "player_" + uuid.uuid4().hex[:8]
    html = f"""
    <div style="display:flex;flex-direction:column;align-items:center;">
      <div id="{player_id}_title" style="font-weight:600;margin-bottom:8px;">正在播放：{player_list[0]['name']}</div>
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
        const list = {player_list!r};
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
            st.write(f"- {p.get('name')}: {p.get('error')}")

if channels:
    badc = [c for c in channels if not c.get("best_url")]
    if badc:
        st.markdown("**頻道中不可用的項目**")
        for c in badc:
            st.write(f"- {c.get('name')}: {c.get('error')}")
