# -*- coding: utf-8 -*-
"""
BrailleFlow — เว็บแอปพลิเคชันท่อส่งข้อมูลสำหรับแปลงเอกสารการเรียนรู้คุณภาพต่ำ
สู่เอกสารอักษรเบรลล์มาตรฐาน

รันด้วยคำสั่ง:  streamlit run app.py
"""
from __future__ import annotations
import io
import json
import time
from datetime import datetime

import streamlit as st
from PIL import Image, ImageDraw

from modules import ingestion, quality, pipeline, braille

st.set_page_config(
    page_title="BrailleFlow",
    page_icon="⠃",
    layout="wide",
    initial_sidebar_state="collapsed",
)

# ---------------------------------------------------------------------------
# Custom styling — โทนสีเรียบ อ่านง่าย คอนทราสต์สูง (เหมาะกับหัวข้องานด้าน Accessibility)
# ---------------------------------------------------------------------------
st.markdown(
    """
    <style>
    .stApp { background-color: #F7F5F1; }
    h1, h2, h3 { color: #1C1F26; letter-spacing: -0.01em; }
    .bf-hero {
        padding: 2.0rem 2.2rem;
        background: #2B3A67;
        border-radius: 6px;
        color: #F3EFE6;
        margin-bottom: 1.4rem;
    }
    .bf-hero h1 { color: #F3EFE6; margin-bottom: 0.2rem; font-size: 2.1rem; }
    .bf-hero p { color: #C7CCDE; font-size: 1.02rem; margin: 0; }
    .bf-dotcell { display:inline-block; }
    .bf-pill {
        display:inline-block; padding: 0.15rem 0.6rem; border-radius: 999px;
        font-size: 0.78rem; font-weight: 600; margin-right: 0.35rem;
    }
    .bf-pill-noise { background:#F3E0CB; color:#8A4B08; }
    .bf-pill-clean { background:#D9E6DA; color:#245A32; }
    .bf-metric-box {
        background:#FFFFFF; border:1px solid #E4E0D6; border-radius:6px;
        padding: 0.9rem 1.0rem;
    }
    .bf-mono { font-family: 'DejaVu Sans Mono', monospace; }
    </style>
    """,
    unsafe_allow_html=True,
)

for key, default in [
    ("ingest_result", None),
    ("quality_report", None),
    ("pipeline_out", None),   # (cleaned, ordered, tree)
    ("brf_bundle", None),     # (brf_content, braille_result)
]:
    if key not in st.session_state:
        st.session_state[key] = default


# ---------------------------------------------------------------------------
# Helper rendering functions
# ---------------------------------------------------------------------------

FLAG_COLORS = {
    "floating_mark": "#C0392B",  # แดง
    "oov": "#C08A2E",            # อำพัน
    "garbage": "#7A7A7A",        # เทา
    "low_conf": "#8E44AD",       # ม่วง
}


def draw_flagged_overlay(page_img: Image.Image, flags_for_page) -> Image.Image:
    img = page_img.copy()
    draw = ImageDraw.Draw(img)
    for tf in flags_for_page:
        t = tf.token
        color = None
        if tf.floating_mark:
            color = FLAG_COLORS["floating_mark"]
        elif tf.garbage:
            color = FLAG_COLORS["garbage"]
        elif tf.oov:
            color = FLAG_COLORS["oov"]
        elif tf.low_conf:
            color = FLAG_COLORS["low_conf"]
        if color:
            draw.rectangle(
                [t.left - 2, t.top - 2, t.right + 2, t.bottom + 2],
                outline=color,
                width=3,
            )
    return img


