# app.py
import streamlit as st
from yt_dlp import YoutubeDL
import tempfile
import os
import uuid
import concurrent.futures
import time
import traceback

# -------------------------------
# 頁面設定
# -------------------------------
st.set_page_config(page_title="YouTube 點唱機（滑動清單 + 上方操作）", layout="wide")
st.markdown("<h1 style='margin-bottom:6px;'>🎵 YouTube 點唱機</h1>", unsafe_allow_html=True)
st.write("下方為可滑動候選清單（每項只有「選擇」按鈕），上方為操作面板（播放 / 加入佇列 / 移除）。")

# -------------------------------
# CSS（滑動清單與樣式）
# -------------------------------
st.markdown(
    """
    <style>
    .container { display:flex; gap:18px; align-items:flex-start; }
    .left { width:36%; background:#0f1724; color:#e6eef8; padding:14px; border-radius:10px; }
    .right { flex:1; background:linear-gradient(180deg,#071021,#0b1b2b); color:#fff; padding:18px; border-radius:10px; }
    .scroll-area { max-height:520px; overflow:auto; padding-right:6px; }
    .song-item { padding:10px; border-radius:6px; margin-bottom:8px; background:rgba(255,255,255,0.02); display:flex; align-items:center; justify-content:space-between; }
    .song-meta { flex:1; padding-right:12px; color:#e6eef8; }
    .queue-item { padding:6px 8px; border-radius:6px; background:rgba(255,255,255,0.02); margin-bottom:6px; color:#e6eef8; }
    .op-panel { display:flex; gap:8px; margin-bottom:10px; align-items:center; }
    .op-btn { padding:8px 12px; border-radius:6px; background:#1f6feb; color:white; border:none; cursor:pointer; }
    .op-btn:disabled { background:#555; cursor:not-allowed; }
    </style>
    """,
    unsafe_allow_html=True,
)

# -------------------------------
# 輸入區（上方）
# -------------------------------
with st.expander("輸入 YouTube 影片或播放清單網址（每行一個）", expanded=False):
    urls_input = st.text_area("網址（每行一個）", height=120)
    uploaded_cookies = st.file_uploader("（選擇性）上傳 cookies.txt（Netscape 格式）", type=["txt"])
    max_workers = st.number_input("並行解析影片數（建議 1-4）", min_value=1, max_value=8, value=2, step=1)
    batch_size = st.number_input("分批處理大小（預設 6）", min_value=1, max_value=32, value=6, step=1)
    debug_mode = st.checkbox("顯示詳細錯誤（開發用）", value=False)
    parse_btn = st.button("開始解析並產生清單")

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
# 解析按鈕觸發（將結果存入 session_state）
# -------------------------------
if parse_btn:
    urls = [u.strip() for u in urls_input.splitlines() if u.strip()]
    if not urls:
        st.warning("請輸入至少一個網址")
    else:
        cookiefile_path = None
        if uploaded_cookies:
            tmp = tempfile.NamedTemporaryFile(delete=False)
            tmp.write(uploaded_cookies.getbuffer())
            tmp.flush()
            tmp.close()
            cookiefile_path = tmp.name
            st.info("已上傳 cookies（暫存），解析時會使用它。")

        to_process = []
        with st.spinner("展開並列出影片條目..."):
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

        results = []
        if total_estimate > 0:
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

        if cookiefile_path and os.path.exists(cookiefile_path):
            try:
                os.remove(cookiefile_path)
            except Exception:
                pass

        playable = [r for r in results if r.get("url")]
        unavailable = [r for r in results if not r.get("url")]

        st.session_state["playable"] = playable
        st.session_state["unavailable"] = unavailable
        if "queue" not in st.session_state:
            st.session_state["queue"] = []
        # selected_index 用來記錄目前在滑動清單中被選擇的項目
        if "selected_index" not in st.session_state:
            st.session_state["selected_index"] = 0 if playable else None
        st.success(f"解析完成：可播放 {len(playable)} 項，無法取得 {len(unavailable)} 項")

# -------------------------------
# 介面：左側為滑動清單（下），上方為操作面板（對選中項目）
# -------------------------------
playable = st.session_state.get("playable", [])
unavailable = st.session_state.get("unavailable", [])
queue = st.session_state.get("queue", [])
selected_index = st.session_state.get("selected_index", None)

col_left, col_right = st.columns([3,7])

