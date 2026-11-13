import html as htmllib
import json
import os
import re
import sqlite3
import urllib.request
from datetime import datetime
from urllib.parse import quote

import pandas as pd
import streamlit as st
import streamlit.components.v1 as components

# --- 1. 自动从 GitHub Release 缓存下载 DB 文件 ---
DB_PATH = "weblog_corpus.db"
# 注意：如果部署到 Streamlit Cloud，记得把新版 weblog_corpus.db 上传到 Release 并更新此处版本号
RELEASE_URL = "https://github.com/JasonZeng24/Recovering-and-organizing-Brad-DeLong-s-weblog-archive-pre-2005-/releases/download/V2.1/weblog_corpus.db"

st.set_page_config(
    page_title="Brad DeLong's Weblog Archive (1995–2005)",
    page_icon="🏛️",
    layout="wide",
    initial_sidebar_state="expanded",
)


@st.cache_resource(show_spinner=False)
def ensure_database():
    if not os.path.exists(DB_PATH):
        with st.spinner(
            "Downloading Brad DeLong's Corpus Database (~100MB+)... Please wait a"
            " few seconds."
        ):
            urllib.request.urlretrieve(RELEASE_URL, DB_PATH)


ensure_database()


def get_db_connection():
    return sqlite3.connect(DB_PATH)


@st.cache_data(show_spinner=False)
def get_posts_columns() -> set[str]:
    """检测当前 weblog_corpus.db 是否包含合并后的新字段，确保新旧库 100% 兼容。"""
    conn = get_db_connection()
    try:
        return {row[1] for row in conn.execute("PRAGMA table_info(posts)").fetchall()}
    finally:
        conn.close()


POSTS_COLS = get_posts_columns()
HAS_MERGED_SCHEMA = {"is_canonical_post", "is_unique_corpus", "wayback_url", "exact_post_datetime"}.issubset(POSTS_COLS)


@st.cache_data(ttl=3600, show_spinner=False)
def get_recovered_assets(post_id: int):
    """Return a list of (orig_path, wayback_url) with hit status for the given post."""
    conn = get_db_connection()
    try:
        has = conn.execute(
            "SELECT name FROM sqlite_master WHERE type='table' AND name='missing_assets'"
        ).fetchone()
        if not has:
            return []
        rows = conn.execute(
            "SELECT orig_path, wayback_url FROM missing_assets "
            "WHERE post_id = ? AND status = 'hit' AND wayback_url IS NOT NULL",
            (post_id,),
        ).fetchall()
        return rows
    finally:
        conn.close()


# ---------------------------------------------------------------------------
# Wayback 相关（用于未命中 CDX 精确快照时的兜底推算）
# ---------------------------------------------------------------------------
SITE_HOSTS = ("www.j-bradford-delong.net", "j-bradford-delong.net")

VERIFIED_WAYBACK_SNAPSHOTS = {
    "2000": "https://web.archive.org/web/20000815052826/http://www.j-bradford-delong.net/",
    "2001": "https://web.archive.org/web/20010720233229/http://www.j-bradford-delong.net/",
    "2002": "https://web.archive.org/web/20020722040030/http://j-bradford-delong.net/",
    "2003": "https://web.archive.org/web/20030719133111/http://j-bradford-delong.net/",
    "2004": "https://web.archive.org/web/20040710015200/http://www.j-bradford-delong.net/",
    "2005": "https://web.archive.org/web/20050701001614/http://www.j-bradford-delong.net/",
}


def _clean_component(c: str) -> str:
    """去掉本地备份目录的后缀：'archives--July 1 2003'、'2004_archives_copy'、'movable_type copy' 等。"""
    c = re.sub(r"--.*$", "", c)
    c = re.sub(r"[ _]copy( \d+)?$", "", c, flags=re.IGNORECASE)
    return c.strip()


