"""
app.py — Lakehouse Copilot Streamlit arayüzü.

Çalıştırma:
    streamlit run app.py
"""

from __future__ import annotations

import asyncio
import time
from datetime import datetime
import structlog

import pandas as pd
import streamlit as st

# ─── Sayfa yapılandırması (ilk st çağrısı olmalı) ────────────────────────────
st.set_page_config(
    page_title="Lakehouse Copilot",
    page_icon="🏔️",
    layout="wide",
    initial_sidebar_state="expanded",
)

# ─── Tek seferlik başlatma ────────────────────────────────────────────────────
@st.cache_resource
def _init():
    from src.core.logging import configure_logging
    from src.core.tracing import init_tracing
    from src.config import get_settings
    configure_logging()
    init_tracing()
    get_settings()

_init()

# ─── Session state başlangıç değerleri ───────────────────────────────────────
if "history" not in st.session_state:
    st.session_state.history: list[dict] = []
if "selected" not in st.session_state:
    st.session_state.selected = None
if "pending_question" not in st.session_state:
    st.session_state.pending_question = ""

# ─── Approval callback ────────────────────────────────────────────────────────
async def _approval_callback(payload: dict) -> str:
    estimated = payload.get("estimated_rows", 0)
    st.session_state._approval_warning = (
        f"Bu sorgu tahminen **{estimated:,}** satır döndürdü (eşik aşıldı, otomatik onaylandı)."
    )
    return "y"

# ─── Agent çalıştırıcı ────────────────────────────────────────────────────────
def _run_question(question: str) -> dict:
    from src.agent.graph import run

    async def _inner():
        return await run(question, approval_callback=_approval_callback)

    # Streamlit'in event loop'uyla çakışmaması için yeni loop aç
    loop = asyncio.new_event_loop()
    try:
        state = loop.run_until_complete(_inner())
    finally:
        loop.close()

    result = state.get("result")
    return {
        "question":  question,
        "sql":       state.get("sql", ""),
        "source":    state.get("source", "duckdb"),
        "answer":    state.get("final_answer", ""),
        "rows":      result.row_count if result else 0,
        "data":      result.data[:500] if result else [],
        "columns":   result.columns if result else [],
        "elapsed":   round(time.perf_counter(), 2),  # overwritten below
        "attempts":  state.get("attempt", 0),
        "ts":        datetime.now().strftime("%H:%M:%S"),
        "success":   result is not None,
    }

# ─── CSS ─────────────────────────────────────────────────────────────────────
st.markdown(
    """
    <style>
    .stButton > button { border-radius: 8px; }
    .metric-label { font-size: 0.75rem !important; }
    div[data-testid="stExpander"] { border: 1px solid #e0e0e0; border-radius: 8px; }
    </style>
    """,
    unsafe_allow_html=True,
)

# ─── Sidebar ─────────────────────────────────────────────────────────────────
with st.sidebar:
    st.markdown("## 🏔️ Lakehouse Copilot")
    st.caption("Doğal dilde veri sorgulama ajanı")
    st.divider()

    if st.session_state.history:
        st.markdown("**Soru Geçmişi**")
        for i, item in enumerate(reversed(st.session_state.history)):
            icon = "✅" if item["success"] else "❌"
            idx = len(st.session_state.history) - 1 - i
            label = f"{icon} {item['ts']}  {item['question'][:35]}..."
            if st.button(label, key=f"hist_{i}", use_container_width=True):
                st.session_state.selected = idx
                st.rerun()
        st.divider()
        if st.button("🗑️ Geçmişi Temizle", use_container_width=True):
            st.session_state.history = []
            st.session_state.selected = None
            st.rerun()
    else:
        st.caption("Henüz soru sorulmadı.")

    st.divider()
    st.markdown("**Bağlı Kaynaklar**")
    st.markdown("🟢 DuckDB (Parquet)")
    st.markdown("🟢 PostgreSQL")

