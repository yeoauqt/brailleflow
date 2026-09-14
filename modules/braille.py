"""
braille.py
----------
แปลงข้อความไทยที่ผ่านการทำความสะอาด+จัดโครงสร้างแล้ว (จาก pipeline.py)
ให้เป็นอักษรเบรลล์ไทย พร้อมส่งออกไฟล์ .brf (Braille Ready Format) และ
ข้อมูลสำหรับพรีวิวหน้าสัมผัสจำลอง (Tactile Grid Preview)

*** หมายเหตุสำคัญเกี่ยวกับความถูกต้อง (Accuracy Disclaimer) ***
ตารางจุดเบรลล์ (dot pattern) ด้านล่างอ้างอิงจากมาตรฐานอักษรเบรลล์ไทย (Thai Braille,
พัฒนาโดย Genevieve Caulfield) ครอบคลุมพยัญชนะ สระหลัก วรรณยุกต์ และตัวเลขที่ใช้บ่อย
แต่ยัง "ไม่ครบทุกกรณี" ของสระประสม/อักขระพิเศษที่ซับซ้อน (เช่น สระเสียงสั้นบางรูป
ที่ต้องสะกดแยกตามบริบท) โมดูลนี้จึงเป็นการสาธิตแนวทางทางวิศวกรรมข้อมูล
(text -> dot pattern -> BRF) ไม่ใช่โปรแกรมแปลอักษรเบรลล์ไทยที่ผ่านการรับรองมาตรฐาน
สำหรับใช้งานจริงควรตรวจทานกับผู้เชี่ยวชาญด้านเบรลล์ไทยหรือใช้ตารางกฎแบบเต็ม
(เช่น Liblouis th-g1) ก่อนนำไปผลิตสื่อจริง — ตรงตามที่ระบุไว้ในหัวข้อ 5 ของเอกสาร
โครงงานว่าการคำนวณจัดหน้าเบรลล์ที่สมบูรณ์จะอยู่ในโมดูล CSP ที่ต่อยอดในอนาคต
"""

from __future__ import annotations
import regex
from dataclasses import dataclass
from typing import List, Tuple, Optional