def get_wayback_info(rel_path: str, post_date: str, body_text: str):
    """返回 (年份首页快照, 年份, 时间戳, 候选原站URL列表, 年份日历页)"""
    rel = str(rel_path or "").replace("\\", "/").strip()
    pdate = str(post_date or "").strip()

    dt = None
    for fmt in ("%Y-%m-%d %H:%M:%S", "%Y-%m-%d", "%B %d, %Y"):
        try:
            dt = datetime.strptime(pdate, fmt)
            break
        except Exception:
            continue

    if dt:
        yr = str(dt.year)
        ts = dt.strftime("%Y%m%d")
    else:
        m_yr = re.search(r"(200[0-5])", f"{rel} {pdate} {body_text[:400]}")
        yr = m_yr.group(1) if m_yr else "2003"
        ts = f"{yr}0701"

    parts = [_clean_component(c) for c in rel.split("/") if c.strip()]
    paths = []
    if parts:
        fname, folders = parts[-1], parts[:-1]
        m = re.fullmatch(r"(\d{4,7})\.html?", fname, re.IGNORECASE)
        if m and folders and "archives" in folders[-1].lower():
            folder = folders[-1]
            names = [folder]
            m2 = re.match(r"^(\d{4})-\d+_archives$", folder)
            if m2:
                names.append(f"{m2.group(1)}_archives")
            for n in names:
                paths.append(f"movable_type/{quote(n)}/{m.group(1)}.html")
        else:
            in_archives = any("archives" in f.lower() for f in folders)
            if in_archives and folders and folders[0].lower() != "movable_type":
                prefixed = ["movable_type"] + parts
                paths.append("/".join(quote(c) for c in prefixed))
                paths.append(f"movable_type/{quote(fname)}")
                m2 = re.match(r"^(\d{4})-\d+_archives$", folders[-1])
                if m2:
                    alt = ["movable_type"] + parts[:-2] + [f"{m2.group(1)}_archives", fname]
                    paths.append("/".join(quote(c) for c in alt))
            else:
                paths.append("/".join(quote(c) for c in parts))

    urls = []
    for p in paths:
        for host in SITE_HOSTS:
            u = f"http://{host}/{p}"
            if u not in urls:
                urls.append(u)

    home = VERIFIED_WAYBACK_SNAPSHOTS.get(yr, VERIFIED_WAYBACK_SNAPSHOTS["2003"])
    calendar = f"https://web.archive.org/web/{yr}*/http://www.j-bradford-delong.net/*"
    return home, yr, ts, urls, calendar


@st.cache_data(ttl=86400, show_spinner=False)
def find_closest_snapshot(urls: tuple, ts: str):
    """依次查询 Wayback 官方接口，返回 (命中链接或None, 调试信息列表)。"""
    debug = []
    for u in urls:
        api = (
            "https://archive.org/wayback/available?url="
            f"{quote(u, safe='')}&timestamp={ts}"
        )
        try:
            with urllib.request.urlopen(api, timeout=10) as r:
                data = json.load(r)
            snap = data.get("archived_snapshots", {}).get("closest")
            if snap and snap.get("available"):
                return snap["url"].replace("http://", "https://", 1), debug
            debug.append(f"{u} -> API OK, but no snapshot")
        except Exception as e:
            debug.append(f"{u} -> API failed: {type(e).__name__}: {e}")
    return None, debug


# ---------------------------------------------------------------------------
# HTML 阅读器
# ---------------------------------------------------------------------------
MONTHS = (
    "January|February|March|April|May|June|July|August|September|October|November|December"
)
DATE_RE = re.compile(rf"((?:{MONTHS})\s+\d{{1,2}},\s+\d{{4}})")
FOOTER_RE = re.compile(
    r"(Posted by DeLong at\s+[0-9:]+\s*[AP]M\s*\|\s*Permanent Link"
    r"(?:\s*\|\s*Comments\s*\(\d+\))?(?:\s*\|\s*Trackback\s*\(\d+\))?)",
    re.IGNORECASE,
)
SENT_RE = re.compile(r'(?:(?<=[.!?])|(?<=[.!?]["”]))\s+(?=[A-Z"“])')

