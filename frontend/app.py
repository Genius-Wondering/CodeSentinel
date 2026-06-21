"""
CodeSentinel — Streamlit Frontend  v0.3.0
Supports:
  - Index a local repo path
  - Upload & index documents  (PDF / MD / TXT / DOCX / XLSX)
  - Ask questions about the indexed knowledge base
"""
import os
import requests
import streamlit as st

API_BASE = os.getenv("API_BASE", "http://localhost:8000/api/v1")

# ── Page config & custom CSS ──────────────────────────────────────────────────
st.set_page_config(
    page_title="CodeSentinel",
    page_icon="🔍",
    layout="wide",
    initial_sidebar_state="expanded",
)

st.markdown("""
<style>
/* ── fonts ───────────────────────────────────────────────── */
@import url('https://fonts.googleapis.com/css2?family=JetBrains+Mono:wght@400;600;700&family=DM+Sans:wght@300;400;500;600&display=swap');

html, body, [class*="css"] {
    font-family: 'DM Sans', sans-serif;
}

/* ── page background ─────────────────────────────────────── */
.stApp {
    background: #0d1117;
    color: #e6edf3;
}

/* ── sidebar ─────────────────────────────────────────────── */
section[data-testid="stSidebar"] {
    background: #161b22 !important;
    border-right: 1px solid #21262d;
}
section[data-testid="stSidebar"] * {
    color: #c9d1d9 !important;
}

/* ── title bar ───────────────────────────────────────────── */
.cs-header {
    display: flex;
    align-items: baseline;
    gap: 12px;
    margin-bottom: 4px;
}
.cs-logo {
    font-family: 'JetBrains Mono', monospace;
    font-size: 1.75rem;
    font-weight: 700;
    color: #58a6ff;
    letter-spacing: -1px;
}
.cs-version {
    font-family: 'JetBrains Mono', monospace;
    font-size: 0.72rem;
    color: #484f58;
    border: 1px solid #30363d;
    border-radius: 4px;
    padding: 1px 6px;
}
.cs-tagline {
    font-size: 0.82rem;
    color: #8b949e;
    margin-bottom: 24px;
}

/* ── stat pills ──────────────────────────────────────────── */
.stat-row {
    display: flex;
    gap: 8px;
    flex-wrap: wrap;
    margin: 10px 0 18px;
}
.stat-pill {
    background: #21262d;
    border: 1px solid #30363d;
    border-radius: 20px;
    padding: 3px 10px;
    font-size: 0.75rem;
    font-family: 'JetBrains Mono', monospace;
    color: #8b949e;
}
.stat-pill span { color: #58a6ff; font-weight: 600; }

/* ── section labels ──────────────────────────────────────── */
.section-label {
    font-family: 'JetBrains Mono', monospace;
    font-size: 0.68rem;
    font-weight: 600;
    letter-spacing: 0.12em;
    text-transform: uppercase;
    color: #484f58;
    margin: 20px 0 8px;
    padding-bottom: 6px;
    border-bottom: 1px solid #21262d;
}

/* ── upload drop zone ────────────────────────────────────── */
[data-testid="stFileUploader"] {
    background: #161b22 !important;
    border: 1.5px dashed #30363d !important;
    border-radius: 8px !important;
    transition: border-color 0.2s;
}
[data-testid="stFileUploader"]:hover {
    border-color: #58a6ff !important;
}
[data-testid="stFileUploader"] label {
    color: #8b949e !important;
    font-size: 0.82rem !important;
}

/* ── uploaded file badges ────────────────────────────────── */
.file-badge-row { display: flex; flex-wrap: wrap; gap: 6px; margin: 8px 0; }
.file-badge {
    display: inline-flex; align-items: center; gap: 5px;
    background: #21262d; border: 1px solid #30363d; border-radius: 5px;
    padding: 3px 9px; font-size: 0.78rem; font-family: 'JetBrains Mono', monospace;
    color: #c9d1d9;
}
.file-badge .ext {
    background: #1f6feb; color: #fff; border-radius: 3px;
    padding: 0 4px; font-size: 0.65rem; font-weight: 700;
}

/* ── buttons ─────────────────────────────────────────────── */
.stButton > button {
    background: #21262d !important;
    border: 1px solid #30363d !important;
    color: #c9d1d9 !important;
    border-radius: 6px !important;
    font-family: 'DM Sans', sans-serif !important;
    font-size: 0.84rem !important;
    transition: all 0.15s ease;
}
.stButton > button:hover {
    border-color: #58a6ff !important;
    color: #58a6ff !important;
}
[data-testid="baseButton-primary"] > button,
.stButton > button[kind="primary"] {
    background: #1f6feb !important;
    border-color: #1f6feb !important;
    color: #fff !important;
    font-weight: 600 !important;
}
[data-testid="baseButton-primary"] > button:hover,
.stButton > button[kind="primary"]:hover {
    background: #388bfd !important;
    border-color: #388bfd !important;
    color: #fff !important;
}

/* ── text inputs ─────────────────────────────────────────── */
.stTextInput > div > div > input,
.stTextArea textarea {
    background: #161b22 !important;
    border: 1px solid #30363d !important;
    color: #e6edf3 !important;
    border-radius: 6px !important;
    font-family: 'DM Sans', sans-serif !important;
}
.stTextInput > div > div > input:focus,
.stTextArea textarea:focus {
    border-color: #58a6ff !important;
    box-shadow: 0 0 0 2px rgba(88,166,255,0.15) !important;
}

/* ── radio ───────────────────────────────────────────────── */
.stRadio > div { gap: 4px !important; }
.stRadio label { font-size: 0.84rem !important; }

/* ── answer card ─────────────────────────────────────────── */
.answer-card {
    background: #161b22;
    border: 1px solid #21262d;
    border-left: 3px solid #58a6ff;
    border-radius: 8px;
    padding: 20px 24px;
    margin-top: 16px;
}
.answer-card h3 {
    font-family: 'JetBrains Mono', monospace;
    font-size: 0.78rem;
    letter-spacing: 0.1em;
    text-transform: uppercase;
    color: #58a6ff;
    margin-bottom: 14px;
}

/* ── agent plan expander ─────────────────────────────────── */
.streamlit-expanderHeader {
    background: #161b22 !important;
    border: 1px solid #21262d !important;
    border-radius: 6px !important;
    color: #8b949e !important;
    font-size: 0.82rem !important;
    font-family: 'JetBrains Mono', monospace !important;
}

/* ── success / error / warning ───────────────────────────── */
.stAlert {
    border-radius: 6px !important;
    font-size: 0.84rem !important;
}

/* ── divider ─────────────────────────────────────────────── */
hr { border-color: #21262d !important; }

/* ── spinner ─────────────────────────────────────────────── */
.stSpinner > div { border-top-color: #58a6ff !important; }

/* ── query area label ────────────────────────────────────── */
.query-label {
    font-family: 'JetBrains Mono', monospace;
    font-size: 0.78rem;
    color: #8b949e;
    margin-bottom: 6px;
    letter-spacing: 0.05em;
}
</style>
""", unsafe_allow_html=True)

