"""
quality.py
----------
Automated Data Quality Profiling สำหรับข้อความไทยที่ได้จาก OCR

แนวคิด: เอกสารสแกนคุณภาพต่ำทำให้เกิดปัญหาข้อมูลสกปรก (dirty data) เฉพาะทาง
ของภาษาไทยที่ตรวจสอบด้วยกฎ (rule-based) และ dictionary lookup ได้ คือ

  1. Floating vowel / tone-mark error (สระลอย, วรรณยุกต์ลอย)
     สระบน-ล่าง และวรรณยุกต์ไทยเป็น combining character ที่ต้อง "เกาะ" อยู่กับ
     พยัญชนะนำหน้าเสมอ ถ้า OCR ตัดพยัญชนะหาย จะเหลือสระ/วรรณยุกต์ลอยเดี่ยว ๆ
     ซึ่งตรวจจับได้ด้วย regex ระดับอักขระ

  2. Out-Of-Vocabulary (OOV) score
     ตัดคำด้วย pythainlp แล้วเทียบกับพจนานุกรมไทยมาตรฐาน คำที่ไม่พบในดิกชันนารี
     และไม่ใช่ตัวเลข/อังกฤษ มีแนวโน้มสูงว่าเป็นคำที่ OCR อ่านผิด

  3. Low-confidence ratio
     ใช้ค่า confidence ที่ tesseract ให้มาโดยตรง (0-100) เป็นสัญญาณเสริม

  4. Garbage-symbol ratio
     คำที่ประกอบด้วยสัญลักษณ์แปลกปลอมล้วน ๆ (ไม่ใช่ตัวอักษร/ตัวเลขไทย-อังกฤษ)
     มักเป็นเศษ noise จากคราบหมึกหรือรอยขีดในภาพสแกน
"""

from __future__ import annotations
import regex
from dataclasses import dataclass, field
from typing import List, Dict

from .ingestion import WordToken

try:
    from pythainlp import word_tokenize
    from pythainlp.corpus import thai_words
    _THAI_VOCAB = set(thai_words())
    _PYTHAINLP_OK = True
except Exception:
    _THAI_VOCAB = set()
    _PYTHAINLP_OK = False

# กลุ่มอักขระไทย (Unicode block) แบ่งตามหน้าที่ทางภาษาศาสตร์
THAI_CONSONANTS = set("กขฃคฅฆงจฉชซฌญฎฏฐฑฒณดตถทธนบปผฝพฟภมยรลวศษสหฬอฮ")
THAI_LEADING_VOWELS = set("เแโใไ")  # สระหน้า วางก่อนพยัญชนะได้ตามปกติ
THAI_COMBINING_MARKS = set("ัิีึืุู่้๊๋็์ํ๎")  # สระบน/ล่าง + วรรณยุกต์ + ทัณฑฆาต ฯลฯ ต้องเกาะพยัญชนะ
THAI_DIGITS = set("๐๑๒๓๔๕๖๗๘๙")

_FLOATING_MARK_RE = regex.compile(
    r"(?:^|[^ก-ฮ])([ัิีึืุู่้๊๋็์ํ๎])"
)  # combining mark ที่ไม่มีพยัญชนะไทยอยู่ก่อนหน้าทันที
_GARBAGE_RE = regex.compile(r"^[^\p{L}\p{N}]+$")  # token ที่ไม่มีตัวอักษร/ตัวเลขเลย


def is_floating_mark_error(text: str) -> bool:
    """True ถ้าพบสระ/วรรณยุกต์ลอยอย่างน้อยหนึ่งจุดใน token นี้"""
    return bool(_FLOATING_MARK_RE.search(text))


def is_garbage_token(text: str) -> bool:
    """True ถ้า token ประกอบด้วยสัญลักษณ์ล้วน ไม่มีตัวอักษร/ตัวเลขจริง"""
    return bool(_GARBAGE_RE.match(text))


