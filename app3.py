
import streamlit as st
from yt_dlp import YoutubeDL
import uuid
import json

st.set_page_config(page_title="YouTube m3u8 播放器", layout="wide")
st.title("YouTube m3u8 播放器")

urls_input = st.text_area("請輸入 YouTube 影片或播放清單連結（每行一個）")
start_button = st.button("產生播放清單")

def fetch_info(url):
    ydl_opts = {"quiet": True, "skip_download": True, "no_warnings": True}
    with YoutubeDL(ydl_opts) as ydl:
        return ydl.extract_info(url, download=False)

def choose_best_m3u8(formats):
    candidates = [f for f in formats if "m3u8" in (f.get("protocol") or "") or "hls" in (f.get("protocol") or "")]
    if not candidates:
        return None
    return sorted(candidates, key=lambda f: (f.get("height") or 0), reverse=True)[0]

playlist = []

if start_button and urls_input.strip():
    urls = [u.strip() for u in urls_input.splitlines() if u.strip()]
    st.info("正在解析，請稍候...")
    for url in urls:
        try:
            info = fetch_info(url)
            best = choose_best_m3u8(info.get("formats", []))
            if best:
                playlist.append({"title": info.get("title"), "url": best["url"]})
        except Exception as e:
            st.warning(f"解析失敗：{url}，原因：{e}")

if playlist:
    st.success(f"成功解析 {len(playlist)} 個影片")
    player_id = "player_" + uuid.uuid4().hex[:8]

    html = f"""
<div style="text-align:center;">
  <video id="{player_id}" controls autoplay style="width:100%;max-width:960px;background:black;"></video>
  <ul style="list-style:none;padding:0;">
"""
    for i, item in enumerate(playlist):
        html += f'<li style="cursor:pointer;color:#007bff;" onclick="gotoIndex({i})">{item["title"]}</li>'
    html += "</ul></div>"

    html += f"""
<script src="https://cdn.jsdelivr.net/npm/hls.js@1.4.0/dist/hls.min.js"></script>
<script>
const list = {json.dumps(playlist)};
let idx = 0;
const video = document.getElementById('{player_id}');

function attachHls(url){
    if(video.canPlayType('application/vnd.apple.mpegurl')){
        video.src = url;
    } else if(Hls.isSupported()){
        if(window._hls_instance){window._hls_instance.destroy();}
        const hls = new Hls();
        window._hls_instance = hls;
        hls.loadSource(url);
        hls.attachMedia(video);
    } else {
        video.src = url;
    }
}

function gotoIndex(newIdx){
    idx = newIdx;
    attachHls(list[idx].url);
}

attachHls(list[0].url);
</script>
"""
