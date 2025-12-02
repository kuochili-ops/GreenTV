
import streamlit as st
from yt_dlp import YoutubeDL
import uuid
import tempfile
import os
from urllib.parse import urlparse

# 頁面設定
st.set_page_config(page_title="綠的電視", layout="wide")
st.title("自動播放，左右切換頻道")
st.write("頁面載入後自動從三立新聞開始播放；使用左右名稱點擊、鍵盤左右鍵或滑動切換頻道。若直播需要登入驗證，請上傳 cookies.txt（Netscape 格式）。")

# 頻道清單
CHANNELS = [
    {"name": "三立新聞", "url": "https://www.youtube.com/live/QsGswQvRmtU?si=0tG0FZcoxq5nftxS"},
    {"name": "民視新聞", "url": "https://www.youtube.com/live/ylYJSBUgaMA?si=yBqbwafsMknTq_gT"},
    {"name": "鏡新聞", "url": "https://www.youtube.com/live/5n0y6b0Q25o?si=ZufSUna9wrqjZuZx"},
    {"name": "非凡新聞", "url": "https://www.youtube.com/live/wAUx3pywTt8?si=9RB3z_JhUsQyGwb-"},
    {"name": "寰宇新聞", "url": "https://www.youtube.com/live/6IquAgfvYmc?si=FdqxZ7-48v64H7ZZ"},
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
    ydl_opts = {
        "skip_download": True,
        "quiet": True,
        "no_warnings": True,
        "socket_timeout": timeout,
    }
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

# cookies 上傳（選用）
uploaded_cookies = st.file_uploader("（選擇性）上傳 YouTube cookies.txt（Netscape 格式）以供抓取時使用", type=["txt"])

# 抓取頻道資訊
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
        item = {"name": name, "input_url": url, "error": None, "best_url": None}
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
            else:
                item["error"] = "找不到 m3u8/HLS 格式"
        except Exception as e:
            item["error"] = str(e)
        results.append(item)

    if cookiefile_path and os.path.exists(cookiefile_path):
        try:
            os.remove(cookiefile_path)
        except Exception:
            pass

    st.session_state["tv_channels"] = results

# 顯示播放器
channels = st.session_state.get("tv_channels", [])
playable = [c for c in channels if c.get("best_url")]
unavailable = [c for c in channels if not c.get("best_url")]

if not playable:
    st.warning("目前沒有可播放的頻道。請檢查是否需要 cookies 或該直播是否使用 HLS。")
    for u in unavailable:
        st.write(f"- {u['name']}: {u.get('error')}")
else:
    player_list = [{"name": c["name"], "url": c["best_url"]} for c in playable]
    player_id = "player_" + uuid.uuid4().hex[:8]

    html = f"""
    <div style="display:flex;flex-direction:column;align-items:center;">
      <video id="{player_id}" controls autoplay playsinline style="width:100%;max-width:960px;height:auto;background:black;"></video>

      <!-- 三欄顯示：左(上一頻道) 中(目前頻道) 右(下一頻道) -->
      <div style="margin-top:16px;display:flex;align-items:center;justify-content:center;font-size:24px;font-weight:bold;">
        <div id="{player_id}_prev" style="cursor:pointer;color:#007bff;margin-right:40px;">{player_list[-1]['name']}</div>
        <div id="{player_id}_current" style="margin:0 40px;color:red;-webkit-text-stroke:1px white;text-shadow:0 0 2px white;font-weight:bold;">
            {player_list[0]['name']}
        </div>
        <div id="{player_id}_next" style="cursor:pointer;color:#007bff;margin-left:40px;">{player_list[1]['name']}</div>
      </div>
    </div>

    https://cdn.jsdelivr.net/npm/hls.js@1.4.0/dist/hls.min.js</script>
    <script>
    (function(){{
        const list = {player_list!r};
        let idx = 0;
        const video = document.getElementById("{player_id}");
        const prevName = document.getElementById("{player_id}_prev");
        const nextName = document.getElementById("{player_id}_next");
        const currentName = document.getElementById("{player_id}_current");

        function updateUI(){{
            prevName.innerText = list[(idx-1+list.length)%list.length].name;
            currentName.innerText = list[idx].name;
            nextName.innerText = list[(idx+1)%list.length].name;
        }}

        function attachHls(url){{
            if(video.canPlayType('application/vnd.apple.mpegurl')){{
                video.src = url;
            }} else if(Hls.isSupported()){{
                if(window._hls_instance){{window._hls_instance.destroy();}}
                const hls = new Hls();
                window._hls_instance = hls;
                hls.loadSource(url);
                hls.attachMedia(video);
            }} else {{
                video.src = url;
            }}
        }}

        async function loadSrc(url){{
            video.muted = false;
            attachHls(url);
            try{{await video.play();}}catch(e){{}}
        }}

        function gotoIndex(newIdx){{
            idx = (newIdx+list.length)%list.length;
            updateUI();
            loadSrc(list[idx].url);
        }}

        prevName.addEventListener('click', ()=>gotoIndex(idx-1));
        nextName.addEventListener('click', ()=>gotoIndex(idx+1));

        document.addEventListener('keydown', e=>{{
            if(e.key==="ArrowLeft") gotoIndex(idx-1);
            if(e.key==="ArrowRight") gotoIndex(idx+1);
        }});

        let startX=null;
        video.addEventListener('touchstart', e=>{{startX=e.touches[0].clientX;}});
        video.addEventListener('touchend', e=>{{
            const endX=e.changedTouches[0].clientX;
            if(startX && Math.abs(endX-startX)>50){{
                if(endX<startX) gotoIndex(idx+1); else gotoIndex(idx-1);
            }}
            startX=null;
        }});

        updateUI();
        loadSrc(list[0].url);
    }})();
    </script>
    """

    st.components.v1.html(html, height=700)

    if unavailable:
        st.markdown("**不可用或需驗證的頻道**")
        for u in unavailable:
            st.write(f"- {u['name']}: {u.get('error')}")

    st.info("若某台需要登入驗證，請上傳 cookies.txt 並重新整理頁面以讓伺服器抓取帶 cookies 的 m3u8。")