with col_left:
    st.markdown("<div class='left'>", unsafe_allow_html=True)
    st.markdown("### 候選清單（滑動視窗）")

    # 操作面板（上方） - 對 selected_index 生效
    st.markdown("<div class='op-panel'>", unsafe_allow_html=True)
    # 顯示目前選中項目標題（簡短）
    if selected_index is None or not playable:
        st.markdown("<div style='color:#cfe8ff;'>尚未選擇項目</div>", unsafe_allow_html=True)
    else:
        cur = playable[selected_index]
        st.markdown(f"<div style='color:#cfe8ff;flex:1;'>選擇：{selected_index+1}. {cur.get('title')[:80]}</div>", unsafe_allow_html=True)

    # 三個操作按鈕（播放 / 加入佇列 / 移除）
    op_cols = st.columns([1,1,1])
    with op_cols[0]:
        if st.button("▶ 播放", key="op_play"):
            if selected_index is not None and playable:
                st.session_state["selected_m3u8"] = {"index": selected_index, "title": playable[selected_index]["title"], "url": playable[selected_index]["url"]}
    with op_cols[1]:
        if st.button("＋ 加入佇列", key="op_queue"):
            if selected_index is not None and playable:
                item = playable[selected_index]
                if item not in queue:
                    queue.append(item)
                    st.session_state["queue"] = queue
    with op_cols[2]:
        if st.button("🗑 移除", key="op_remove"):
            if selected_index is not None and playable:
                item = playable[selected_index]
                new_playable = [x for x in playable if x != item]
                st.session_state["playable"] = new_playable
                # 調整選中索引
                if new_playable:
                    new_idx = min(selected_index, len(new_playable)-1)
                    st.session_state["selected_index"] = new_idx
                else:
                    st.session_state["selected_index"] = None
                # 若被選為播放中，取消或重設
                if "selected_m3u8" in st.session_state and st.session_state["selected_m3u8"].get("url") == item.get("url"):
                    st.session_state.pop("selected_m3u8", None)
    st.markdown("</div>", unsafe_allow_html=True)

    st.markdown('<div class="scroll-area">', unsafe_allow_html=True)
    # 顯示滑動清單：每項只有一個「選擇」按鈕
    if not playable:
        st.info("候選清單為空，請先解析網址。")
    else:
        for i, p in enumerate(playable):
            st.markdown("<div class='song-item'>", unsafe_allow_html=True)
            st.markdown(f"<div class='song-meta'>{i+1}. {p.get('title')[:140]}</div>", unsafe_allow_html=True)
            # 單一選擇按鈕（避免每項列三個按鈕）
            if st.button("選擇", key=f"select_{i}"):
                st.session_state["selected_index"] = i
            st.markdown("</div>", unsafe_allow_html=True)
    st.markdown('</div>', unsafe_allow_html=True)

    st.markdown("---")
    st.markdown("### ▶️ 播放佇列")
    if not queue:
        st.write("佇列為空，使用上方「＋ 加入佇列」把歌曲放進來。")
    else:
        for qi, q in enumerate(queue):
            st.markdown(f"<div class='queue-item'>{qi+1}. {q.get('title')[:100]}</div>", unsafe_allow_html=True)
        qcols = st.columns([1,1,1])
        with qcols[0]:
            if st.button("清空佇列", key="queue_clear"):
                st.session_state["queue"] = []
        with qcols[1]:
            if st.button("播放佇列第一首", key="queue_play_first"):
                if queue:
                    first = queue.pop(0)
                    # 若 first 在 playable 中，設定為該索引
                    if first in playable:
                        st.session_state["selected_index"] = playable.index(first)
                    st.session_state["selected_m3u8"] = {"index": st.session_state.get("selected_index", 0), "title": first["title"], "url": first["url"]}
                    st.session_state["queue"] = queue
        with qcols[2]:
            if st.button("加入全部到佇列", key="queue_add_all"):
                for p in playable:
                    if p not in queue:
                        queue.append(p)
                st.session_state["queue"] = queue

    st.markdown("</div>", unsafe_allow_html=True)