# ---------------------------------------------------------------------------
# ตารางจุดเบรลล์ไทย: อักขระไทย -> รายการ "เซลล์" โดยแต่ละเซลล์คือ tuple ของหมายเลขจุด (1-6)
# อ้างอิงมาตรฐาน Thai Braille (พยัญชนะยึดรูปแบบสากล, สระยืมจาก Japanese Braille)
# ---------------------------------------------------------------------------
DOT_TABLE = {
    # พยัญชนะ
    "ก": [(1, 2, 4, 5)], "ข": [(1, 3)], "ค": [(1, 3, 6)],
    "ฆ": [(6,), (1, 3, 6)], "ง": [(1, 2, 4, 5, 6)],
    "จ": [(2, 4, 5)], "ฉ": [(3, 4)], "ช": [(3, 4, 6)],
    "ซ": [(2, 3, 4, 6)], "ฌ": [(6,), (3, 4, 6)], "ญ": [(6,), (1, 3, 4, 5, 6)],
    "ฎ": [(6,), (1, 4, 5)], "ฏ": [(6,), (1, 2, 5, 6)],
    "ฐ": [(6,), (2, 3, 4, 5)], "ฑ": [(6,), (2, 3, 4, 5, 6)],
    "ฒ": [(3, 6), (2, 3, 4, 5, 6)], "ณ": [(6,), (1, 3, 4, 5)],
    "ด": [(1, 4, 5)], "ต": [(1, 2, 5, 6)], "ถ": [(2, 3, 4, 5)],
    "ท": [(2, 3, 4, 5, 6)], "ธ": [(3, 5, 6), (2, 3, 4, 5, 6)], "น": [(1, 3, 4, 5)],
    "บ": [(1, 2, 3, 6)], "ป": [(1, 2, 3, 4, 6)], "ผ": [(1, 2, 3, 4)],
    "ฝ": [(1, 3, 4, 6)], "พ": [(1, 4, 5, 6)], "ฟ": [(1, 2, 4, 6)],
    "ภ": [(6,), (1, 4, 5, 6)], "ม": [(1, 3, 4)],
    "ย": [(1, 3, 4, 5, 6)], "ร": [(1, 2, 3, 5)], "ล": [(1, 2, 3)],
    "ว": [(2, 4, 5, 6)], "ศ": [(6,), (2, 3, 4)], "ษ": [(3, 6), (2, 3, 4)],
    "ส": [(2, 3, 4)], "ห": [(1, 2, 5)], "ฬ": [(6,), (1, 2, 3)],
    "อ": [(1, 3, 5)], "ฮ": [(1, 2, 3, 4, 5, 6)],
    # ฤ/ฦ (rue/lue) ที่หายไปจากตารางเดิม -> ประกอบจาก ร/ล + จุดพิเศษ (2,) ตามมาตรฐาน
    # หมายเหตุ: แปลงทีละอักขระ Unicode ดังนั้น "ฤๅ"/"ฦๅ" ไม่ต้องใส่เป็น key รวม
    # เพราะ ฤ + ๅ (lakkhangyao) จะถูกแปลงแยกแล้วต่อกันเป็น 3 เซลล์ถูกต้องเองอยู่แล้ว
    "ฤ": [(1, 2, 3, 5), (2,)], "ฦ": [(1, 2, 3), (2,)],
    "ๅ": [(1, 6)],  # lakkhangyao (ตัวยาว) ที่ตามหลัง ฤ/ฦ

    # สระ (แสดงเป็นรูปเดี่ยว ต่อท้ายพยัญชนะตามลำดับการอ่าน)
    "ะ": [(1,)], "ั": [(3, 4, 5)],  # sara a เปิดพยางค์ = (1,) / ไม้หันอากาศปิดพยางค์ = (3,4,5) คนละจุดกัน
    "ิ": [(1, 2)], "ึ": [(2, 4, 6)], "ุ": [(1, 4)],
    "า": [(1, 6)], "ี": [(2, 3)], "ื": [(2, 6)], "ู": [(2, 5)],
    "เ": [(1, 2, 4)], "แ": [(1, 2, 6)], "โ": [(2, 4)], "อ_o": [(1, 3, 5)],
    "ำ": [(1, 3, 5, 6)], "ไ": [(1, 5, 6)], "ใ": [(1, 5, 6), (2,)],

    # วรรณยุกต์
    "่": [(3, 5)], "้": [(2, 5, 6)], "๊": [(2, 3, 5, 6)], "๋": [(2, 3, 6)],

    # เครื่องหมายอื่น ๆ
    "์": [(3, 5, 6)], "ๆ": [(2,)], "ฯ": [(5, 6), (2, 3)],
    " ": [()],  # เว้นวรรค = ไม่มีจุด

    # ตัวเลขไทย (ต้องมี prefix ตัวเลขนำหน้าเสมอ ดู NUM_PREFIX)
    "๑": [(1,)], "๒": [(1, 2)], "๓": [(1, 4)], "๔": [(1, 4, 5)], "๕": [(1, 5)],
    "๖": [(1, 2, 4)], "๗": [(1, 2, 4, 5)], "๘": [(1, 2, 5)], "๙": [(2, 4)], "๐": [(2, 4, 5)],
    # เลขอารบิกก็ใช้จุดเดียวกับเลขไทย
    "1": [(1,)], "2": [(1, 2)], "3": [(1, 4)], "4": [(1, 4, 5)], "5": [(1, 5)],
    "6": [(1, 2, 4)], "7": [(1, 2, 4, 5)], "8": [(1, 2, 5)], "9": [(2, 4)], "0": [(2, 4, 5)],

    # เครื่องหมายวรรคตอนสากลที่ใช้ตรงกับอังกฤษ
    ",": [(2,)], ".": [(4, 5, 6), (2, 5, 6)], "?": [(4, 5, 6), (2, 3, 6)],
    "!": [(4, 5, 6), (2, 3, 5)], "-": [(3, 6)],
}
NUM_PREFIX_THAI = [(6,), (3, 4, 5, 6)]   # ตัวเลขไทย (๑๒๓...) ต้องมี dot-6 นำหน้าเพื่อบอกว่าเป็นเลขไทย/ลาว
NUM_PREFIX_ARABIC = [(3, 4, 5, 6)]        # ตัวเลขอารบิกใช้แค่เครื่องหมายตัวเลขสากลเฉยๆ ไม่ต้องมี dot-6
THAI_DIGITS = set("๑๒๓๔๕๖๗๘๙๐")
ARABIC_DIGITS = set("1234567890")

