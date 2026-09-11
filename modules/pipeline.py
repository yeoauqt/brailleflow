"""
pipeline.py
-----------
แกนกลางด้าน Data Engineering ของ BrailleFlow แบ่งเป็น 3 ขั้นตอนหลัก
ที่ทำงานต่อเนื่องกันแบบ Modular Pipeline Steps (เรียกซ้ำได้ผลลัพธ์เดิมเสมอ = Idempotent):

  Step 1: clean_tokens()         -> ทำความสะอาดข้อความระดับคำ (regex/heuristic repair)
  Step 2: reorder_reading_flow() -> XY-Cut แยกคอลัมน์ + จัดลำดับการอ่านใหม่ + กรอง header/footer
  Step 3: build_document_tree()  -> แปลงข้อมูลแบนราบ -> โครงสร้างลำดับชั้น (Hierarchical JSON)
                                     พร้อม Data Lineage ของทุกย่อหน้า

ทุกฟังก์ชันเป็น pure function (input เดิม -> output เดิมเสมอ) ตามหลัก
Pipeline Reproducibility ที่ระบุไว้ในเอกสารโครงงาน (ข้อ 4.3)
"""

from __future__ import annotations
import hashlib
import regex
from dataclasses import dataclass, field
from typing import List, Dict, Optional, Tuple

from .ingestion import WordToken

# ---------------------------------------------------------------------------
# STEP 1: Automated Data Cleaning
# ---------------------------------------------------------------------------

# แก้ไข OCR noise ที่พบบ่อยในภาษาไทย: สัญลักษณ์แปลกปลอมปนในคำ, ช่องว่างเกิน,
# สระ/วรรณยุกต์ลอยเดี่ยว ๆ ที่ไม่มีพยัญชนะนำหน้าเลย (ตัดทิ้งเพราะกู้คืนไม่ได้อัตโนมัติ)
_JUNK_SYMBOLS_RE = regex.compile(r"[|~`^_={}\[\]<>\\]")
_MULTI_SPACE_RE = regex.compile(r"\s{2,}")
_LEADING_ORPHAN_MARK_RE = regex.compile(r"^[ัิีึืุู่้๊๋็์ํ๎]+")


def clean_text(raw: str) -> Tuple[str, bool]:
    """ทำความสะอาดข้อความดิบหนึ่ง token
    คืนค่า (ข้อความที่ซ่อมแซมแล้ว, ถูกแก้ไขหรือไม่)
    """
    text = raw
    text = _JUNK_SYMBOLS_RE.sub("", text)
    text = _LEADING_ORPHAN_MARK_RE.sub("", text)  # ตัดสระ/วรรณยุกต์ลอยหน้าคำที่กู้คืนไม่ได้
    text = _MULTI_SPACE_RE.sub(" ", text).strip()
    changed = text != raw
    return text, changed


@dataclass
class CleanedToken:
    token: WordToken
    original_text: str
    cleaned_text: str
    was_modified: bool
    dropped: bool  # True ถ้าหลังทำความสะอาดแล้วไม่เหลือข้อความ (เป็นขยะล้วน)


def clean_tokens(tokens: List[WordToken]) -> List[CleanedToken]:
    """STEP 1: รันการทำความสะอาดกับทุก token"""
    out: List[CleanedToken] = []
    for t in tokens:
        cleaned, changed = clean_text(t.text)
        out.append(
            CleanedToken(
                token=t,
                original_text=t.text,
                cleaned_text=cleaned,
                was_modified=changed,
                dropped=(len(cleaned) == 0),
            )
        )
    return out


# ---------------------------------------------------------------------------
# STEP 2: Spatial Ordering (simplified XY-Cut) + Header/Footer Filtering
# ---------------------------------------------------------------------------

@dataclass
class OrderedToken:
    cleaned: CleanedToken
    column_index: int
    reading_order: int
    is_header_footer: bool = False


