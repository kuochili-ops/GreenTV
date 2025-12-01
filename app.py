import streamlit as st
from yt_dlp import YoutubeDL
import requests
import uuid
from urllib.parse import urlparse
import tempfile
import os

st.set_page_config(page_title="簡易 YouTube Live TV（m3u8）", layout="centered")
st.title("簡易 YouTube Live TV（m3u8）")
st.write("按下 載入頻道 m3u8 會擷取每個頻道的最佳畫質 m3u8，然後可選台並複製/開啟/下載。")
st.warning("僅對您有權限或公開授權的內容使用。若影片需要登入驗證，請在私有環境上傳 cookies.txt 再重試。")

# 預設三個頻道（你提供的連結）
CHANNELS = {
    "三立新聞": "https://www.youtube.com/live/QsGswQvRmtU?si=ugpldIy-6K6KQS5u",
    "民視新聞": "https://www.youtube.com/live/ylYJSBUgaMA?si=yBqbwafsMknTq_gT",
    "鏡新聞": "https://www.youtube.com/live/5n0y6b0Q25o?si=ZufSUna9wrqjZuZx",
}

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
    """
    從 yt-dlp 回傳的 formats 中挑選最佳 m3u8/HLS。
    優先依 height（解析度）排序，若無 height 則依 tbr（總位元率）排序。
    回傳 dict 或 None。
    """
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

    # 優先依 height 排序，再依 tbr（或 bandwidth）排序
    def score(f):
        h = f.get("height") or 0
        tbr = f.get("tbr") or f.get("abr") or f.get("filesize") or 0
        return (int(h), float(tbr))

    candidates.sort(key=score, reverse=True)
    return candidates[0]

def fetch_m3u8_text(url: str, cookies=None, timeout=10):
    headers = {"User-Agent": "Mozilla/5.0 (compatible; yt-dlp/streamlit-app)"}
    resp = requests.get(url, headers=headers, cookies=cookies, timeout=timeout)
    resp.raise_for_status()
    return resp.text

# UI：cookies 上傳（選用）
uploaded_cookies = st.file_uploader("（選擇性）上傳 YouTube cookies.txt（Netscape 格式）以供需要時使用", type=["txt"])

# 按鈕：一次抓取所有頻道的 metadata
if st.button("載入頻道 m3u8"):
    # 若上傳 cookies，暫存成檔
    cookiefile_path = None
    if uploaded_cookies:
        tmp = tempfile.NamedTemporaryFile(delete=False)
        tmp.write(uploaded_cookies.getbuffer())
        tmp.flush()
        tmp.close()
        cookiefile_path = tmp.name
        st.info("已上傳 cookies（暫存），抓取階段會使用它（若需要）。")

    results = []
    for name, url in CHANNELS.items():
        item = {"name": name, "input_url": url, "error": None, "best": None}
        if not is_youtube_url(url):
            item["error"] = "非 YouTube 連結"
            results.append(item)
            continue
        try:
            info = fetch_info(url, cookiefile=cookiefile_path)
            formats = info.get("formats") or []
            best = choose_best_m3u8(formats)
            if best:
                item["best"] = {
                    "format_id": best.get("format_id"),
                    "ext": best.get("ext"),
                    "protocol": best.get("protocol"),
                    "url": best.get("url"),
                    "height": best.get("height"),
                    "tbr": best.get("tbr"),
                    "note": best.get("format_note"),
                }
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

    st.session_state["tv_results"] = results
    st.success("已完成載入，向下選台。")

# 若有結果，顯示電視台選單（單選）
if "tv_results" in st.session_state:
    results = st.session_state["tv_results"]
    # 建立選台選單（只列出有 best 的頻道）
    available = [r for r in results if r.get("best")]
    unavailable = [r for r in results if not r.get("best")]

    if not available:
        st.warning("目前沒有可用的 m3u8 頻道。請檢查是否需要 cookies 或該直播是否使用 HLS。")
    else:
        options = [f"{r['name']} ({r['best'].get('height') or 'N/A'}p)" for r in available]
        sel = st.radio("選擇頻道", options, index=0)
        chosen_idx = options.index(sel)
        chosen = available[chosen_idx]
        best = chosen["best"]

        st.markdown(f"**目前頻道**  {chosen['name']}")
        # 三個操作按鈕：複製、開啟、下載
        uid = uuid.uuid4().hex[:8]
        # 複製按鈕（HTML+JS）
        copy_html = f"""
        <button id="copy_{uid}">複製連結</button>
        <script>
        const btn = document.getElementById("copy_{uid}");
        btn.addEventListener('click', () => {{
            navigator.clipboard.writeText({best['url']!r}).then(()=> {{
                btn.innerText = "已複製";
                setTimeout(()=>{{ btn.innerText = "複製連結"; }}, 1500);
            }});
        }});
        </script>
        """
        st.components.v1.html(copy_html, height=40)

        # 開啟連結
        st.markdown(f"[進入連結]({best['url']})")

        # 下載按鈕：嘗試抓取 m3u8 內容以供下載（不顯示內容）
        try:
            m3u8_text = fetch_m3u8_text(best["url"])
            st.download_button("下載 m3u8（最佳畫質）", data=m3u8_text, file_name=f"{chosen['name']}_best.m3u8", mime="application/vnd.apple.mpegurl")
        except Exception as e:
            st.error(f"無法取得 m3u8 內容以供下載：{e}")
            st.info("請按 複製連結 並在本機或其他工具中開啟/下載。")

    # 顯示不可用頻道的錯誤訊息（若有）
    if unavailable:
        st.markdown("**不可用或需驗證的頻道**")
        for u in unavailable:
            st.write(f"- {u['name']}: {u.get('error')}")
