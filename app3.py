
import streamlit as st
from yt_dlp import YoutubeDL
import tempfile
import os
import uuid

# 頁面設定
st.set_page_config(page_title="YouTube m3u8 產生器", layout="wide")
st.title("YouTube 高畫質 m3u8 產生器 + 播放器")
st.write("輸入 YouTube 影片或播放清單網址，產生高畫質 m3u8 串流連結，並可直接播放。")

# 使用者輸入
urls_input = st.text_area("貼上 YouTube 影片或播放清單網址（每行一個）")
uploaded_cookies = st.file_uploader("（選擇性）上傳 cookies.txt（Netscape 格式）", type=["txt"])

# 抓取影片資訊
def fetch_info(url, cookiefile=None):
    opts = {
        "skip_download": True,
        "quiet": True,
        "extract_flat": False,
        "socket_timeout": 30,
    }
    if cookiefile:
        opts["cookiefile"] = cookiefile
    with YoutubeDL(opts) as ydl:
        return ydl.extract_info(url, download=False)

# 選擇最佳 m3u8 格式
def choose_best_m3u8(formats):
    candidates = []
    for f in formats:
        proto = (f.get("protocol") or "").lower()
        ext = (f.get("ext") or "").lower()
        note = (f.get("format_note") or "").lower()
        if "m3u8" in proto or ext == "m3u8" or "hls" in proto or "hls" in note:
            candidates.append(f)
    if not candidates:
        return None
    return sorted(candidates, key=lambda f: (f.get("height", 0), f.get("tbr", 0)), reverse=True)[0]

# 按鈕觸發
if st.button("開始解析"):
    urls = [u.strip() for u in urls_input.splitlines() if u.strip()]
    if not urls:
        st.warning("請輸入至少一個網址")
    else:
        cookiefile_path = None
        if uploaded_cookies:
            tmp = tempfile.NamedTemporaryFile(delete=False)
            tmp.write(uploaded_cookies.getbuffer())
            tmp.close()
            cookiefile_path = tmp.name
            st.info("已上傳 cookies，解析時會使用它。")

        results = []
        for u in urls:
            try:
                info = fetch_info(u, cookiefile=cookiefile_path)
                entries = info["entries"] if "entries" in info else [info]
                for e in entries:
                    best = choose_best_m3u8(e.get("formats", []))
                    results.append({
                        "title": e.get("title"),
                        "m3u8": best.get("url") if best else None
                    })
            except Exception as ex:
                results.append({"title": u, "m3u8": None, "error": str(ex)})

        if cookiefile_path and os.path.exists(cookiefile_path):
            os.remove(cookiefile_path)

        # 顯示結果
        playable = [r for r in results if r["m3u8"]]
        unavailable = [r for r in results if not r["m3u8"]]

        if playable:
            st.subheader("✅ 可播放影片清單")
            player_id = "player_" + uuid.uuid4().hex[:8]
            player_list = [{"name": r["title"], "url": r["m3u8"]} for r in playable]

            # HTML 播放器
            html = f"""
            <div style="display:flex;flex-direction:column;align-items:center;">
              <video id="{player_id}" controls autoplay playsinline style="width:100%;max-width:960px;height:auto;background:black;"></video>
              <div style="margin-top:16px;display:flex;align-items:center;justify-content:center;font-size:20px;font-weight:bold;">
                <div id="{player_id}_prev" style="cursor:pointer;color:#007bff;margin-right:40px;">{player_list[-1]['name']}</div>
                <div id="{player_id}_current" style="margin:0 40px;color:red;-webkit-text-stroke:1px white;text-shadow:0 0 2px white;font-weight:bold;">
                    {player_list[0]['name']}
                </div>
                <div id="{player_id}_next" style="cursor:pointer;color:#007bff;margin-left:40px;">{player_list[1]['name']}</div>
              </div>
            </div>

            <script src="https://cdn.jsdelivr.net/npm/hls.js@1.4.0/dist/hls.min.js"></script>
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

                prevName.addEventListener('click', () => gotoIndex(idx-1));
                nextName.addEventListener('click', () => gotoIndex(idx+1));

                document.addEventListener('keydown', e => {{
                    if(e.key==="ArrowLeft") gotoIndex(idx-1);
                    if(e.key==="ArrowRight") gotoIndex(idx+1);
                }});

                updateUI();
                loadSrc(list[0].url);
            }})();
            </script>
            """
            st.components.v1.html(html, height=700)

        if unavailable:
            st.subheader("❌ 無法取得 m3u8 的影片")
            for u in unavailable:
                st.write(f"- {u['title']} → {u.get('error', '找不到 HLS 格式')}")

        st.info("若影片需要登入驗證，請上傳 cookies.txt 並重新解析。")