def render_tactile_svg(cells, max_cells: int = 60, cells_per_row: int = 15) -> str:
    """สร้าง SVG แสดงหน้าสัมผัสจำลอง (tactile grid) จากรายการเซลล์เบรลล์"""
    cell_w, cell_h = 34, 46
    dot_r = 3.6
    dot_positions = {
        1: (9, 9), 2: (9, 23), 3: (9, 37),
        4: (23, 9), 5: (23, 23), 6: (23, 37),
    }
    shown = cells[:max_cells]
    rows = (len(shown) + cells_per_row - 1) // cells_per_row if shown else 1
    width = cells_per_row * cell_w + 20
    height = rows * cell_h + 20

    svg = [f'<svg xmlns="http://www.w3.org/2000/svg" viewBox="0 0 {width} {height}" width="100%" height="{height}">']
    svg.append(f'<rect x="0" y="0" width="{width}" height="{height}" fill="#FFFFFF" rx="4"/>')
    for i, dots in enumerate(shown):
        row = i // cells_per_row
        col = i % cells_per_row
        ox = 10 + col * cell_w
        oy = 10 + row * cell_h
        for d in range(1, 7):
            dx, dy = dot_positions[d]
            filled = d in dots
            fill = "#2B3A67" if filled else "none"
            stroke = "#2B3A67" if filled else "#D8D3C6"
            svg.append(
                f'<circle cx="{ox+dx}" cy="{oy+dy}" r="{dot_r}" '
                f'fill="{fill}" stroke="{stroke}" stroke-width="1.2"/>'
            )
    svg.append("</svg>")
    return "".join(svg)


def all_original_text(ingest_result) -> str:
    return " ".join(t.text for t in ingest_result.tokens)


def all_pipeline_text(tree) -> str:
    paras = []
    for s in tree.sections:
        for p in s.paragraphs:
            paras.append(p.text)
    return "\n".join(paras)


# ---------------------------------------------------------------------------
# Hero
# ---------------------------------------------------------------------------
st.markdown(
    """
    <div class="bf-hero">
        <h1>⠃⠗⠁⠊⠇⠇⠑ &nbsp;BrailleFlow</h1>
        <p>แปลงเอกสารการเรียนรู้คุณภาพต่ำ (สแกน/หลายคอลัมน์) ให้เป็นข้อมูลโครงสร้างสูง
        พร้อมส่งออกเป็นอักษรเบรลล์มาตรฐาน (.brf) ด้วยกระบวนการ Data Engineering</p>
    </div>
    """,
    unsafe_allow_html=True,
)

tab1, tab2, tab3 = st.tabs([
    "① นำเข้าเอกสาร (Ingestion)",
    "② ตรวจสอบคุณภาพ & รัน Pipeline",
    "③ พรีวิวเบรลล์ & ส่งออก",
])