READER_CSS = """
<style>
  body { margin:0; padding:24px 32px; background:#fff; color:#1f2937;
         font-family: Georgia, 'Times New Roman', serif; font-size:17px; line-height:1.75; }
  .wrap { max-width: 760px; margin: 0 auto; }
  h3.date { font-family: -apple-system, 'Segoe UI', sans-serif; font-size:13px; letter-spacing:.08em;
            text-transform:uppercase; color:#003262; border-bottom:2px solid #FDB515;
            padding-bottom:6px; margin:36px 0 16px; }
  h3.date:first-child { margin-top:0; }
  p { margin: 0 0 1.1em; text-align: justify; hyphens: auto; }
  .sig { font-family: -apple-system, 'Segoe UI', sans-serif; font-size:12px; color:#6b7280;
         border-left:3px solid #d1d5db; padding-left:10px; margin:18px 0 8px; }
  mark { background:#fef08a; color:#1e293b; font-weight:600; padding:0 2px; border-radius:2px; }
  @media (prefers-color-scheme: dark) {
    body { background:#0e1117; color:#e5e7eb; }
    h3.date { color:#FDB515; border-color:#374151; }
    .sig { color:#9ca3af; border-color:#4b5563; }
  }
</style>
"""


def _highlight(escaped_text: str, query: str) -> str:
    if len(query) < 2:
        return escaped_text
    q = re.escape(htmllib.escape(query))
    pattern = re.compile(rf"(&#?\w+;)|({q})", re.IGNORECASE)

    def repl(m):
        if m.group(1):
            return m.group(1)
        return f"<mark>{m.group(2)}</mark>"

    return pattern.sub(repl, escaped_text)


def _paragraphs(text: str, query: str, sents_per_para: int = 3) -> str:
    out = []
    for block in re.split(r"\n\s*\n", text):
        block = block.strip()
        if not block:
            continue
        sents = SENT_RE.split(block)
        for i in range(0, len(sents), sents_per_para):
            chunk = " ".join(sents[i : i + sents_per_para]).strip()
            if not chunk:
                continue
            chunk = _highlight(htmllib.escape(chunk), query)
            out.append(f"<p>{chunk}</p>")
    return "\n".join(out)


def format_post_as_html(raw_text: str, search_query: str = "") -> str:
    if not raw_text:
        return ""

    q = search_query.strip().replace('"', "")
    text = re.sub(
        r"^.*?Website Policies\s*",
        "",
        str(raw_text).strip(),
        count=1,
        flags=re.IGNORECASE | re.DOTALL,
    )

    parts = DATE_RE.split(text)
    html_parts = []
    if parts[0].strip():
        html_parts.append(_paragraphs(parts[0], q))

    for i in range(1, len(parts), 2):
        date = parts[i]
        body = parts[i + 1] if i + 1 < len(parts) else ""
        html_parts.append(f'<h3 class="date">📅 {htmllib.escape(date)}</h3>')

        segs = FOOTER_RE.split(body)
        for j, seg in enumerate(segs):
            if j % 2 == 0:
                html_parts.append(_paragraphs(seg, q))
            else:
                html_parts.append(f'<div class="sig">✍️ {htmllib.escape(seg)}</div>')

    return (
        "<!doctype html><html><head><meta charset='utf-8'>"
        f"{READER_CSS}</head><body><div class='wrap'>{''.join(html_parts)}</div></body></html>"
    )


# ---------------------------------------------------------------------------
# 时间轴统计
# ---------------------------------------------------------------------------
def _entry_key(rel_path):
    parts = [
        _clean_component(c)
        for c in str(rel_path or "").replace("\\", "/").split("/")
        if c.strip()
    ]
    if (
        len(parts) >= 2
        and re.fullmatch(r"\d{4,7}\.html?", parts[-1], re.IGNORECASE)
        and "archives" in parts[-2].lower()
    ):
        return f"{parts[-2]}/{parts[-1]}"
    return None


def _norm_title(t):
    t = str(t or "").strip()
    t = re.sub(
        r":?\s*Archive Entry From Brad DeLong[’']s (?:Webjournal|Semi-Daily Journal)\s*$",
        "",
        t,
        flags=re.IGNORECASE,
    )
    t = re.sub(r"^Brad DeLong[’']s Website:\s*", "", t, flags=re.IGNORECASE)
    return re.sub(r"\s+", " ", t).strip().lower()


