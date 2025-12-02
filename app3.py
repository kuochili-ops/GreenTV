
import streamlit as st
from yt_dlp import YoutubeDL
import uuid
import random

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

# 解析 YouTube 連結
def fetch_info(url: str):
    ydl_opts = {
        "quiet": True,
        "skip_download": True,
        "extract_flat": False,
        "no_warnings": True,
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
    for url in urls:
        try:
            info = fetch_info(url)
            if "entries" in info:  # Playlist
                for entry in info["entries"]:
                    try:
                        sub_info = fetch_info(entry["url"])
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
        st.warning(f"跳過 {len(failed_list)} 個影片")
        for f in failed_list:
            st.write(f"- {f['title']}（原因：{f['reason']}）")

    # 播放器 HTML + JS 省略（保持原樣）
    st.components.v1.html(html, height=800)

elif start_button:
    st.error("沒有成功解析的影片，請檢查連結是否有效或影片是否可播放。")
    if failed_list:
        st.warning("以下影片解析失敗：")
        for f in failed_list:
            st.write(f"- {f['title']}（原因：{f['reason']}）")