# กฎการสลับตำแหน่ง: ในตัวพิมพ์ วรรณยุกต์เขียนหลังสระ ะ/ำ (เช่น "น้ำ" = น+้+ำ)
# แต่ในเบรลล์ไทย สระ ะ และ ำ ต้องมาก่อนวรรณยุกต์เสมอ (สลับกับลำดับตัวพิมพ์)
_TONE_MARK_BEFORE_SARA_RE = regex.compile(r"([่้๊๋])([ะำ])")

# ตาราง Braille ASCII มาตรฐาน (North American Braille ASCII / BRF) 64 ตัวอักษร
# ดัชนี = ผลรวม 2^(หมายเลขจุด-1) ของจุดที่ยกขึ้นในเซลล์นั้น (ตรงกับลำดับบิตของ Unicode Braille Patterns)
_BRAILLE_ASCII_TABLE = (
    " A1B'K2L@CIF/MSP\"E3H9O6R^DJG>NTQ,*5<-U8V.%[$+X!&;:4\\0Z7(_?W]#Y)="
)


def _dots_to_index(dots: Tuple[int, ...]) -> int:
    return sum(1 << (d - 1) for d in dots)


def dots_to_unicode(dots: Tuple[int, ...]) -> str:
    """แปลง tuple จุด -> อักขระ Unicode Braille Patterns (U+2800 ...)"""
    return chr(0x2800 + _dots_to_index(dots))


def dots_to_ascii(dots: Tuple[int, ...]) -> str:
    """แปลง tuple จุด -> อักขระ Braille ASCII มาตรฐาน (ใช้ในไฟล์ .brf)"""
    idx = _dots_to_index(dots)
    return _BRAILLE_ASCII_TABLE[idx]


@dataclass
class BrailleConversionResult:
    cells: List[Tuple[int, ...]]
    unicode_text: str
    ascii_text: str
    unmapped_chars: List[str]
    coverage_ratio: float  # สัดส่วนอักขระที่แปลงได้สำเร็จ


def text_to_braille(text: str) -> BrailleConversionResult:
    """แปลงข้อความไทยหนึ่งสตริงให้เป็นลำดับเซลล์เบรลล์ (รองรับเว้นวรรค ตัวเลข วรรคตอนพื้นฐาน)"""
    # สลับตำแหน่งวรรณยุกต์กับสระ ะ/ำ ก่อนแปลง (กฎเบรลล์ไทย: ะ/ำ ต้องมาก่อนวรรณยุกต์เสมอ
    # ตรงข้ามกับลำดับตัวพิมพ์ปกติ เช่น "น้ำ" ต้องแปลงในลำดับ น -> ำ -> ้ ไม่ใช่ น -> ้ -> ำ)
    text = _TONE_MARK_BEFORE_SARA_RE.sub(lambda m: m.group(2) + m.group(1), text)

    cells: List[Tuple[int, ...]] = []
    unmapped: List[str] = []
    total = 0
    mapped = 0
    prev_was_digit = False

    for ch in text:
        if ch == "\n":
            continue
        total += 1
        is_thai_digit = ch in THAI_DIGITS
        is_digit = is_thai_digit or ch in ARABIC_DIGITS
        if is_digit and not prev_was_digit:
            cells.extend(NUM_PREFIX_THAI if is_thai_digit else NUM_PREFIX_ARABIC)
        prev_was_digit = is_digit

        entry = DOT_TABLE.get(ch)
        if entry is None:
            unmapped.append(ch)
            continue
        mapped += 1
        cells.extend(entry)

    unicode_text = "".join(dots_to_unicode(c) for c in cells)
    ascii_text = "".join(dots_to_ascii(c) for c in cells)
    coverage = (mapped / total) if total else 1.0
    return BrailleConversionResult(
        cells=cells,
        unicode_text=unicode_text,
        ascii_text=ascii_text,
        unmapped_chars=unmapped,
        coverage_ratio=coverage,
    )