@st.cache_data(show_spinner=False)
def load_timeline_df():
    conn = get_db_connection()
    if HAS_MERGED_SCHEMA:
        df = pd.read_sql_query(
            "SELECT rel_path, title, post_date, exact_post_datetime, is_canonical_post, is_unique_corpus "
            "FROM posts WHERE (exact_post_datetime IS NOT NULL) OR (post_date IS NOT NULL AND post_date != 'Unknown');",
            conn,
        )
    else:
        df = pd.read_sql_query(
            "SELECT rel_path, title, post_date FROM posts "
            "WHERE post_date IS NOT NULL AND post_date != 'Unknown';",
            conn,
        )
    conn.close()

    cleaned_legacy = df["post_date"].astype(str).str.replace(r"\s+", " ", regex=True).str.strip()
    legacy_dt = pd.to_datetime(cleaned_legacy, format="%B %d, %Y", errors="coerce")

    if HAS_MERGED_SCHEMA:
        exact_dt = pd.to_datetime(df["exact_post_datetime"], errors="coerce")
        df["date"] = exact_dt.fillna(legacy_dt)
    else:
        df["date"] = legacy_dt

    df = df.dropna(subset=["date"])
    df = df[(df["date"].dt.year >= 1990) & (df["date"].dt.year <= 2029)].copy()

    df["entry_key"] = df["rel_path"].map(_entry_key)
    df["is_entry"] = df["entry_key"].notna()

    def _dedupe_key(row):
        nt = _norm_title(row["title"])
        if nt:
            return f"{nt}|{row['date'].date()}"
        return row["entry_key"] or row["rel_path"]

    df["dedupe_key"] = df.apply(_dedupe_key, axis=1)
    return df


# --- 2. 侧边栏 ---
st.sidebar.title("🏛️ Brad DeLong's Corpus")
st.sidebar.caption("UC Berkeley Economics | Pre-2005 Archive")
st.sidebar.markdown(
    """
**J. Bradford DeLong's Semi-Daily Journal**  
A digital catalog with a mouth for 20+ years of economic history, policy critiques, and academic discourse.
"""
)
st.sidebar.divider()

try:
    conn = get_db_connection()
    total_posts = pd.read_sql_query("SELECT COUNT(*) AS c FROM posts;", conn).iloc[0]["c"]
    dated_posts = pd.read_sql_query(
        "SELECT COUNT(*) AS c FROM posts WHERE post_date != 'Unknown';", conn
    ).iloc[0]["c"]
    missing_asset_posts = pd.read_sql_query(
        "SELECT COUNT(*) AS c FROM posts WHERE missing_imgs > 0;", conn
    ).iloc[0]["c"]

    if HAS_MERGED_SCHEMA:
        unique_docs = pd.read_sql_query(
            "SELECT COUNT(*) AS c FROM posts WHERE is_unique_corpus = 1;", conn
        ).iloc[0]["c"]
        canonical_posts = pd.read_sql_query(
            "SELECT COUNT(*) AS c FROM posts WHERE is_canonical_post = 1;", conn
        ).iloc[0]["c"]
        verified_wb = pd.read_sql_query(
            "SELECT COUNT(*) AS c FROM posts WHERE wayback_url IS NOT NULL;", conn
        ).iloc[0]["c"]
    conn.close()

    st.sidebar.metric("Total Indexed HTML Files", f"{total_posts:,}")
    if HAS_MERGED_SCHEMA:
        st.sidebar.metric("Unique Deduplicated Docs", f"{unique_docs:,}")
        st.sidebar.metric("Canonical Blog Posts (2001–05)", f"{canonical_posts:,}")
        st.sidebar.metric("Verified Wayback Links", f"{verified_wb:,}")
    else:
        st.sidebar.metric("Temporal Recovery Rate", f"{(dated_posts / total_posts):.1%}")
        st.sidebar.metric(
            "Asset Integrity Rate",
            f"{((total_posts - missing_asset_posts) / total_posts):.1%}",
        )
except Exception as e:
    st.sidebar.warning(f"Database error: {e}")

st.sidebar.divider()
nav_mode = st.sidebar.radio(
    "Navigation",
    [
        "DeLong Full-Text Search (FTS5)",
        "Posting Timeline & Trends",
        "Resource Health Audit",
    ],
)