def _detect_column_bands(
    tokens: List[CleanedToken], page_width: int, min_gap_px: int
) -> List[Tuple[int, int]]:
    """XY-Cut (แกน X): หาช่องว่างแนวตั้งที่ไม่มี token ใดพาดผ่านเลย ถ้าช่องว่างนั้น
    กว้างกว่า min_gap_px ให้ตัดเป็นคอลัมน์ใหม่ -> คืนค่ารายการช่วง (left, right) ของแต่ละคอลัมน์
    """
    if not tokens:
        return [(0, page_width)]

    # projection profile แนวนอน: ช่วง x ที่ "มี" token ครอบคลุมอยู่ -> True
    covered = [False] * (page_width + 1)
    for ct in tokens:
        t = ct.token
        l = max(0, min(t.left, page_width))
        r = max(0, min(t.right, page_width))
        for x in range(l, r + 1):
            covered[x] = True

    # หาช่วงว่างต่อเนื่อง (gap) ที่กว้างกว่า min_gap_px
    gaps: List[Tuple[int, int]] = []
    gap_start: Optional[int] = None
    for x in range(page_width + 1):
        if not covered[x]:
            if gap_start is None:
                gap_start = x
        else:
            if gap_start is not None:
                if x - gap_start >= min_gap_px:
                    gaps.append((gap_start, x))
                gap_start = None
    if gap_start is not None and (page_width + 1 - gap_start) >= min_gap_px:
        gaps.append((gap_start, page_width))

    if not gaps:
        return [(0, page_width)]

    columns: List[Tuple[int, int]] = []
    prev_end = 0
    for gs, ge in gaps:
        if gs > prev_end:
            columns.append((prev_end, gs))
        prev_end = ge
    if prev_end < page_width:
        columns.append((prev_end, page_width))
    return columns if columns else [(0, page_width)]


def _line_group_key(ct: CleanedToken, line_tol_px: int = 8) -> int:
    """จัดกลุ่มบรรทัดจากพิกัด top โดยปัดตามช่วง tolerance (กรณี tesseract แบ่ง line_num พลาด)"""
    return round(ct.token.top / line_tol_px)


def reorder_reading_flow(
    cleaned_tokens: List[CleanedToken],
    page_widths: Dict[int, int],
    page_heights: Dict[int, int],
    header_band_ratio: float = 0.08,
    footer_band_ratio: float = 0.08,
    min_gap_ratio: float = 0.035,
) -> List[OrderedToken]:
    """STEP 2: แยก token ตามหน้า -> ตัดคอลัมน์ด้วย XY-Cut -> เรียงลำดับการอ่าน
    (คอลัมน์ซ้ายไปขวา, บนลงล่างในแต่ละคอลัมน์) -> ตีตรา header/footer band
    """
    # เตรียม lookup ข้อความที่ปรากฏในแถบ header/footer เพื่อดูว่าเป็น "ข้อความซ้ำข้ามหน้า" หรือไม่
    band_text_page_count: Dict[str, set] = {}
    for ct in cleaned_tokens:
        if ct.dropped:
            continue
        h = page_heights.get(ct.token.page, 1)
        in_header = ct.token.top <= header_band_ratio * h
        in_footer = ct.token.bottom >= (1 - footer_band_ratio) * h
        if in_header or in_footer:
            key = ct.cleaned_text
            band_text_page_count.setdefault(key, set()).add(ct.token.page)

    pages = sorted(set(ct.token.page for ct in cleaned_tokens))
    result: List[OrderedToken] = []

    for page in pages:
        page_tokens = [ct for ct in cleaned_tokens if ct.token.page == page and not ct.dropped]
        pw = page_widths.get(page, max((ct.token.right for ct in page_tokens), default=1000))
        ph = page_heights.get(page, max((ct.token.bottom for ct in page_tokens), default=1000))
        min_gap_px = max(15, int(pw * min_gap_ratio))

        columns = _detect_column_bands(page_tokens, pw, min_gap_px)

        order_counter = 0
        for col_idx, (col_left, col_right) in enumerate(columns):
            col_tokens = [
                ct for ct in page_tokens
                if col_left <= (ct.token.left + ct.token.width / 2) < col_right
            ]
            # เรียงภายในคอลัมน์: บรรทัดบนลงล่าง แล้วซ้ายไปขวาในบรรทัดเดียวกัน
            col_tokens.sort(key=lambda ct: (_line_group_key(ct), ct.token.left))

            for ct in col_tokens:
                h = ph
                in_header = ct.token.top <= header_band_ratio * h
                in_footer = ct.token.bottom >= (1 - footer_band_ratio) * h
                repeats_across_pages = len(band_text_page_count.get(ct.cleaned_text, set())) >= 2
                is_hf = (in_header or in_footer) and (repeats_across_pages or len(ct.cleaned_text) <= 3)

                result.append(
                    OrderedToken(
                        cleaned=ct,
                        column_index=col_idx,
                        reading_order=order_counter,
                        is_header_footer=is_hf,
                    )
                )
                order_counter += 1

    return result


