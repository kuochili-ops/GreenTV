
import streamlit as st
from yt_dlp import YoutubeDL
import uuid

# 頁面設定
st.set_page_config(page_title="YouTube m3u8 播放器", layout="wide")
st.title("YouTube m3u8 播放器")
st.write("輸入一個或多個 YouTube 影片或播放清單連結，產生高畫質 m3u8 播放清單。")

# 使用者輸入
urls_input = st.text_area("請輸入 YouTube 影片或播放清單連結（每行一個）")
play_mode = st.selectbox("播放模式", [
    "播放一次後停止",
    "播放一次後播放下一段",
    "清單播放一次",
    "循環播放",
    "隨機播放"
])

start_button = st.button("產生播放清單")

# yt-dlp 解析函式
def fetch_info(url: str, extract_flat=False):
    ydl_opts = {
        "quiet": True,
        "skip_download": True,
        "extract_flat": extract_flat,
        "no_warnings": True,
        "socket_timeout": 15,
    }
    with YoutubeDL(ydl_opts) as ydl:
        return ydl.extract_info(url, download=False)

def choose_best_m3u8(formats):
    candidates = []
    for f in formats:
        proto = (f.get("protocol") or "").lower()
        ext = (f.get("ext") or "").lower()
        note = (f.get("format_note") or "").lower()
        url_f = f.get("url")
        if url_f and ("m3u8" in proto or ext == "m3u8" or "hls" in proto or "hls" in note):
            candidates.append(f)
    if not candidates:
        return None
    candidates.sort(key=lambda f: (f.get("height") or 0, f.get("tbr") or 0), reverse=True)
    return candidates[0]

playlist = []
failed_list = []

if start_button and urls_input.strip():
    urls = [u.strip() for u in urls_input.splitlines() if u.strip()]
    st.info("正在解析 YouTube 連結，請稍候...")
    progress = st.progress(0)
    total = len(urls)
    count = 0

    for url in urls:
        count += 1
        progress.progress(count / total)
        st.write(f"正在解析：{url}")
        try:
            extract_flat = "list=RD" in url
            info = fetch_info(url, extract_flat=extract_flat)

            if "entries" in info:  # Playlist 或 Radio
                for entry in info["entries"][:30]:
                    try:
                        entry_url = entry.get("url") or f"https://www.youtube.com/watch?v={entry.get('id')}"
                        sub_info = fetch_info(entry_url)
                        best = choose_best_m3u8(sub_info.get("formats", []))
                        if best:
                            playlist.append({"title": sub_info.get("title"), "url": best["url"]})
                        else:
                            failed_list.append({"title": sub_info.get("title"), "reason": "找不到 m3u8"})
                    except Exception as e:
                        failed_list.append({"title": entry.get("title", "未知影片"), "reason": str(e)})
            else:  # 單一影片
                best = choose_best_m3u8(info.get("formats", []))
                if best:
                    playlist.append({"title": info.get("title"), "url": best["url"]})
                else:
                    failed_list.append({"title": info.get("title"), "reason": "找不到 m3u8"})
        except Exception as e:
            failed_list.append({"title": url, "reason": str(e)})

# 顯示結果
if playlist:
    st.success(f"成功解析 {len(playlist)} 個影片")
    if failed_list:
        st.warning(f"以下 {len(failed_list)} 個影片無法播放（已跳過）：")
        st.table(failed_list)

    player_id = "player_" + uuid.uuid4().hex[:8]

    # ✅ 修正 HTML，不再使用 &lt; &gt;
    html = f"""
<div style="display:flex;flex-direction:column;align-items:center;">
  <video id="{player_id}" controls autoplay playsinline style="width:100%;max-width:960px;height:auto;background:black;"></video>
  <div style="margin-top:16px;">
    <ul id="{player_id}_list" style="list-style:none;padding:0;font-size:18px;">
"""
    for i, item in enumerate(playlist):
        html += f'<li id="item_{i}" style="margin:8px 0;cursor:pointer;color:#007bff;" onclick="gotoIndex({i})">{item["title"]}</li>'
    html += "</ul></div></div>"

    html += f"""
<script src="https://cdn.jsdelivr.net/npm/hls.js@1.4.0/dist/hls.min.js"></script>
<script>
(function(){
    const list = {str(playlist).replace("'", '"')};
    let idx = 0;
    const video = document.getElementById('{player_id}');

    function highlightCurrent(){
        for(let i=0;i<list.length;i++){
            const li = document.getElementById('item_'+i);
            if(li){
                if(i===idx){
                    li.style.color='red';
                    li.style.fontWeight='bold';
                } else {
                    li.style.color='#007bff';
                    li.style.fontWeight='normal';
                }
            }
        }
    }

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

    async function loadSrc(url){
        video.muted = false;
        attachHls(url);
        try{await video.play();}catch(e){}
    }

    function gotoIndex(newIdx){
        idx = newIdx;
        highlightCurrent();
        loadSrc(list[idx].url);
    }

    function nextVideo(){
        const mode = '{play_mode}';
        if(mode === '播放一次後停止'){
            return;
        } else if(mode === '播放一次後播放下一段'){
            if(idx < list.length - 1){gotoIndex(idx+1);}
        } else if(mode === '清單播放一次'){
            if(idx < list.length - 1){gotoIndex(idx+1);}
        } else if(mode === '循環播放'){
            gotoIndex((idx+1)%list.length);
        } else if(mode === '隨機播放'){
            gotoIndex(Math.floor(Math.random()*list.length));
        }
    }

    video.addEventListener('ended', nextVideo);

    video.addEventListener('dblclick', async ()=>{
        try{
            if(!document.fullscreenElement){
                await video.requestFullscreen();
            } else {
                await document.exitFullscreen();
            }
        }catch(e){}
    });

    highlightCurrent();
    loadSrc(list[0].url);
})();
</script>
"""

    st.components.v1.html(html, height=800)

elif start_button and urls_input.strip():
    st.warning("所有影片都失效或無法解析，請檢查連結或換另一個播放清單。")
    if failed_list:
        st.table(failed_list)