# --- 3. 页面 1：全文检索 ---
if nav_mode == "DeLong Full-Text Search (FTS5)":
    st.title("Search Brad DeLong's Weblog Archive")
    st.markdown(
        "**Explore deduplicated posts, early essays, and course notes from Brad"
        " DeLong's *Semi-Daily Journal* (Pre-2005).**  \n*Powered by SQLite FTS5,"
        " CDX Wayback Provenance & HTML Content Renderer.*"
    )

    if HAS_MERGED_SCHEMA:
        col1, col2, col3 = st.columns([2.5, 1.5, 1])
    else:
        col1, col3 = st.columns([3, 1])

    with col1:
        query_text = st.text_input(
            "Search keywords or phrases in DeLong's writings:",
            value="Zhu Rongji",
            placeholder="e.g. Zhu Rongji, Greenspan, East Asian Crisis, Krugman",
        )

    scope_filter_sql = ""
    if HAS_MERGED_SCHEMA:
        with col2:
            corpus_scope = st.selectbox(
                "Corpus Scope:",
                [
                    "Unique Archive (11,772 docs)",
                    "Canonical Blog Posts (2001–2005, 6,533)",
                    "Early Pages & Courses Only (5,239)",
                    "All Raw Files (Unfiltered, 21,531)",
                ],
                index=0,
            )
            if corpus_scope.startswith("Unique Archive"):
                scope_filter_sql = "AND p.is_unique_corpus = 1"
            elif corpus_scope.startswith("Canonical Blog"):
                scope_filter_sql = "AND p.is_canonical_post = 1"
            elif corpus_scope.startswith("Early Pages"):
                scope_filter_sql = "AND p.is_unique_corpus = 1 AND p.is_canonical_post = 0"

    with col3:
        max_results = st.selectbox(
            "Max results:", [5, 10, 20, 50], index=1
        )

    if query_text.strip():
        conn = get_db_connection()
        extra_select = (
            "p.exact_post_datetime, p.wayback_url, p.is_canonical_post, p.permalink,"
            if HAS_MERGED_SCHEMA
            else "NULL AS exact_post_datetime, NULL AS wayback_url, 0 AS is_canonical_post, p.permalink,"
        )

        search_sql = f"""
        SELECT 
            p.id, 
            p.title, 
            p.post_date, 
            p.rel_path, 
            p.body_clean,
            p.missing_imgs,
            {extra_select}
            snippet(posts_fts, 1, '<mark style="background-color: #fef08a; font-weight: bold; color: #1e293b;">', '</mark>', '...', 35) AS snippet_text
        FROM posts_fts
        JOIN posts p ON posts_fts.rowid = p.id
        WHERE posts_fts MATCH ?
        {scope_filter_sql}
        LIMIT ?;
        """
        try:
            results = pd.read_sql_query(
                search_sql, conn, params=(query_text, max_results)
            )
            conn.close()

            st.write(
                f"Found **{len(results)}** matching records by Brad DeLong (display"
                f" limit: {max_results}):"
            )

            for _, row in results.iterrows():
                post_title = (
                    row["title"] if row["title"] else "Untitled Entry / DeLong Note"
                )
                display_date = (
                    row["exact_post_datetime"]
                    if pd.notna(row["exact_post_datetime"]) and str(row["exact_post_datetime"]).strip()
                    else row["post_date"]
                )
                status_color = "green" if row["missing_imgs"] == 0 else "orange"
                status_label = (
                    "Complete Local Assets"
                    if row["missing_imgs"] == 0
                    else f"Missing {row['missing_imgs']} Images"
                )

                raw_body = str(row["body_clean"]).strip() if row["body_clean"] else ""
                is_local_valid = len(raw_body) > 40

                wb_home, yr_tag, wb_ts, post_urls, wb_calendar = get_wayback_info(
                    row["rel_path"], display_date, raw_body
                )

                with st.expander(
                    f"📝 {post_title} ({display_date})", expanded=True
                ):
                    top_c1, top_c2 = st.columns([3, 1])
                    with top_c1:
                        canonical_tag = (
                            " | :blue[**🏛️ Canonical Blog Post**]"
                            if row["is_canonical_post"] == 1
                            else ""
                        )
                        st.caption(
                            f"Record ID: `{row['id']}` | Local File: `{row['rel_path']}` | Integrity: :{status_color}[{status_label}]{canonical_tag}"
                        )
                    with top_c2:
                        if is_local_valid:
                            st.markdown(":green-background[✅ Local Archive Verified]")
                        else:
                            st.markdown(":blue-background[🌐 Sourced from Wayback]")

                    st.markdown(
                        f"**Matched Contextual Excerpt:**\n> {row['snippet_text']}",
                        unsafe_allow_html=True,
                    )

                    if st.toggle("📖 Read Full Article", key=f"read_{row['id']}"):
                        if is_local_valid:
                            doc = format_post_as_html(raw_body, query_text)
                            components.html(doc, height=650, scrolling=True)
                        else:
                            st.warning(
                                "Local file content is missing or corrupt. Please use the Wayback link below."
                            )

                    recovered = get_recovered_assets(int(row["id"]))
                    if recovered:
                        st.markdown(
                            f"**🖼️ Recovered figures ({len(recovered)}) — fetched from Wayback:**"
                        )
                        img_cols = st.columns(min(len(recovered), 3))
                        for idx, (orig_path, wb_url) in enumerate(recovered[:6]):
                            with img_cols[idx % len(img_cols)]:
                                raw_wb = re.sub(
                                    r"/web/(\d+)/", r"/web/\1if_/", wb_url, count=1
                                )
                                try:
                                    st.image(
                                        raw_wb,
                                        caption=orig_path.split("/")[-1],
                                        use_container_width=True,
                                    )
                                except Exception:
                                    st.markdown(f"[{orig_path.split('/')[-1]}]({wb_url})")
                        if len(recovered) > 6:
                            st.caption(f"...and {len(recovered) - 6} more recovered figures")

                    # Wayback 链接（优先展示 CDX 精确匹配链接）
                    st.divider()
                    links = []
                    verified_wb_url = (
                        str(row["wayback_url"]).strip()
                        if pd.notna(row["wayback_url"]) and str(row["wayback_url"]).strip()
                        else None
                    )
                    if verified_wb_url:
                        links.append(
                            f"**[✅ Verified Wayback Snapshot (CDX)]({verified_wb_url})**"
                        )
                    elif post_urls:
                        direct = f"https://web.archive.org/web/{wb_ts}/{post_urls[0]}"
                        links.append(f"**[🌐 Open derived post on Wayback]({direct})**")

                    if pd.notna(row["permalink"]) and str(row["permalink"]).strip():
                        links.append(
                            f"[All captures of canonical URL](https://web.archive.org/web/*/{row['permalink']})"
                        )
                    elif post_urls:
                        links.append(
                            f"[All captures of this URL](https://web.archive.org/web/*/{post_urls[0]})"
                        )

                    links.append(f"[{yr_tag} homepage snapshot]({wb_home})")
                    links.append(f"[Browse all {yr_tag} captures]({wb_calendar})")
                    st.markdown(" · ".join(links))

                    if st.toggle(
                        "🔎 Check availability & debug", key=f"wb_{row['id']}"
                    ):
                        if verified_wb_url:
                            st.success("Pre-verified via Wayback Machine CDX API.")
                            st.code(
                                f"wayback_url = {verified_wb_url}\n"
                                f"permalink   = {row['permalink']!r}\n"
                                f"rel_path    = {row['rel_path']!r}"
                            )
                        elif post_urls:
                            hit, debug = find_closest_snapshot(tuple(post_urls), wb_ts)
                            if hit:
                                st.success("Snapshot found.")
                                st.markdown(f"[Open closest snapshot]({hit})")
                            else:
                                st.info(
                                    "No snapshot of this exact page was found. "
                                    "Use the homepage or calendar links above."
                                )
                            st.code(
                                f"rel_path = {row['rel_path']!r}\n"
                                f"post_date = {display_date!r}\n"
                                + "\n".join(debug or ["(all lookups returned a hit)"])
                            )
                        else:
                            st.warning("Could not derive a post URL from rel_path.")
                            st.code(f"rel_path = {row['rel_path']!r}")

        except Exception as err:
            st.error(f"Search query error: {err}")
            conn.close()