def build_brf(
    paragraphs: List[str],
    cells_per_line: int = 40,
    lines_per_page: int = 25,
    source_pages: Optional[List[int]] = None,
) -> Tuple[str, BrailleConversionResult]:
    """ประกอบย่อหน้าทั้งหมด -> ไฟล์ .brf เดียว โดยตัดบรรทัด/แบ่งหน้าตามมาตรฐาน
    (ค่าเริ่มต้น 40 เซลล์/บรรทัด, 25 บรรทัด/หน้า ตามธรรมเนียม BRF ทั่วไป
    ปรับได้ตามขนาดกระดาษเบรลล์ที่ใช้จริง)

    source_pages: รายการ "เลขหน้าต้นฉบับ" ของแต่ละย่อหน้า (ความยาวเท่ากับ paragraphs)
    ถ้าระบุมา -> การขึ้นหน้าเบรลล์ใหม่ (form-feed) จะยึดตามการเปลี่ยนหน้าต้นฉบับจริงเป็นหลัก
    (จะไม่ตัดกลางประโยค/ย่อหน้าเดิมที่ยังอยู่หน้าเดียวกันจากต้นฉบับ) โดยยังคงตัดแบ่ง
    หน้าเบรลล์เพิ่มเติมตาม lines_per_page เป็น safety net เฉพาะกรณีหน้าต้นฉบับเดียว
    ยาวเกินกว่าจะใส่ในแผ่นเบรลล์แผ่นเดียวได้จริง
    ถ้าไม่ระบุ (None) -> ทำงานแบบเดิม คือตัดหน้าเบรลล์ทุก lines_per_page บรรทัดล้วนๆ
    """
    full_text = "\n".join(paragraphs)
    result = text_to_braille(full_text)

    if source_pages is not None and len(source_pages) != len(paragraphs):
        raise ValueError("source_pages ต้องมีความยาวเท่ากับ paragraphs")

    # ตัดบรรทัดจาก ascii_text ตาม cells_per_line โดยเคารพการขึ้นย่อหน้าใหม่ (\n ในต้นฉบับ)
    # พร้อมจดจำว่าแต่ละบรรทัดที่ตัดออกมา มาจากหน้าต้นฉบับหน้าไหน (ถ้ามีข้อมูล)
    lines: List[str] = []
    line_source_page: List[Optional[int]] = []
    for idx, para in enumerate(paragraphs):
        para_result = text_to_braille(para)
        ascii_line = para_result.ascii_text
        page_no = source_pages[idx] if source_pages is not None else None
        if not ascii_line:
            lines.append("")
            line_source_page.append(page_no)
            continue
        for i in range(0, len(ascii_line), cells_per_line):
            lines.append(ascii_line[i : i + cells_per_line])
            line_source_page.append(page_no)

    # แบ่งหน้าเบรลล์: ถ้ามี source_pages ให้ขึ้นหน้าใหม่ตอนหน้าต้นฉบับเปลี่ยนเป็นหลัก
    # (นับจำนวนบรรทัดในหน้าเบรลล์ปัจจุบันแยกต่างหาก เพื่อยังกันไม่ให้หน้าต้นฉบับเดียว
    # ที่ยาวมากล้นแผ่นเบรลล์แผ่นเดียว)
    out_lines: List[str] = []
    lines_in_current_brf_page = 0
    prev_source_page: Optional[int] = None
    for i, line in enumerate(lines):
        cur_source_page = line_source_page[i]
        page_changed = (
            source_pages is not None
            and i > 0
            and cur_source_page != prev_source_page
        )
        overflow = lines_in_current_brf_page >= lines_per_page

        if i > 0 and (page_changed or overflow):
            out_lines.append("\x0c")
            lines_in_current_brf_page = 0

        out_lines.append(line)
        lines_in_current_brf_page += 1
        prev_source_page = cur_source_page

    brf_content = "\n".join(out_lines)
    return brf_content, result


def tactile_grid(cells: List[Tuple[int, ...]]) -> List[List[List[bool]]]:
    """แปลงรายการเซลล์ -> โครงสร้างกริด 2x3 จุด (บน UI ใช้วาด Tactile Preview)
    คืนค่า: list ของเซลล์ แต่ละเซลล์เป็น matrix 3 แถว x 2 คอลัมน์ของ True/False
    ตำแหน่งจุดมาตรฐาน:  1 4
                        2 5
                        3 6
    """
    grids = []
    for dots in cells:
        grid = [[False, False] for _ in range(3)]
        for d in dots:
            row = (d - 1) % 3
            col = 0 if d <= 3 else 1
            grid[row][col] = True
        grids.append(grid)
    return grids