# ---------------------------------------------------------------------------
# STEP 3: Hierarchical Schema Transformation + Data Lineage
# ---------------------------------------------------------------------------

@dataclass
class LineageRecord:
    page: int
    bbox: Tuple[int, int, int, int]  # left, top, width, height
    original_text: str
    cleaned_text: str
    column_index: int


@dataclass
class Paragraph:
    text: str
    lineage: List[LineageRecord] = field(default_factory=list)

    def to_dict(self) -> dict:
        return {
            "type": "paragraph",
            "text": self.text,
            "lineage": [
                {
                    "page": r.page,
                    "bbox": {"left": r.bbox[0], "top": r.bbox[1], "width": r.bbox[2], "height": r.bbox[3]},
                    "original_text": r.original_text,
                    "cleaned_text": r.cleaned_text,
                    "column_index": r.column_index,
                }
                for r in self.lineage
            ],
        }


@dataclass
class Section:
    title: Optional[str]
    paragraphs: List[Paragraph] = field(default_factory=list)

    def to_dict(self) -> dict:
        return {
            "type": "section",
            "title": self.title,
            "paragraphs": [p.to_dict() for p in self.paragraphs],
        }


@dataclass
class DocumentTree:
    filename: str
    sections: List[Section] = field(default_factory=list)
    content_hash: str = ""

    def to_dict(self) -> dict:
        return {
            "type": "document",
            "filename": self.filename,
            "content_hash": self.content_hash,
            "sections": [s.to_dict() for s in self.sections],
        }


def _median(values: List[int]) -> float:
    if not values:
        return 0.0
    s = sorted(values)
    n = len(s)
    mid = n // 2
    return (s[mid] if n % 2 else (s[mid - 1] + s[mid]) / 2)