# --- 4. 页面 2：发帖时间轴趋势 ---
elif nav_mode == "Posting Timeline & Trends":
    st.title("Brad DeLong's Publishing Timeline")
    st.markdown(
        "*Volume of posts published across the pre-2005 archive, by date.*"
    )

    try:
        tl = load_timeline_df()
    except Exception as e:
        st.error(f"Timeline error: {e}")
        tl = None

    if tl is None or tl.empty:
        st.info("No timeline data available.")
    else:
        c1, c2 = st.columns(2)
        mode_options = (
            [
                "Canonical blog posts (2001–2005, 6,533)",
                "All unique archive docs (deduplicated)",
                "All dated records (raw)",
            ]
            if HAS_MERGED_SCHEMA
            else ["Unique posts (deduplicated)", "All dated records"]
        )
        with c1:
            count_mode = st.radio("Count", mode_options, horizontal=False)
        with c2:
            gran = st.radio("Granularity", ["By year", "By month"], horizontal=True)

        if count_mode.startswith("Canonical blog"):
            d = tl[tl["is_canonical_post"] == 1]
            st.caption(
                "Exact 6,533 individual Movable Type blog posts verified across the 2001–2005 archives."
            )
        elif count_mode.startswith("All unique"):
            d = tl[tl["is_unique_corpus"] == 1]
            st.caption(
                "All 11,772 deduplicated documents (canonical blog posts + early essays & course pages)."
            )
        elif count_mode.startswith("Unique"):
            d = tl[tl["is_entry"]].drop_duplicates("dedupe_key")
            st.caption(
                "Only single-entry pages are counted, and copies across backup folders are merged."
            )
        else:
            d = tl
            st.caption(
                "Every record with a valid date, including aggregator pages and duplicate backup copies."
            )

        if d.empty:
            st.info("No records match this view.")
        else:
            if gran == "By year":
                counts = d["date"].dt.year.value_counts().sort_index()
                label = "year"
                index = counts.index.astype(str)
            else:
                counts = d["date"].dt.to_period("M").astype(str).value_counts().sort_index()
                label = "month"
                index = counts.index

            chart_df = pd.DataFrame({"post_count": counts.values}, index=index)
            chart_df.index.name = label

            m1, m2, m3 = st.columns(3)
            m1.metric("Posts counted", f"{len(d):,}")
            m2.metric("Earliest", d["date"].min().strftime("%b %d, %Y"))
            m3.metric("Latest", d["date"].max().strftime("%b %d, %Y"))

            st.bar_chart(chart_df, color="#003262")
            st.dataframe(chart_df.reset_index(), use_container_width=True)