# ---------------------------------------------------------------------------
# TAB 1 — Ingestion & Document Upload
# ---------------------------------------------------------------------------
with tab1:
    st.subheader("อัปโหลดเอกสารต้นฉบับ")
    st.caption("รองรับไฟล์ PDF (สแกน/หลายคอลัมน์) หรือรูปภาพ (PNG, JPG) คุณภาพต่ำ")

    col_up, col_opt = st.columns([2, 1])
    with col_up:
        uploaded = st.file_uploader(
            "เลือกไฟล์เอกสาร", type=["pdf", "png", "jpg", "jpeg"], accept_multiple_files=False
        )
    with col_opt:
        preprocess = st.checkbox(
            "ทำ Image Pre-processing ก่อน OCR (ลด noise ของภาพสแกน)", value=True
        )
        run_btn = st.button("เริ่มนำเข้าและสกัดข้อมูล (Run Ingestion)", type="primary", use_container_width=True)

    if run_btn:
        if uploaded is None:
            st.warning("กรุณาอัปโหลดไฟล์ก่อน")
        else:
            with st.spinner("กำลังจัดคิวงาน แปลงหน้าเอกสาร และสกัดข้อความ + พิกัด (OCR)..."):
                t0 = time.time()
                file_bytes = uploaded.getvalue()
                result = ingestion.ingest(file_bytes, uploaded.name, preprocess=preprocess)
                elapsed = time.time() - t0
            st.session_state.ingest_result = result
            st.session_state.quality_report = None
            st.session_state.pipeline_out = None
            st.session_state.brf_bundle = None
            st.success(f"นำเข้าเอกสารสำเร็จใน {elapsed:.1f} วินาที")

    result = st.session_state.ingest_result
    if result is not None:
        st.divider()
        st.markdown("#### Metadata ของงาน (Job Metadata)")
        m1, m2, m3, m4 = st.columns(4)
        m1.metric("ชื่อไฟล์", result.filename)
        m2.metric("จำนวนหน้า", result.n_pages)
        m3.metric("จำนวน Token ที่สกัดได้", result.n_tokens)
        m4.metric("นำเข้าเมื่อ", datetime.now().strftime("%H:%M:%S"))

        st.markdown("#### พรีวิวหน้าเอกสาร + พิกัด Bounding Box ดิบ")
        page_idx = st.selectbox(
            "เลือกหน้าที่ต้องการดู", options=list(range(1, result.n_pages + 1)), key="p1_page"
        )
        page_img = result.pages[page_idx - 1]
        tokens_this_page = [t for t in result.tokens if t.page == page_idx]

        preview = page_img.copy()
        draw = ImageDraw.Draw(preview)
        for t in tokens_this_page:
            draw.rectangle([t.left, t.top, t.right, t.bottom], outline="#2B3A67", width=2)
        st.image(preview, use_container_width=True, caption=f"หน้า {page_idx}: พบ {len(tokens_this_page)} token (กรอบสีน้ำเงิน = bounding box จาก OCR)")
    else:
        st.info("ยังไม่มีเอกสารถูกนำเข้า — อัปโหลดไฟล์แล้วกด 'เริ่มนำเข้าและสกัดข้อมูล' ด้านบน")

