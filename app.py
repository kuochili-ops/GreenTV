import streamlit as st
from yt_dlp import YoutubeDL
import requests
import uuid
import tempfile
import os
from urllib.parse import urlparse

st.set_page_config(page_title="YouTube Live TV（自動播放）", layout="wide")
st.title("YouTube Live TV（自動從三立開始播放，左右鍵切台）")
st.write("頁面載入後會自動從三立新聞開始播放；使用鍵盤左右鍵切換頻道。若直播需要登入驗證，請上傳 cookies.txt（私有環境）並重新整理頁面。")
st.warning("Cookies 含登入憑證，請僅在私有或受信任環境使用；上傳後程式會暫存並嘗試刪除。")

# 預設三台（使用者提供）
CHANNELS = [
    {"name": "三立新聞", "url": "https://www.youtube.com/live/QsGswQvRmtU?si=0tG0FZcoxq5nftxS"},
    {"name": "民視新聞", "url": "https://www.youtube.com/live/ylYJSBUgaMA?si=yBqbwafsMknTq_gT"},
    {"name": "鏡新聞", "url": "https://www.youtube.com/live/5n0y6b0Q25o?si=ZufSUna9wrqjZuZx"},
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

# cookies 上傳（選用）
uploaded_cookies = st.file_uploader("（選擇性）上傳 YouTube cookies.txt（Netscape 格式）以供抓取時使用", type=["txt"])

# 若 session_state 中沒有 tv_channels，則在頁面載入時自動抓取並儲存
if "tv_channels" not in st.session_state:
    cookiefile_path = None
    if uploaded_cookies:
        tmp = tempfile.NamedTemporaryFile(delete=False)
        tmp.write(uploaded_cookies.getbuffer())
        tmp.flush()
        tmp.close()
        cookiefile_path = tmp.name
        st.info("已上傳 cookies（暫存），抓取階段會使用它（若需要）。")

    results = []
    for ch in CHANNELS:
        name = ch["name"]
        url = ch["url"]
        item = {"name": name, "input_url": url, "error": None, "best_url": None, "height": None}
        if not is_youtube_url(url):
            item["error"] = "非 YouTube 連結"
            results.append(item)
            continue
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

    # 清理暫存 cookie 檔
    if cookiefile_path and os.path.exists(cookiefile_path):
        try:
            os.remove(cookiefile_path)
        except Exception:
            pass

    st.session_state["tv_channels"] = results

# 若有結果，直接啟動播放器（從三立開始）
if "tv_channels" in st.session_state:
    channels = st.session_state["tv_channels"]
    playable = [c for c in channels if c.get("best_url")]
    unavailable = [c for c in channels if not c.get("best_url")]

    if not playable:
        st.warning("目前沒有可播放的頻道。請檢查是否需要 cookies 或該直播是否使用 HLS。")
        for u in unavailable:
            st.write(f"- {u['name']}: {u.get('error')}")
    else:
        # 確保三立為第一台（若輸入順序不同，可調整）
        # 這裡假設 CHANNELS 第一項為三立
        player_list = []
        for c in playable:
            player_list.append({
                "name": c["name"],
                "url": c["best_url"],
                "height": c.get("height")
            })

        player_id = "player_" + uuid.uuid4().hex[:8]

        # 內嵌播放器：自動播放（從第一台開始），左右鍵切台
        html = f"""
        <div style="display:flex;flex-direction:column;align-items:center;">
          <div id="{player_id}_title" style="font-weight:600;margin-bottom:8px;">正在播放：{player_list[0]['name']}</div>
          <video id="{player_id}" controls autoplay playsinline muted style="width:100%;max-width:960px;height:auto;background:black;"></video>
          <div style="margin-top:8px;">
            <button id="{player_id}_prev">◀ 上一台</button>
            <button id="{player_id}_next">下一台 ▶</button>
            <span id="{player_id}_info" style="margin-left:12px;"></span>
          </div>
          <div style="margin-top:6px;color:#666;font-size:13px;">提示：使用鍵盤左右鍵也可切換頻道；若無聲音請取消靜音。</div>
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

            function updateInfo() {{
                const cur = list[idx];
                title.innerText = "正在播放：" + cur.name;
                info.innerText = (cur.height ? (cur.height + "p") : "") ;
            }}

            function loadSrc(url) {{
                // 嘗試自動播放：先靜音以提高自動播放成功率
                video.muted = true;
                if (video.canPlayType('application/vnd.apple.mpegurl')) {{
                    video.src = url;
                    video.play().catch(()=>{{}});
                }} else if (Hls.isSupported()) {{
                    if (window._hls_instance) {{
                        try {{ window._hls_instance.destroy(); }} catch(e){{}}
                        window._hls_instance = null;
                    }}
                    const hls = new Hls();
                    window._hls_instance = hls;
                    hls.loadSource(url);
                    hls.attachMedia(video);
                    hls.on(Hls.Events.MANIFEST_PARSED, function() {{
                        video.play().catch(()=>{{}});
                    }});
                }} else {{
                    video.src = url;
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

            document.addEventListener('keydown', function(e) {{
                if (e.key === 'ArrowLeft') {{
                    gotoIndex(idx-1);
                }} else if (e.key === 'ArrowRight') {{
                    gotoIndex(idx+1);
                }}
            }});

            // 初始載入（從第一台開始）
            updateInfo();
            loadSrc(list[0].url);
        }})();
        </script>
        """

        # 高度可視需求調整
        st.components.v1.html(html, height=600)

        # 顯示不可用頻道
        if unavailable:
            st.markdown("**不可用或需驗證的頻道**")
            for u in unavailable:
                st.write(f"- {u['name']}: {u.get('error')}")

        st.info("若某台需要登入驗證，請上傳 cookies.txt 並重新整理頁面以讓伺服器抓取帶 cookies 的 m3u8。")

# 小提示：若要強制重新抓取（例如上傳 cookies 後），使用者可按瀏覽器重新整理