# --- 5. 页面 3：Asset Recovery ---
elif nav_mode == "Resource Health Audit":
    st.title("Asset Recovery & Health Audit")
    st.markdown(
        "*494 posts referenced images we couldn't bundle locally. We triaged every"
        " missing path into **chart / chrome / desktop / other** and queried the"
        " Wayback Machine to see which of DeLong's own figures can be rendered"
        " back into the article view.*"
    )

    conn = get_db_connection()
    has_recovery = (
        conn.execute(
            "SELECT name FROM sqlite_master WHERE type='table' AND name='missing_assets'"
        ).fetchone()
        is not None
    )

    if not has_recovery:
        st.warning(
            "`missing_assets` table not found. Run `python scripts/classify_missing.py`"
            " then `python scripts/wayback_recover.py` to populate it."
        )
        st.dataframe(
            pd.read_sql_query(
                "SELECT rel_path, title, total_imgs, missing_imgs, missing_samples "
                "FROM posts WHERE missing_imgs > 0 ORDER BY missing_imgs DESC;",
                conn,
            ),
            use_container_width=True,
        )
        conn.close()
    else:
        kind_counts = pd.read_sql_query(
            "SELECT kind, COUNT(*) AS n FROM missing_assets GROUP BY kind ORDER BY n DESC;",
            conn,
        )
        status_counts = pd.read_sql_query(
            "SELECT status, COUNT(*) AS n FROM missing_assets GROUP BY status ORDER BY n DESC;",
            conn,
        )

        total = int(kind_counts["n"].sum())
        n_chart = int(kind_counts.loc[kind_counts["kind"] == "chart", "n"].sum())
        n_chrome = int(kind_counts.loc[kind_counts["kind"] == "chrome", "n"].sum())
        n_desktop = int(kind_counts.loc[kind_counts["kind"] == "desktop", "n"].sum())
        n_other = int(kind_counts.loc[kind_counts["kind"] == "other", "n"].sum())
        n_hit = int(status_counts.loc[status_counts["status"] == "hit", "n"].sum())
        n_miss = int(status_counts.loc[status_counts["status"] == "miss", "n"].sum())
        n_pending = int(status_counts.loc[status_counts["status"] == "pending", "n"].sum())
        recoverable = n_chart + n_other
        recovery_rate = (n_hit / recoverable) if recoverable else 0.0

        st.subheader("Triage of missing-asset references")
        c1, c2, c3, c4, c5 = st.columns(5)
        c1.metric("Total refs", f"{total:,}")
        c2.metric("📊 DeLong charts", f"{n_chart:,}", help="Figures from j-bradford-delong.net — recoverable via Wayback")
        c3.metric("🧩 Third-party chrome", f"{n_chrome:,}", help="LA Times logo, nav icons, spacers — skip")
        c4.metric("💾 Desktop folder", f"{n_desktop:,}", help="Files from DeLong's local Mac Desktop — unrecoverable")
        c5.metric("❓ Other", f"{n_other:,}")

        st.subheader("Wayback recovery result")
        c1, c2, c3, c4 = st.columns(4)
        c1.metric("✅ Snapshots found", f"{n_hit:,}")
        c2.metric("❌ No snapshot", f"{n_miss:,}")
        c3.metric("⏳ Pending", f"{n_pending:,}")
        c4.metric(
            "Recovery rate",
            f"{recovery_rate:.1%}",
            help=f"{n_hit} / {recoverable} chart+other references have a Wayback snapshot",
        )

        if n_pending > 0:
            st.info(
                f"{n_pending} references are still pending — run"
                " `python scripts/wayback_recover.py` to query them against Wayback."
            )

        st.subheader("Most frequently missing files")
        top_missing = pd.read_sql_query(
            """
            SELECT orig_path, kind, status, wayback_url, COUNT(*) AS posts_affected
            FROM missing_assets
            GROUP BY orig_path
            ORDER BY posts_affected DESC
            LIMIT 25;
            """,
            conn,
        )
        st.dataframe(
            top_missing,
            use_container_width=True,
            column_config={
                "wayback_url": st.column_config.LinkColumn("Wayback", display_text="open"),
                "orig_path": st.column_config.TextColumn("Path", width="large"),
                "posts_affected": st.column_config.NumberColumn("Posts affected"),
            },
        )

        with st.expander("Browse by post (filterable)"):
            kind_filter = st.multiselect(
                "Kind",
                ["chart", "chrome", "desktop", "other"],
                default=["chart", "other"],
            )
            status_filter = st.multiselect(
                "Status",
                ["hit", "miss", "pending", "skip"],
                default=["hit", "miss", "pending"],
            )
            if kind_filter and status_filter:
                placeholders_k = ",".join("?" * len(kind_filter))
                placeholders_s = ",".join("?" * len(status_filter))
                per_post = pd.read_sql_query(
                    f"""
                    SELECT p.rel_path, p.title, ma.orig_path, ma.kind, ma.status, ma.wayback_url
                    FROM missing_assets ma
                    JOIN posts p ON p.id = ma.post_id
                    WHERE ma.kind IN ({placeholders_k}) AND ma.status IN ({placeholders_s})
                    ORDER BY p.rel_path, ma.orig_path
                    LIMIT 500;
                    """,
                    conn,
                    params=kind_filter + status_filter,
                )
                st.dataframe(
                    per_post,
                    use_container_width=True,
                    column_config={
                        "wayback_url": st.column_config.LinkColumn("Wayback", display_text="open"),
                    },
                )
            else:
                st.caption("Select at least one kind and status filter.")

        conn.close()