# ── Helper: fetch stats (cached 5s) ──────────────────────────────────────────
@st.cache_data(ttl=5)
def fetch_stats():
    try:
        return requests.get(f"{API_BASE}/stats", timeout=5).json()
    except requests.RequestException:
        return None

# ── Header ────────────────────────────────────────────────────────────────────
st.markdown("""
<div class="cs-header">
  <span class="cs-logo">⬡ CodeSentinel</span>
  <span class="cs-version">v0.3.0</span>
</div>
<div class="cs-tagline">AI-powered multi-agent code & document understanding system</div>
""", unsafe_allow_html=True)

# ── Sidebar ───────────────────────────────────────────────────────────────────
with st.sidebar:

    # ── Stats ─────────────────────────────────────────────────────────────────
    stats = fetch_stats()
    if stats is None:
        st.warning("⚠ Backend unreachable — run `make run`")
    else:
        if not stats.get("openai_configured"):
            st.warning("⚠ OPENAI_API_KEY not set on backend")

        total  = stats.get("total_chunks", 0)
        code_n = stats.get("code_chunks", 0)
        doc_n  = stats.get("doc_chunks", 0)
        hybrid = stats.get("hybrid_search_active", False)

        st.markdown(f"""
        <div class="stat-row">
          <div class="stat-pill">total <span>{total}</span></div>
          <div class="stat-pill">code <span>{code_n}</span></div>
          <div class="stat-pill">docs <span>{doc_n}</span></div>
          <div class="stat-pill">hybrid <span>{"on" if hybrid else "off"}</span></div>
        </div>
        """, unsafe_allow_html=True)

    # ── Code Indexing ─────────────────────────────────────────────────────────
    st.markdown('<div class="section-label">Code Index</div>', unsafe_allow_html=True)

    repo_input = st.text_input(
        "Local repo path",
        placeholder="/path/to/your/project",
        label_visibility="collapsed",
    )
    if st.button("⬡ Index Local Repo", use_container_width=True):
        if repo_input:
            with st.spinner("Scanning & indexing..."):
                resp = requests.post(
                    f"{API_BASE}/index/local",
                    json={"repo_path": repo_input},
                    timeout=600,
                )
            if resp.ok:
                d = resp.json()
                st.success(f"✓ {d['indexed_chunks']} chunks indexed")
                st.cache_data.clear()
            else:
                _detail = resp.json().get("detail", resp.text) if "application/json" in resp.headers.get("content-type","") else resp.text
                st.error(_detail)
        else:
            st.warning("Enter a repo path first.")

    # ── Document Upload ───────────────────────────────────────────────────────
    st.markdown('<div class="section-label">Document Upload</div>', unsafe_allow_html=True)

    ACCEPTED_TYPES  = ["pdf", "md", "txt", "docx", "xlsx", "xls"]
    ACCEPTED_LABEL  = "PDF · MD · TXT · DOCX · XLSX"
    EXT_COLORS = {
        "pdf":  "#f85149", "md":   "#3fb950", "txt":  "#8b949e",
        "docx": "#58a6ff", "xlsx": "#56d364", "xls":  "#56d364",
    }

    uploaded_files = st.file_uploader(
        f"Drop files here  ·  {ACCEPTED_LABEL}",
        type=ACCEPTED_TYPES,
        accept_multiple_files=True,
        label_visibility="visible",
    )

    # Show badge row for queued files
    if uploaded_files:
        badge_html = '<div class="file-badge-row">'
        for uf in uploaded_files:
            ext = uf.name.rsplit(".", 1)[-1].lower() if "." in uf.name else "?"
            color = EXT_COLORS.get(ext, "#8b949e")
            size_kb = round(uf.size / 1024, 1) if uf.size else "?"
            badge_html += (
                f'<div class="file-badge">'
                f'<span class="ext" style="background:{color}">{ext.upper()}</span>'
                f'{uf.name[:22]}{"…" if len(uf.name)>22 else ""}'
                f'<span style="color:#484f58;font-size:0.7rem">{size_kb} KB</span>'
                f'</div>'
            )
        badge_html += '</div>'
        st.markdown(badge_html, unsafe_allow_html=True)

        if st.button("⬆ Upload & Index All", type="primary", use_container_width=True):
            success_count = 0
            fail_count    = 0
            total_chunks  = 0

            progress = st.progress(0, text="Uploading…")
            status   = st.empty()

            for i, uf in enumerate(uploaded_files):
                status.markdown(
                    f'<span style="font-size:0.8rem;color:#8b949e">Indexing '
                    f'<code style="color:#58a6ff">{uf.name}</code>…</span>',
                    unsafe_allow_html=True,
                )
                progress.progress((i) / len(uploaded_files), text=f"{i}/{len(uploaded_files)} files")

                try:
                    file_bytes = uf.getvalue()
                    resp = requests.post(
                        f"{API_BASE}/index/upload",
                        files={"file": (uf.name, file_bytes, uf.type or "application/octet-stream")},
                        timeout=300,
                    )
                    if resp.ok:
                        d = resp.json()
                        total_chunks += d.get("indexed_chunks", 0)
                        success_count += 1
                    else:
                        _detail = resp.json().get("detail", resp.text) if "application/json" in resp.headers.get("content-type","") else resp.text
                        st.error(f"✗ {uf.name}: {_detail}")
                        fail_count += 1
                except Exception as exc:
                    st.error(f"✗ {uf.name}: {exc}")
                    fail_count += 1

            progress.progress(1.0, text="Done")
            status.empty()
            st.cache_data.clear()

            if success_count:
                st.success(
                    f"✓ {success_count} file{'s' if success_count>1 else ''} indexed "
                    f"— {total_chunks} new chunks added"
                    + (f"  ·  {fail_count} failed" if fail_count else "")
                )
            elif fail_count:
                st.error(f"All {fail_count} files failed.")

    else:
        st.markdown(
            '<p style="font-size:0.78rem;color:#484f58;margin:4px 0 0">'
            'Supported: PDF, Markdown, TXT, Word, Excel</p>',
            unsafe_allow_html=True,
        )