def build_document_tree(
    ordered_tokens: List[OrderedToken],
    filename: str,
    line_gap_factor: float = 1.6,
    heading_height_factor: float = 1.35,
) -> DocumentTree:
    """STEP 3: จาก token ที่เรียงลำดับการอ่านแล้ว -> รวมเป็นบรรทัด -> ย่อหน้า -> section
    กฎที่ใช้ (heuristic ง่าย ๆ สำหรับสาธิต):
      - บรรทัดใหม่: line-group เปลี่ยน (ภายในคอลัมน์เดียวกัน)
      - ย่อหน้าใหม่: ช่องว่างแนวตั้งระหว่างบรรทัด > เส้นค่ามัธยฐานความสูงตัวอักษร * line_gap_factor
      - section ใหม่: ความสูงตัวอักษรของบรรทัดนั้นมากกว่าค่ามัธยฐานทั้งเอกสาร * heading_height_factor
        (สันนิษฐานว่าเป็นหัวข้อ/หัวเรื่อง) และคอลัมน์แรกของหน้า
    """
    kept = [ot for ot in ordered_tokens if not ot.is_header_footer]
    kept.sort(key=lambda ot: (ot.cleaned.token.page, ot.column_index, ot.reading_order))

    heights = [ot.cleaned.token.height for ot in kept]
    median_h = _median(heights) or 12.0

    doc = DocumentTree(filename=filename)
    current_section = Section(title=None)
    doc.sections.append(current_section)

    current_para: Optional[Paragraph] = None
    prev_bottom: Optional[int] = None
    prev_right: Optional[int] = None
    prev_page_col = None
    prev_line_key = None

    all_cleaned_text_for_hash: List[str] = []

    for ot in kept:
        ct = ot.cleaned
        t = ct.token
        page_col = (t.page, ot.column_index)
        line_key = _line_group_key(ct)

        is_heading = t.height > median_h * heading_height_factor

        new_line = (page_col != prev_page_col) or (line_key != prev_line_key)
        big_gap = (
            prev_bottom is not None
            and page_col == prev_page_col
            and (t.top - prev_bottom) > median_h * line_gap_factor
        )

        start_new_paragraph = current_para is None or new_line and (big_gap or is_heading)
        start_new_section = is_heading and (current_para is not None) and big_gap

        if start_new_section:
            current_section = Section(title=ct.cleaned_text)
            doc.sections.append(current_section)
            current_para = Paragraph(text="")
            current_section.paragraphs.append(current_para)
            prev_right = None
        elif start_new_paragraph:
            current_para = Paragraph(text="")
            current_section.paragraphs.append(current_para)
            prev_right = None

        # ต่อข้อความเข้าย่อหน้าปัจจุบัน
        # หมายเหตุ: ภาษาไทยไม่ใช้ "ช่องว่าง" คั่นระหว่างคำในประโยคปกติ (ต่างจากภาษาอังกฤษ)
        # ดังนั้นจะเว้นวรรคเฉพาะเมื่อพบช่องว่างแนวนอนจริงระหว่าง token บนบรรทัดเดียวกัน
        # (สัญญาณของการเว้นวรรคจริงในต้นฉบับ เช่น ระหว่างประโยค/อนุประโยค)
        # ส่วนการตัดขึ้นบรรทัดใหม่ภายในย่อหน้าเดียวกันจะไม่แทรกช่องว่าง
        if current_para.text == "":
            sep = ""
        elif (not new_line) and prev_right is not None:
            gap = t.left - prev_right
            sep = " " if gap > median_h * 0.35 else ""
        else:
            sep = ""
        current_para.text = (current_para.text + sep + ct.cleaned_text).strip()
        current_para.lineage.append(
            LineageRecord(
                page=t.page,
                bbox=(t.left, t.top, t.width, t.height),
                original_text=ct.original_text,
                cleaned_text=ct.cleaned_text,
                column_index=ot.column_index,
            )
        )
        all_cleaned_text_for_hash.append(ct.cleaned_text)

        prev_bottom = t.bottom
        prev_right = t.right
        prev_page_col = page_col
        prev_line_key = line_key

    # ลบ section/paragraph ที่ว่างเปล่า (เผื่อกรณี edge case)
    doc.sections = [s for s in doc.sections if any(p.text for p in s.paragraphs)]
    for s in doc.sections:
        s.paragraphs = [p for p in s.paragraphs if p.text]

    # Content hash สำหรับตรวจสอบ Idempotency: input เดียวกันต้องได้ hash เดียวกันเสมอ
    doc.content_hash = hashlib.sha256("".join(all_cleaned_text_for_hash).encode("utf-8")).hexdigest()[:16]

    return doc


def run_pipeline(
    tokens: List[WordToken],
    page_widths: Dict[int, int],
    page_heights: Dict[int, int],
    filename: str,
) -> Tuple[List[CleanedToken], List[OrderedToken], DocumentTree]:
    """รันทั้ง 3 ขั้นตอนต่อเนื่องกัน (Modular Pipeline) คืนค่าผลลัพธ์ของทุกขั้นตอน
    เพื่อให้ UI แสดงผล before/after ของแต่ละ step ได้
    """
    cleaned = clean_tokens(tokens)
    ordered = reorder_reading_flow(cleaned, page_widths, page_heights)
    tree = build_document_tree(ordered, filename)
    return cleaned, ordered, tree
