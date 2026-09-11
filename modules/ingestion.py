"""
ingestion.py
------------
Data Ingestion Layer ของ BrailleFlow

หน้าที่:
 1. รับไฟล์อัปโหลด (PDF คุณภาพต่ำ หรือรูปภาพสแกน) จากผู้ใช้
 2. แปลง PDF -> รายภาพต่อหน้า (Page Images)
 3. เรียก OCR (Tesseract, ภาษาไทย+อังกฤษ) เพื่อสกัด "ข้อความดิบ" พร้อม
    "พิกัดกรอบสี่เหลี่ยม (2D Bounding Box)" ของแต่ละคำ -> ได้ Raw Semi-structured
    Data ที่พร้อมส่งต่อให้ pipeline.py ทำความสะอาดและจัดโครงสร้างต่อไป

ผลลัพธ์หลักของโมดูลนี้คือ "Word Token" หนึ่งรายการ มีโครงสร้างเป็น dict:
    {
        "page": int,            # เลขหน้า (เริ่มที่ 1)
        "text": str,            # ข้อความดิบจาก OCR
        "left": int, "top": int, "width": int, "height": int,  # bbox (pixel)
        "conf": float,          # OCR confidence 0-100
        "line_num": int, "block_num": int, "word_num": int,    # ตำแหน่งตาม tesseract
    }
"""

from __future__ import annotations
import io
from dataclasses import dataclass, field
from typing import List

import numpy as np
from PIL import Image
import pytesseract

try:
    from pdf2image import convert_from_bytes
    _PDF_OK = True
except Exception:  # poppler ไม่พร้อมใช้งานในบางสภาพแวดล้อม
    _PDF_OK = False

TESSERACT_LANG = "tha+eng"


@dataclass
class WordToken:
    page: int
    text: str
    left: int
    top: int
    width: int
    height: int
    conf: float
    line_num: int
    block_num: int
    word_num: int

    @property
    def right(self) -> int:
        return self.left + self.width

    @property
    def bottom(self) -> int:
        return self.top + self.height

    def to_dict(self) -> dict:
        return {
            "page": self.page,
            "text": self.text,
            "left": self.left,
            "top": self.top,
            "width": self.width,
            "height": self.height,
            "conf": self.conf,
            "line_num": self.line_num,
            "block_num": self.block_num,
            "word_num": self.word_num,
        }


@dataclass
class IngestResult:
    filename: str
    pages: List[Image.Image] = field(default_factory=list)
    tokens: List[WordToken] = field(default_factory=list)

    @property
    def n_pages(self) -> int:
        return len(self.pages)

    @property
    def n_tokens(self) -> int:
        return len(self.tokens)


def load_pages_from_upload(file_bytes: bytes, filename: str) -> List[Image.Image]:
    """แปลงไฟล์ที่อัปโหลด (PDF หรือรูปภาพ) ให้กลายเป็น list ของ PIL.Image ต่อหน้า"""
    lower = filename.lower()
    if lower.endswith(".pdf"):
        if not _PDF_OK:
            raise RuntimeError(
                "ไม่พบ poppler-utils ในระบบ กรุณาติดตั้งก่อนใช้งานไฟล์ PDF "
                "(apt-get install poppler-utils)"
            )
        pages = convert_from_bytes(file_bytes, dpi=200)
        return [p.convert("RGB") for p in pages]
    else:
        img = Image.open(io.BytesIO(file_bytes)).convert("RGB")
        return [img]


def _preprocess_for_ocr(img: Image.Image) -> Image.Image:
    """ทำความสะอาดภาพเบื้องต้นก่อนส่งเข้า OCR เพื่อลด noise ของภาพสแกนคุณภาพต่ำ
    (grayscale -> adaptive threshold) ช่วยให้ tesseract อ่านตัวอักษรไทยที่จางหรือเป็นจุดได้ดีขึ้น
    """
    import cv2

    arr = np.array(img)
    gray = cv2.cvtColor(arr, cv2.COLOR_RGB2GRAY)
    # ลด salt-and-pepper noise ที่พบบ่อยในภาพสแกนคุณภาพต่ำ
    denoised = cv2.medianBlur(gray, 3)
    thresh = cv2.adaptiveThreshold(
        denoised, 255, cv2.ADAPTIVE_THRESH_GAUSSIAN_C, cv2.THRESH_BINARY, 31, 15
    )
    return Image.fromarray(thresh)


def extract_tokens(pages: List[Image.Image], preprocess: bool = True) -> List[WordToken]:
    """รัน OCR บนทุกหน้า แล้วคืนค่า WordToken ที่มีพิกัด bounding-box ระดับคำ"""
    tokens: List[WordToken] = []
    for page_idx, page_img in enumerate(pages, start=1):
        ocr_img = _preprocess_for_ocr(page_img) if preprocess else page_img
        data = pytesseract.image_to_data(
            ocr_img, lang=TESSERACT_LANG, output_type=pytesseract.Output.DICT
        )
        n = len(data["text"])
        for i in range(n):
            text = data["text"][i].strip()
            if not text:
                continue
            conf_raw = data["conf"][i]
            try:
                conf = float(conf_raw)
            except (TypeError, ValueError):
                conf = -1.0
            tokens.append(
                WordToken(
                    page=page_idx,
                    text=text,
                    left=int(data["left"][i]),
                    top=int(data["top"][i]),
                    width=int(data["width"][i]),
                    height=int(data["height"][i]),
                    conf=conf,
                    line_num=int(data["line_num"][i]),
                    block_num=int(data["block_num"][i]),
                    word_num=int(data["word_num"][i]),
                )
            )
    return tokens


def ingest(file_bytes: bytes, filename: str, preprocess: bool = True) -> IngestResult:
    """จุดเข้าใช้งานหลักของ Ingestion Layer: ไฟล์ดิบ -> IngestResult (หน้าภาพ + tokens)"""
    pages = load_pages_from_upload(file_bytes, filename)
    tokens = extract_tokens(pages, preprocess=preprocess)
    return IngestResult(filename=filename, pages=pages, tokens=tokens)