# ---------------------------------------------------------------------------
# TAB 2 — Data Quality Assessment & Pipeline Execution
# ---------------------------------------------------------------------------
with tab2:
    result = st.session_state.ingest_result
    if result is None:
        st.info("กรุณานำเข้าเอกสารในแท็บ ① ก่อน")
    else:
        if st.session_state.quality_report is None:
            st.session_state.quality_report = quality.profile(result.tokens)
        qr = st.session_state.quality_report

        st.subheader("Automated Data Quality Profiling")
        cols = st.columns(4)
        summary = qr.to_summary_dict()
        keys = list(summary.keys())
        for i, k in enumerate(keys):
            with cols[i % 4]:
                st.markdown(
                    f'<div class="bf-metric-box"><div style="font-size:0.8rem;color:#6b6b6b">{k}</div>'
                    f'<div style="font-size:1.5rem;font-weight:700;color:#2B3A67">{summary[k]}</div></div>',
                    unsafe_allow_html=True,
                )
            if i % 4 == 3:
                st.write("")

        st.divider()
        st.markdown("#### Interactive Inspection — ภาพต้นฉบับพร้อมจุดที่ตรวจพบปัญหา")
        legend = " ".join(
            f'<span class="bf-pill" style="background:{c}22;color:{c}">{name}</span>'
            for name, c in [
                ("สระ/วรรณยุกต์ลอย", FLAG_COLORS["floating_mark"]),
                ("นอกพจนานุกรม (OOV)", FLAG_COLORS["oov"]),
                ("สัญลักษณ์ขยะ", FLAG_COLORS["garbage"]),
                ("Confidence ต่ำ", FLAG_COLORS["low_conf"]),
            ]
        )
        st.markdown(legend, unsafe_allow_html=True)

        page_idx2 = st.selectbox(
            "เลือกหน้าที่ต้องการตรวจสอบ", options=list(range(1, result.n_pages + 1)), key="p2_page"
        )
        flags_this_page = [f for f in qr.flags if f.token.page == page_idx2]
        overlay_img = draw_flagged_overlay(result.pages[page_idx2 - 1], flags_this_page)
        st.image(overlay_img, use_container_width=True)

        st.divider()
        st.markdown("#### รันกระบวนการ Data Engineering Pipeline")
        st.caption(
            "ขั้นตอน: (1) Data Cleaning ซ่อมแซมข้อความ → (2) XY-Cut จัดลำดับการอ่านตามคอลัมน์ "
            "+ กรอง Header/Footer → (3) แปลงเป็นโครงสร้าง Hierarchical JSON พร้อม Data Lineage"
        )
        if st.button("▶ Run Pipeline", type="primary"):
            with st.spinner("กำลังประมวลผล pipeline..."):
                page_widths = {i + 1: img.width for i, img in enumerate(result.pages)}
                page_heights = {i + 1: img.height for i, img in enumerate(result.pages)}
                cleaned, ordered, tree = pipeline.run_pipeline(
                    result.tokens, page_widths, page_heights, result.filename
                )
            st.session_state.pipeline_out = (cleaned, ordered, tree)
            st.session_state.brf_bundle = None
            st.success("รัน pipeline สำเร็จ")

        if st.session_state.pipeline_out is not None:
            cleaned, ordered, tree = st.session_state.pipeline_out
            n_modified = sum(1 for c in cleaned if c.was_modified)
            n_dropped = sum(1 for c in cleaned if c.dropped)
            n_hf = sum(1 for o in ordered if o.is_header_footer)
            n_sections = len(tree.sections)
            n_paras = sum(len(s.paragraphs) for s in tree.sections)

            c1, c2, c3, c4, c5 = st.columns(5)
            c1.metric("คำที่ถูกซ่อมแซม", n_modified)
            c2.metric("คำขยะที่ถูกตัดทิ้ง", n_dropped)
            c3.metric("Token header/footer ที่กรองออก", n_hf)
            c4.metric("จำนวน Section", n_sections)
            c5.metric("จำนวน Paragraph", n_paras)

            st.markdown("##### Pipeline Reproducibility Check (Idempotency)")
            st.caption(
                "รัน pipeline ซ้ำด้วย input เดิม ควรได้ content hash เดิมเสมอ — ใช้ยืนยันว่ากระบวนการ "
                "reproducible และสามารถสอบทานย้อนกลับ (Data Lineage) ได้"
            )
            st.code(f"content_hash = {tree.content_hash}", language="text")

            st.divider()
            st.markdown("#### Before vs After — เปรียบเทียบข้อความ")
            colb, cola = st.columns(2)
            with colb:
                st.markdown("**Before** (ข้อความดิบจาก OCR เรียงตามลำดับที่อ่านเจอ)")
                st.text_area("before", all_original_text(result), height=280, label_visibility="collapsed")
            with cola:
                st.markdown("**After** (ผ่านการทำความสะอาด + จัดลำดับการอ่าน + ตัด header/footer)")
                st.text_area("after", all_pipeline_text(tree), height=280, label_visibility="collapsed")

            st.markdown("#### โครงสร้าง Document Tree (Hierarchical JSON) พร้อม Data Lineage")
            with st.expander("ดู JSON โครงสร้างเอกสารแบบเต็ม"):
                st.json(tree.to_dict())