def is_oov(word: str) -> bool:
    """ตรวจว่าคำไทยนี้ไม่พบในพจนานุกรมมาตรฐาน (แปลว่าน่าจะเป็นคำที่ OCR อ่านผิด)"""
    if not _PYTHAINLP_OK:
        return False
    has_thai = any(ch in THAI_CONSONANTS or ch in THAI_LEADING_VOWELS for ch in word)
    if not has_thai:
        return False  # ตัวเลข/อังกฤษ ไม่ต้องเช็ค OOV แบบไทย
    return word not in _THAI_VOCAB


@dataclass
class TokenFlags:
    token: WordToken
    floating_mark: bool = False
    garbage: bool = False
    oov: bool = False
    low_conf: bool = False

    @property
    def is_clean(self) -> bool:
        return not (self.floating_mark or self.garbage or self.oov or self.low_conf)


@dataclass
class QualityReport:
    total_tokens: int = 0
    avg_confidence: float = 0.0
    low_conf_ratio: float = 0.0
    floating_mark_ratio: float = 0.0
    garbage_ratio: float = 0.0
    oov_ratio: float = 0.0
    overall_noise_score: float = 0.0  # 0 = สะอาดสมบูรณ์, 1 = สกปรกทั้งหมด
    flags: List[TokenFlags] = field(default_factory=list)

    def to_summary_dict(self) -> Dict[str, float]:
        return {
            "จำนวนคำทั้งหมด (Total Tokens)": self.total_tokens,
            "ความเชื่อมั่น OCR เฉลี่ย (Avg Confidence)": round(self.avg_confidence, 1),
            "สัดส่วนคำความเชื่อมั่นต่ำ (Low-confidence %)": round(self.low_conf_ratio * 100, 1),
            "สัดส่วนสระ/วรรณยุกต์ลอย (Floating-mark %)": round(self.floating_mark_ratio * 100, 1),
            "สัดส่วนสัญลักษณ์ขยะ (Garbage-symbol %)": round(self.garbage_ratio * 100, 1),
            "สัดส่วนคำนอกพจนานุกรม (OOV %)": round(self.oov_ratio * 100, 1),
            "คะแนน Noise รวม (Overall Noise Score, 0-100)": round(self.overall_noise_score * 100, 1),
        }


def profile(tokens: List[WordToken], low_conf_threshold: float = 60.0) -> QualityReport:
    """สแกน token ทั้งหมดที่ได้จาก ingestion แล้วสร้างรายงานคุณภาพข้อมูล"""
    flags: List[TokenFlags] = []
    n = len(tokens)
    if n == 0:
        return QualityReport()

    conf_sum = 0.0
    low_conf_n = 0
    floating_n = 0
    garbage_n = 0
    oov_n = 0

    for t in tokens:
        conf = t.conf if t.conf >= 0 else 0.0
        conf_sum += conf
        f_low = conf < low_conf_threshold and t.conf >= 0
        f_float = is_floating_mark_error(t.text)
        f_garbage = is_garbage_token(t.text)
        f_oov = (not f_garbage) and is_oov(t.text)

        if f_low:
            low_conf_n += 1
        if f_float:
            floating_n += 1
        if f_garbage:
            garbage_n += 1
        if f_oov:
            oov_n += 1

        flags.append(
            TokenFlags(token=t, floating_mark=f_float, garbage=f_garbage, oov=f_oov, low_conf=f_low)
        )

    report = QualityReport(
        total_tokens=n,
        avg_confidence=conf_sum / n,
        low_conf_ratio=low_conf_n / n,
        floating_mark_ratio=floating_n / n,
        garbage_ratio=garbage_n / n,
        oov_ratio=oov_n / n,
        flags=flags,
    )
    # คะแนน noise รวม = ค่าเฉลี่ยถ่วงน้ำหนักของสัญญาณสกปรกทั้ง 4 ตัว
    report.overall_noise_score = (
        0.30 * report.low_conf_ratio
        + 0.25 * report.floating_mark_ratio
        + 0.20 * report.garbage_ratio
        + 0.25 * report.oov_ratio
    )
    return report
