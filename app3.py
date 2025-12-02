# app.py
import streamlit as st
from yt_dlp import YoutubeDL
import tempfile
import os
import uuid
from urllib.parse import urlparse
import concurrent.futures
import time
import traceback

# -------------------------------
# 頁面設定（啟動時不做任何耗時工作）
# -------------------------------
st.set_page_config(page_title="YouTube m3u8 產生器（非阻塞啟動）", layout="wide")
st.title("🎬 YouTube m3u8 產生器（非阻塞啟動）")
st.write("此版本保證啟動時不會執行網路或 yt-dlp，請在輸入網址後按「開始解析」。")
st.info("若啟動仍卡住，請檢查 Python 環境是否能 import yt_dlp、requests 等套件。")

# -------------------------------
# 使用者輸入（UI 立即顯示）
# -------------------------------
urls_input = st.text_area("貼上 YouTube 影片或播放清單網址（每行一個）", height=140)
uploaded_cookies = st.file_uploader("（選擇性）上傳 cookies.txt（Netscape 格式）", type=["txt"])
max_workers = st.number_input("並行解析影片數（建議 1-4，預設 2）", min_value=1, max_value=8, value=2, step=1)
batch_size = st.number_input("分批處理大小（避免一次處理過多，預設 6）", min_value=1, max_value=32, value=6, step=1)
debug_mode = st.checkbox("顯示詳細錯誤（開發用）", value=False)

st.markdown("---")
st.write("按下「開始解析並產生清單」後才會開始網路抓取與解析。")

# -------------------------------
# 工具函式（不在 import 時執行網路）
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
        if debug_mode:
            return {"title": video_url, "url": None, "error": f"{str(e)}\n{traceback.format_exc()}"}
        return {"title": video_url, "url": None, "error": str(e)}

def export_m3u8_list(results):
    lines = [f"{r['title']} | {r['url']}" for r in results if r.get("url")]
    return "\n".join(lines)

# -------------------------------
# 主流程（僅在按鈕按下時執行）
# -------------------------------
if st.button("開始解析並產生清單"):
    urls = [u.strip() for u in urls_input.splitlines() if u.strip()]
    if not urls:
        st.warning("請輸入至少一個 YouTube 影片或播放清單網址。")
    else:
        # 暫存 cookies（若有）
        cookiefile_path = None
        if uploaded_cookies:
            tmp = tempfile.NamedTemporaryFile(delete=False)
            tmp.write(uploaded_cookies.getbuffer())
            tmp.flush()
            tmp.close()
            cookiefile_path = tmp.name
            st.info("已上傳 cookies（暫存），解析時會使用它。")

        # 展開輸入（playlist -> 條目）
        to_process = []
        with st.spinner("展開輸入並列出影片條目（若為 playlist，會先快速列出條目）..."):
            for u in urls:
                if "playlist" in u or "list=" in u:
                    try:
                        flat = fetch_playlist_entries_flat(u, cookiefile=cookiefile_path, quiet=not debug_mode)
                        if not flat:
                            st.warning(f"Playlist {u} 未列出任何條目或為私人/受限。")
                        for e in flat:
                            if e.get("url"):
                                to_process.append({"title": e.get("title"), "url": e.get("url")})
                    except Exception as e:
                        if debug_mode:
                            st.error(f"列出 playlist 失敗：{u}\n{traceback.format_exc()}")
                        else:
                            st.warning(f"列出 playlist 失敗：{u} → {e}")
                        to_process.append({"title": u, "url": u})
                else:
                    to_process.append({"title": u, "url": u})

        total_estimate = len(to_process)
        st.info(f"總共要解析 {total_estimate} 支影片（分批並行處理）")

        # 分批並行解析
        results = []
        if total_estimate == 0:
            st.warning("找不到任何影片條目。")
        else:
            overall_progress = st.progress(0)
            status = st.empty()
            done = 0
            for batch_start in range(0, total_estimate, int(batch_size)):
                batch = to_process[batch_start: batch_start + int(batch_size)]
                status.text(f"處理第 {batch_start + 1} 到 {batch_start + len(batch)} 支影片...")
                with concurrent.futures.ThreadPoolExecutor(max_workers=int(max_workers)) as ex:
                    future_to_item = {ex.submit(fetch_best_m3u8_for_video, item["url"], cookiefile_path, 25, not debug_mode): item for item in batch}
                    for fut in concurrent.futures.as_completed(future_to_item):
                        item = future_to_item[fut]
                        try:
                            res = fut.result()
                        except Exception as exc:
                            if debug_mode:
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

        # 清理 cookies 暫存檔
        if cookiefile_path and os.path.exists(cookiefile_path):
            try:
                os.remove(cookiefile_path)
            except Exception:
                pass

        # 顯示結果
        playable = [r for r in results if r.get("url")]
        unavailable = [r for r in results if not r.get("url")]

        if playable:
            st.subheader("✅ 可播放的 m3u8 清單")
            st.download_button("📥 下載 m3u8 清單（每行：title | url）", export_m3u8_list(playable), file_name="m3u8_list.txt", mime="text/plain")
            st.markdown("**點選下列任一項以在下方播放器播放**")
            cols = st.columns([4, 1])
            with cols[0]:
                for i, it in enumerate(playable):
                    key = f"play_item_{i}"
                    if st.button(f"{i+1}. {it['title']}", key=key):
                        st.session_state["selected_m3u8"] = {"index": i, "title": it["title"], "url": it["url"]}
            with cols[1]:
                st.write("共可播放：")
                st.write(len(playable))

            if "selected_m3u8" not in st.session_state and playable:
                st.session_state["selected_m3u8"] = {"index": 0, "title": playable[0]["title"], "url": playable[0]["url"]}

            sel = st.session_state.get("selected_m3u8")
            if sel:
                player_id = "player_" + uuid.uuid4().hex[:8]
                js_list = [{"name": p["title"], "url": p["url"]} for p in playable]
                html = f'''
                <div style="display:flex;flex-direction:column;align-items:center;">
                  <div id="{player_id}_title" style="font-weight:600;margin-bottom:8px;">正在播放：{sel['title']}</div>
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
                    let idx = {sel['index']};
                    const video = document.getElementById("{player_id}");
                    const title = document.getElementById("{player_id}_title");
                    const prevBtn = document.getElementById("{player_id}_prev");
                    const nextBtn = document.getElementById("{player_id}_next");
                    const overlay = document.getElementById("{player_id}_overlay");

                    function updateInfo() {{
                        const cur = list[idx];
                        title.innerText = "正在播放：" + cur.name;
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
                        video.muted = false;
                        attachHls(url);
                        try {{ await video.play(); overlay.style.display = "none"; }} catch (err) {{ overlay.style.display = "block"; }}
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
                        const tag = (document.activeElement && document.activeElement.tagName) || '';
                        if (tag === 'INPUT' || tag === 'TEXTAREA' || document.activeElement && document.activeElement.isContentEditable) return;
                        if (e.key === 'ArrowLeft') gotoIndex(idx-1);
                        if (e.key === 'ArrowRight') gotoIndex(idx+1);
                    }});

                    updateInfo();
                    loadSrc(list[idx].url);
                }})();
                </script>
                '''
                st.components.v1.html(html, height=640)

        if unavailable:
            st.subheader("❌ 無法取得 m3u8 的項目")
            for u in unavailable:
                st.write(f"- {u.get('title') or u.get('url')} → {u.get('error', '找不到 HLS 格式')}")