# ---------------------------------------------------------------------------
# TAB 3 — Braille Layout Preview & Export
# ---------------------------------------------------------------------------
with tab3:
    pipeline_out = st.session_state.pipeline_out
    if pipeline_out is None:
        st.info("กรุณารัน Pipeline ในแท็บ ② ก่อน จึงจะสามารถแปลงเป็นอักษรเบรลล์ได้")
    else:
        cleaned, ordered, tree = pipeline_out
        st.subheader("Braille Layout Preview & Export")

        with st.expander("⚠️ หมายเหตุเกี่ยวกับความถูกต้องของการแปลงเบรลล์", expanded=False):
            st.write(
                "การแปลงในหน้านี้ใช้ตารางจุดเบรลล์ไทยมาตรฐาน (พยัญชนะ สระหลัก วรรณยุกต์ ตัวเลข) "
                "แบบตัวอักษรต่อตัวอักษร (character-by-character) เพื่อสาธิตแนวทาง text → dot pattern → BRF "
                "ยังไม่ครอบคลุมกฎสระประสม/อักษรควบที่ซับซ้อนทุกกรณี และยังไม่ผ่านการรับรองมาตรฐานสำหรับ"
                "ผลิตสื่อจริง ตามที่ระบุไว้ในหัวข้อ 5 ของเอกสารโครงงานว่าการคำนวณจัดหน้าเบรลล์ที่สมบูรณ์"
                "(ตัดคำ/จัดหน้า 2 มิติ) จะอยู่ในโมดูล AI/CSP ที่ต่อยอดในอนาคต"
            )

        cells_per_line = st.slider("จำนวนเซลล์ต่อบรรทัด (ขนาดกระดาษเบรลล์)", 20, 42, 40)
        lines_per_page = st.slider("จำนวนบรรทัดต่อหน้า", 15, 30, 25)

        if st.button("🔤 แปลงเป็นอักษรเบรลล์ (Convert to Braille)", type="primary"):
            paragraphs = [p.text for s in tree.sections for p in s.paragraphs]
            with st.spinner("กำลังแปลงข้อความเป็นจุดเบรลล์และประกอบไฟล์ .brf..."):
                brf_content, braille_result = braille.build_brf(
                    paragraphs, cells_per_line=cells_per_line, lines_per_page=lines_per_page
                )
            st.session_state.brf_bundle = (brf_content, braille_result)

        bundle = st.session_state.brf_bundle
        if bundle is not None:
            brf_content, braille_result = bundle
            c1, c2, c3 = st.columns(3)
            c1.metric("จำนวนเซลล์เบรลล์ทั้งหมด", len(braille_result.cells))
            c2.metric("ความครอบคลุมการแปลง (Coverage)", f"{braille_result.coverage_ratio*100:.1f}%")
            c3.metric("อักขระที่แปลงไม่ได้ (Unmapped)", len(braille_result.unmapped_chars))

            if braille_result.unmapped_chars:
                uniq = sorted(set(braille_result.unmapped_chars))
                st.warning(f"พบอักขระที่ยังไม่มีในตารางเบรลล์ {len(uniq)} ชนิด: {' '.join(uniq[:40])}")

            st.markdown("#### Tactile Grid Preview (จำลองหน้าสัมผัส)")
            st.caption("แสดงตัวอย่าง 60 เซลล์แรก — จุดสีน้ำเงินทึบ = จุดนูน (raised dot)")
            svg = render_tactile_svg(braille_result.cells, max_cells=60, cells_per_row=15)
            st.markdown(svg, unsafe_allow_html=True)

            st.markdown("#### เนื้อหา .brf (Braille ASCII)")
            st.text_area("brf_preview", brf_content, height=200, label_visibility="collapsed")

            st.divider()
            st.markdown("#### ดาวน์โหลดผลลัพธ์")
            dl1, dl2 = st.columns(2)
            with dl1:
                st.download_button(
                    "⬇ ดาวน์โหลด .brf",
                    data=brf_content.encode("utf-8"),
                    file_name=f"{tree.filename.rsplit('.',1)[0]}.brf",
                    mime="text/plain",
                    use_container_width=True,
                )
            with dl2:
                json_bytes = json.dumps(tree.to_dict(), ensure_ascii=False, indent=2).encode("utf-8")
                st.download_button(
                    "⬇ ดาวน์โหลด .json (Document Tree)",
                    data=json_bytes,
                    file_name=f"{tree.filename.rsplit('.',1)[0]}.json",
                    mime="application/json",
                    use_container_width=True,
                )

st.divider()
st.caption(
    "BrailleFlow — โครงงาน Data Engineering: แปลงเอกสารคุณภาพต่ำให้เป็นข้อมูลโครงสร้างสูง "
    "พร้อมสำหรับผลิตสื่อการเรียนรู้อักษรเบรลล์"
)