# ─── Ana başlık ───────────────────────────────────────────────────────────────
st.title("🏔️ Lakehouse Copilot")
st.caption(
    "Türkçe veya İngilizce sorunuzu yazın — agent SQL üretir, çalıştırır ve iş diline çevirir."
)

# ─── Soru girişi ─────────────────────────────────────────────────────────────
with st.form("ask_form", clear_on_submit=True):
    col_inp, col_btn = st.columns([6, 1])
    with col_inp:
        question_input = st.text_input(
            "Soru",
            placeholder="örn: 2024'te en çok ciro yapan 5 kategori hangileri?",
            label_visibility="collapsed",
        )
    with col_btn:
        submitted = st.form_submit_button("Sor →", type="primary", use_container_width=True)

# Örnek sorular
st.caption("Hızlı başlangıç:")
examples = [
    "Top 5 categories by revenue in 2024?",
    "Hiç sipariş vermemiş müşteri sayısı?",
    "Ödeme kaydı olmayan siparişleri duruma göre say.",
    "Kar marjı en yüksek 10 ürün hangileri?",
]
ex_cols = st.columns(len(examples))
for col, ex in zip(ex_cols, examples):
    if col.button(ex, use_container_width=True, key=f"ex_{ex[:10]}"):
        st.session_state.pending_question = ex
        st.rerun()

st.divider()

# ─── Soru tetikleyici ─────────────────────────────────────────────────────────
active_question = ""
if submitted and question_input.strip():
    active_question = question_input.strip()
elif st.session_state.pending_question:
    active_question = st.session_state.pending_question
    st.session_state.pending_question = ""

if active_question:
    st.session_state._approval_warning = None
    with st.spinner(f"**'{active_question[:60]}...'** için düşünüyorum..."):
        t0 = time.perf_counter()
        try:
            entry = _run_question(active_question)
            entry["elapsed"] = round(time.perf_counter() - t0, 2)
        except Exception as exc:
            st.error(f"Beklenmeyen hata: {exc}")
            st.stop()

    st.session_state.history.append(entry)
    st.session_state.selected = len(st.session_state.history) - 1
    st.rerun()

# ─── Sonuç göster ─────────────────────────────────────────────────────────────
selected_idx = st.session_state.selected
if selected_idx is not None and st.session_state.history:
    entry = st.session_state.history[selected_idx]

    # Approval uyarısı
    if st.session_state.get("_approval_warning"):
        st.warning(st.session_state._approval_warning)

    # Soru başlığı
    st.markdown(f"### 💬 {entry['question']}")

    # Cevap kutusu
    if entry["answer"]:
        st.info(entry["answer"], icon="🤖")
    else:
        st.error("Agent bir cevap üretemedi. SQL kısmını inceleyiniz.", icon="❌")

    # Metrikler
    m1, m2, m3, m4 = st.columns(4)
    m1.metric("Kaynak", entry["source"].upper())
    m2.metric("Satır Sayısı", f"{entry['rows']:,}")
    m3.metric("Süre", f"{entry['elapsed']}s")
    m4.metric("Deneme", entry["attempts"])

    # SQL
    if entry["sql"]:
        with st.expander("🔍 Üretilen SQL'i Göster", expanded=False):
            st.code(entry["sql"], language="sql")

    # Tablo
    if entry["data"]:
        st.markdown("#### 📊 Ham Sonuç")
        total = entry["rows"]
        shown = len(entry["data"])
        if total > shown:
            st.caption(f"İlk {shown} satır gösteriliyor — toplam {total:,} satır")
        df = pd.DataFrame(entry["data"])
        st.dataframe(df, use_container_width=True, hide_index=True)

elif not st.session_state.history:
    # Hoşgeldiniz ekranı
    st.markdown(
        """
        <div style="text-align:center; padding: 4rem 0; color: #888;">
            <h3>Sorunuzu yukarıya yazın</h3>
            <p>Agent şemayı keşfeder, SQL üretir, çalıştırır ve özetler.</p>
        </div>
        """,
        unsafe_allow_html=True,
    )