# ── Main: Query area ──────────────────────────────────────────────────────────
col_q, col_scope = st.columns([5, 1])

with col_q:
    st.markdown('<div class="query-label">// ask anything about the indexed codebase & documents</div>', unsafe_allow_html=True)
    query = st.text_area(
        "query",
        placeholder="Where is JWT authentication implemented?\nWhat tables are in the Q1 Sales sheet?\nSummarise the architecture document.",
        height=90,
        label_visibility="collapsed",
    )

with col_scope:
    st.markdown('<div style="height:22px"></div>', unsafe_allow_html=True)
    scope = st.selectbox(
        "Scope",
        ["All sources", "Code only", "Docs only"],
        label_visibility="visible",
    )

source_type_map = {
    "All sources": None,
    "Code only":   "code",
    "Docs only":   "doc",
}

btn_col, _ = st.columns([1, 5])
with btn_col:
    ask_clicked = st.button("Ask ›", type="primary", use_container_width=True)

if ask_clicked and query.strip():
    with st.spinner("Thinking…"):
        resp = requests.post(
            f"{API_BASE}/ask",
            json={"query": query.strip(), "source_type": source_type_map[scope]},
            timeout=120,
        )

    if resp.ok:
        data = resp.json()

        # ── Confidence + iterations badges ────────────────────────────────
        conf = data.get("confidence", "low")
        iters = data.get("iterations", 1)
        conf_color = {"high": "#3fb950", "medium": "#d29922", "low": "#f85149"}.get(conf, "#8b949e")
        st.markdown(
            f'<div style="display:flex;gap:8px;margin:8px 0 12px">'
            f'<span style="background:#21262d;border:1px solid {conf_color};border-radius:20px;'
            f'padding:2px 10px;font-size:0.75rem;font-family:JetBrains Mono,monospace;color:{conf_color}">'
            f'confidence: {conf}</span>'
            f'<span style="background:#21262d;border:1px solid #30363d;border-radius:20px;'
            f'padding:2px 10px;font-size:0.75rem;font-family:JetBrains Mono,monospace;color:#8b949e">'
            f'iterations: {iters}</span>'
            f'</div>',
            unsafe_allow_html=True,
        )

        # ── Agent plan (collapsed by default) ─────────────────────────────
        with st.expander("🧠  Agent plan  ·  click to expand"):
            ic1, ic2 = st.columns(2)
            with ic1:
                st.markdown(f"**Intent:** `{data.get('intent','unknown')}`")
                st.markdown(f"**Scope:** `{data.get('source_type','cross')}`")
                rewritten = data.get("rewritten_query","")
                if rewritten and rewritten != data.get("query",""):
                    st.markdown(f"**Rewritten query:**")
                    st.code(rewritten, language=None)
                hyde = data.get("hyde_passage","")
                if hyde:
                    st.markdown(f"**HyDE passage:**")
                    st.caption(hyde)
            with ic2:
                st.markdown("**Sub-queries issued:**")
                for sq in data.get("sub_queries", []):
                    st.markdown(f"- `{sq}`")

        # ── Answer card ────────────────────────────────────────────────────
        st.markdown('<div class="answer-card"><h3>Answer</h3>', unsafe_allow_html=True)
        st.markdown(data["answer"])
        st.markdown('</div>', unsafe_allow_html=True)

    else:
        _detail = (
            resp.json().get("detail", resp.text)
            if "application/json" in resp.headers.get("content-type", "")
            else resp.text
        )
        st.error(_detail)

elif ask_clicked:
    st.warning("Please enter a question first.")