with col_right:
    st.markdown("<div class='right'>", unsafe_allow_html=True)
    # 播放顯示區
    selected_play = st.session_state.get("selected_m3u8")
    if not selected_play:
        st.markdown("<h3 style='color:#cfe8ff;'>尚未播放</h3>", unsafe_allow_html=True)
        st.write("請在左側滑動清單選擇一首，然後按上方的「▶ 播放」。")
    else:
        sel_index = selected_play.get("index", 0)
        # 安全檢查
        if sel_index < 0 or sel_index >= len(playable):
            sel_index = 0
            if playable:
                st.session_state["selected_m3u8"] = {"index": 0, "title": playable[0]["title"], "url": playable[0]["url"]}
        if playable:
            sel_item = playable[sel_index]
            st.markdown(f"<h2 style='margin-bottom:6px;color:#fff;'>{sel_item.get('title')}</h2>", unsafe_allow_html=True)
            st.image("https://placehold.co/640x360/0b1b2b/ffffff?text=YouTube+Cover", caption="", use_column_width=False, width=640, clamp=True)

            control_cols = st.columns([1,1,1,2,2])
            with control_cols[0]:
                if st.button("◀ 上一首", key="ui_prev"):
                    new_idx = (sel_index - 1) % len(playable) if playable else 0
                    st.session_state["selected_m3u8"] = {"index": new_idx, "title": playable[new_idx]["title"], "url": playable[new_idx]["url"]}
            with control_cols[1]:
                if st.button("▶ 播放（重載）", key="ui_play"):
                    st.session_state["selected_m3u8"] = {"index": sel_index, "title": sel_item["title"], "url": sel_item["url"]}
            with control_cols[2]:
                if st.button("下一首 ▶", key="ui_next"):
                    new_idx = (sel_index + 1) % len(playable) if playable else 0
                    st.session_state["selected_m3u8"] = {"index": new_idx, "title": playable[new_idx]["title"], "url": playable[new_idx]["url"]}
            with control_cols[3]:
                loop_mode = st.checkbox("循環播放", value=st.session_state.get("loop", False), key="ui_loop")
                st.session_state["loop"] = loop_mode
            with control_cols[4]:
                shuffle_mode = st.checkbox("隨機播放", value=st.session_state.get("shuffle", False), key="ui_shuffle")
                st.session_state["shuffle"] = shuffle_mode

            vol = st.slider("音量", min_value=0, max_value=100, value=80, step=1, key="volume_slider")

            dl_cols = st.columns([1,1,1])
            with dl_cols[0]:
                st.download_button("下載 m3u8 清單", export_m3u8_list(playable), file_name="m3u8_list.txt")
            with dl_cols[1]:
                if st.button("從佇列播放下一首", key="ui_queue_next"):
                    if queue:
                        nxt = queue.pop(0)
                        if nxt in playable:
                            st.session_state["selected_index"] = playable.index(nxt)
                        st.session_state["selected_m3u8"] = {"index": st.session_state.get("selected_index", 0), "title": nxt["title"], "url": nxt["url"]}
                        st.session_state["queue"] = queue
            with dl_cols[2]:
                if st.button("移除目前歌曲（播放中）", key="ui_remove_current"):
                    if playable:
                        new_playable = [x for x in playable if x != sel_item]
                        st.session_state["playable"] = new_playable
                        st.session_state.pop("selected_m3u8", None)
                        if new_playable:
                            st.session_state["selected_m3u8"] = {"index": 0, "title": new_playable[0]["title"], "url": new_playable[0]["url"]}

            # 前端播放器（HLS）
            player_id = "player_" + uuid.uuid4().hex[:8]
            js_list = [{"name": p["title"], "url": p["url"]} for p in playable]

            html = f'''
            <div style="margin-top:12px;">
              <video id="{player_id}" controls playsinline style="width:100%;max-width:960px;height:auto;background:black;"></video>
            </div>

            <script src="https://cdn.jsdelivr.net/npm/hls.js@1.4.0/dist/hls.min.js"></script>
            <script>
            (function(){{
                const list = {js_list!r};
                let idx = {sel_index};
                const video = document.getElementById("{player_id}");
                const volume = {st.session_state.get("volume_slider", 80)} / 100.0;
                video.volume = volume;

                function attachHls(url) {{
                    if (!url) return;
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

                function loadAndPlay(i) {{
                    if (!list || list.length === 0) return;
                    idx = i % list.length;
                    attachHls(list[idx].url);
                    setTimeout(()=>{{ try{{ video.play(); }}catch(e){{}} }}, 300);
                }}

                loadAndPlay(idx);

                video.addEventListener('ended', function() {{
                    const loop = {str(st.session_state.get("loop", False)).lower()};
                    const shuffle = {str(st.session_state.get("shuffle", False)).lower()};
                    if (shuffle) {{
                        idx = Math.floor(Math.random() * list.length);
                    }} else {{
                        idx = (idx + 1) % list.length;
                    }}
                    if (!loop && idx === 0 && !shuffle) {{
                        return;
                    }}
                    loadAndPlay(idx);
                }});
            }})();
            </script>
            '''
            st.components.v1.html(html, height=420)

    st.markdown("</div>", unsafe_allow_html=True)

# -------------------------------
# 顯示無法取得的項目（底部）
# -------------------------------
if unavailable:
    st.markdown("---")
    st.subheader("❌ 無法取得 m3u8 的項目")
    for u in unavailable:
        st.write(f"- {u.get('title') or u.get('url')} → {u.get('error', '找不到 HLS 格式')}")
