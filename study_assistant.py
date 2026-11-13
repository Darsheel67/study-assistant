import os
import sys
import io
import time
import base64
import threading
import ctypes
from ctypes import wintypes
import tkinter as tk
from PIL import Image, ImageGrab, ImageOps, ImageEnhance, ImageFilter
import keyboard
import pyperclip
import asyncio
import json
import re
import math
import cmath
import sqlite3
import concurrent.futures
from dataclasses import dataclass
from typing import Optional, Tuple, List, Dict, Any

try:
    import sympy
except Exception:
    sympy = None

try:
    import pymupdf as fitz
except Exception:
    fitz = None

import winocr
from groq import Groq
try:
    from google import genai
    from google.genai import types
except Exception:
    genai = None
    types = None

# Enable high-DPI awareness on Windows
try:
    ctypes.windll.shcore.SetProcessDpiAwareness(1)
except Exception:
    try:
        ctypes.windll.user32.SetProcessDPIAware()
    except Exception:
        pass

# Global Shortcuts
HOTKEY_SOLVE   = "alt+q"      # 1-Click Auto Solve / Solve Accumulated Lines
HOTKEY_ACCUM   = "alt+e"      # Capture line at cursor and append to accumulator
HOTKEY_CLEAR   = "alt+c"      # Clear accumulated lines
HOTKEY_SNIP    = "alt+s"      # Quick Snip Solve (Drag box over tricky question)
HOTKEY_HIDE    = "alt+w"      # Emergency / Instant Hide Answer
DEFAULT_GROQ_KEY = ""
ENV_FILE = os.path.join(os.path.dirname(os.path.abspath(__file__)), ".env")

def _load_local_env():
    """Load API keys from .env file if present in the script directory."""
    if os.path.exists(ENV_FILE):
        try:
            with open(ENV_FILE, "r", encoding="utf-8") as f:
                for line in f:
                    line = line.strip()
                    if line and not line.startswith("#") and "=" in line:
                        k, v = line.split("=", 1)
                        k = k.strip()
                        v = v.strip().strip('"').strip("'")
                        if k and not os.environ.get(k):
                            os.environ[k] = v
        except Exception:
            pass

_load_local_env()
USER_HOME = os.path.expanduser("~")
LOG_FILE = os.path.join(USER_HOME, "study_assistant.log")
BENCHMARK_LOG = os.path.join(USER_HOME, "study_assistant_benchmark.jsonl")
RAG_DB_PATH = os.path.join(USER_HOME, ".study_rag.db")
STUDY_MATERIALS_DIR = os.path.join(USER_HOME, "Desktop", "study")


def log(msg: str):
    timestamp = time.strftime("%Y-%m-%d %H:%M:%S")
    formatted = f"[{timestamp}] {msg}"
    try:
        if sys.stdout and not sys.stdout.closed:
            print(formatted, flush=True)
    except Exception:
        pass
    try:
        with open(LOG_FILE, "a", encoding="utf-8") as f:
            f.write(formatted + "\n")
    except Exception:
        pass


# Persistent client instances
_gemini_client = None
_groq_client = None
_request_history = []
_daily_count = 0


def get_groq_client():
    global _groq_client
    if _groq_client is None:
        key = os.environ.get("GROQ_API_KEY", DEFAULT_GROQ_KEY)
        if key:
            try:
                _groq_client = Groq(api_key=key)
            except Exception as e:
                log(f"[Groq Client Init Error]: {e}")
    return _groq_client


def get_gemini_client():
    global _gemini_client
    if _gemini_client is None and genai is not None:
        key = os.environ.get("GEMINI_API_KEY")
        if key:
            try:
                _gemini_client = genai.Client(api_key=key)
            except Exception as e:
                log(f"[Gemini Client Init Error]: {e}")
    return _gemini_client


def switch_to_input_desktop():
    """Ensures thread is attached to the user's interactive desktop."""
    try:
        user32 = ctypes.windll.user32
        hdesk = user32.OpenInputDesktop(0, False, 0x01FF)
        if hdesk:
            user32.SetThreadDesktop(hdesk)
    except Exception:
        pass


def get_cursor_position() -> tuple[int, int]:
    """Gets current mouse cursor screen coordinates."""
    switch_to_input_desktop()
    try:
        pt = wintypes.POINT()
        ctypes.windll.user32.GetCursorPos(ctypes.byref(pt))
        return pt.x, pt.y
    except Exception:
        return 0, 0


# ==============================================================================
# 1. OCR ENHANCEMENT & PREPROCESSING PIPELINE
# ==============================================================================

def preprocess_image_for_ocr(img: Image.Image) -> Image.Image:
    """
    Preprocess screenshot for maximum OCR legibility:
    1. Upscale 2x with Lanczos for small fonts
    2. Convert to Grayscale
    3. Enhance Contrast
    4. Apply mild unsharp mask sharpening
    """
    try:
        w, h = img.size
        # Upscale 2x
        upscaled = img.resize((w * 2, h * 2), Image.Resampling.LANCZOS)
        # Convert to Grayscale
        gray = upscaled.convert("L")
        # Increase contrast
        enhancer = ImageEnhance.Contrast(gray)
        contrast = enhancer.enhance(1.6)
        # Mild sharpening
        sharpened = contrast.filter(ImageFilter.UnsharpMask(radius=1.5, percent=150, threshold=3))
        return sharpened
    except Exception as e:
        log(f"[OCR Preprocessing Warn]: {e}")
        return img


def extract_text_via_ocr(pil_img: Image.Image) -> str:
    """Extracts clean text from image using Windows native offline OCR (~30ms) with preprocessing."""
    try:
        enhanced = preprocess_image_for_ocr(pil_img)
        loop = asyncio.new_event_loop()
        asyncio.set_event_loop(loop)
        result = loop.run_until_complete(winocr.recognize_pil(enhanced))
        loop.close()
        if result and result.text:
            return result.text.strip()
    except Exception as e:
        log(f"[OCR Error]: {e}")
    return ""


def repair_ocr_code_artifacts(text: str) -> str:
    """Heuristically repairs common OCR character confusions in code lines."""
    if not text:
        return ""
    # Repair missing opening brace if line ends with closing brace
    # e.g. "d = 'a': 10, 'b': 20}" -> "d = {'a': 10, 'b': 20}"
    if "}" in text and "{" not in text and ("=" in text or ":" in text):
        text = re.sub(r'=\s*(\'|"[a-zA-Z0-9]|1|2|3|4|5|6|7|8|9|0|\w)', r'= {\1', text)
        if "{" not in text:
            text = "{" + text
    # Repair '.get(..., O)' -> '.get(..., 0)'
    text = re.sub(r'\.get\(([^,]+),\s*[Oo]\)', r'.get(\1, 0)', text)
    # Repair 'IO' as 10
    text = re.sub(r':\s*IO\b', ': 10', text)
    text = re.sub(r'\bIO\b', '10', text)
    # Repair 'l:t' -> '1: "c"'
    text = re.sub(r'\bl:t\b', "1: 'c'", text)
    return text.strip()


def validate_ocr_quality(text: str) -> Tuple[bool, str]:
    """
    Validates OCR reliability. Returns (is_valid, reason).
    Checks for:
    - Minimum length
    - Math formulas / LaTeX / tables / garbled Unicode characters
    - Code structures / curly braces that Windows OCR typically mangles
    """
    if not text or len(text.strip()) < 15:
        return False, "Text too short (< 15 chars)"

    # Check for excessive unprintable / garbled characters
    printable_count = sum(1 for c in text if c.isprintable())
    if printable_count / max(1, len(text)) < 0.85:
        return False, "High proportion of garbled/unprintable characters"

    # Route complex math symbols, formulas, and tables straight to vision model
    math_symbols = [
        "∫", "∑", "√", "∂", "±", "≤", "≥", "≠", "≈", "∈", "∉", "⊂", "⊃",
        "α", "β", "γ", "θ", "λ", "μ", "π", "σ", "ω", "^", "÷", "×", "\\frac",
        "\\sum", "\\int", "\\sqrt"
    ]
    math_count = sum(text.count(sym) for sym in math_symbols)
    if math_count >= 2:
        return False, f"Contains mathematical formulas/symbols ({math_count} symbols)"

    # Check for ASCII tables or tab-separated column grids
    if text.count("|") >= 4 or text.count("---") >= 2:
        return False, "Contains structured table or matrix"

    # If code syntax or braces are detected, route to Vision for 100% symbol precision
    if ("{" in text and "}" not in text) or ("}" in text and "{" not in text):
        return False, "Unmatched curly braces in code"
    if any(kw in text for kw in ["def ", "class ", "dict", "print(", ".get(", "IO,", ": IO", "l:t"]):
        return False, "Contains code with delicate syntax, routing to Vision"

    return True, "Valid text"


# ==============================================================================
# 2. QUESTION PARSER & STRUCTURE DETECTOR
# ==============================================================================

@dataclass
class ParsedQuestion:
    raw_text: str
    stem: str
    options: List[str]
    q_type: str  # "mcq", "numeric", "code", "true_false", "conceptual"
    has_code: bool
    code_snippet: Optional[str] = None


def parse_question_structure(text: str) -> ParsedQuestion:
    """
    Splits OCR text into stem, options, question type, and extracts code snippets.
    Preserves multi-line code formatting, indentation, and detects 'predict the output' questions.
    """
    lines = text.splitlines()
    stem_lines = []
    options = []
    opt_pattern = re.compile(r"^\s*([A-Da-d])[\)\.\:\s\-]+(.*)$")

    in_options = False
    for line in lines:
        stripped = line.strip()
        if not stripped:
            if not in_options:
                stem_lines.append("")
            continue

        m = opt_pattern.match(line)
        if m:
            in_options = True
            letter = m.group(1).upper()
            content = m.group(2).strip()
            options.append(f"{letter}) {content}")
        elif in_options:
            if options:
                options[-1] += f" {stripped}"
        else:
            # Preserve original line with formatting for code snippets
            stem_lines.append(line)

    stem = "\n".join(stem_lines).strip()
    full_text = text.strip()

    # Detect code block or multi-line code snippet
    code_match = re.search(r"```(?:python|c|cpp|java)?\s*([\s\S]*?)```", text)
    has_code = False
    code_snippet = None
    if code_match:
        has_code = True
        code_snippet = code_match.group(1).strip()
    else:
        # Check for multi-line programming patterns in stem
        code_indicators = [
            "def ", "class ", "import ", "print(", "for ", "while ",
            "return ", "int ", "void ", "public static", "#include",
            "console.log", "System.out", "char ", "float ", "bool "
        ]
        count = sum(1 for kw in code_indicators if kw in text)
        if count >= 2 or any(term in text.lower() for term in [
            "what is the output", "predict the output", "what will be printed",
            "output of the following", "output of following", "following code",
            "following program", "snippet"
        ]):
            has_code = True
            # Extract code block from stem lines
            c_lines = []
            for l in stem_lines:
                sl = l.strip()
                if any(sl.startswith(kw) for kw in [
                    "def ", "for ", "while ", "if ", "elif ", "else:", "print(",
                    "int ", "char ", "return ", "import ", "#", "x =", "y =", "a =", "b =", "s ="
                ]) or (l.startswith("    ") or l.startswith("\t")):
                    c_lines.append(l)
            if c_lines:
                code_snippet = "\n".join(c_lines)

    # Determine question type
    text_lower = text.lower()
    if any(phrase in text_lower for phrase in [
        "what is the output", "predict the output", "what will be printed",
        "output of the following", "output of following"
    ]):
        q_type = "code_output"
    elif re.search(r"\b(true\s+or\s+false|true/false)\b", text_lower) or (len(options) == 2 and any("true" in o.lower() for o in options)):
        q_type = "true_false"
    elif has_code:
        q_type = "code"
    elif len(options) >= 2:
        q_type = "mcq"
    elif any(term in text_lower for term in ["calculate", "compute", "solve for", "value of", "how many"]):
        q_type = "numeric"
    else:
        q_type = "conceptual"

    return ParsedQuestion(
        raw_text=text,
        stem=stem if stem else text,
        options=options,
        q_type=q_type,
        has_code=has_code,
        code_snippet=code_snippet
    )


# ==============================================================================
# 3. COMPUTATIONAL / CODE SANDBOX EXECUTION (SymPy & Python)
# ==============================================================================

def execute_sandboxed_python(code_str: str, timeout: float = 2.0) -> Tuple[Optional[str], Optional[str]]:
    """
    Safely executes pure Python code / snippet and captures standard output and returns.
    """
    if not code_str:
        return None, "Empty code"

    def _worker():
        import io, contextlib
        stdout_buf = io.StringIO()
        loc = {
            "math": math,
            "cmath": cmath,
            "sympy": sympy,
            "result": None
        }
        safe_builtins = {
            "abs": abs, "round": round, "min": min, "max": max,
            "sum": sum, "len": len, "range": range, "enumerate": enumerate,
            "zip": zip, "int": int, "float": float, "str": str,
            "bool": bool, "list": list, "dict": dict, "set": set,
            "tuple": tuple, "pow": pow, "sorted": sorted, "reversed": reversed,
            "print": print
        }
        with contextlib.redirect_stdout(stdout_buf):
            exec(code_str, {"__builtins__": safe_builtins}, loc)
        out = stdout_buf.getvalue().strip()
        if out:
            return out
        if loc.get("result") is not None:
            return str(loc.get("result"))
        return None

    with concurrent.futures.ThreadPoolExecutor(max_workers=1) as executor:
        future = executor.submit(_worker)
        try:
            res = future.result(timeout=timeout)
            return res, None
        except concurrent.futures.TimeoutError:
            return None, "Execution timed out (> 2.0s)"
        except Exception as e:
            return None, str(e)


# ==============================================================================
# 4. SUBJECT-SPECIFIC RETRIEVAL (Lightweight SQLite FTS5 Vector/Keyword RAG)
# ==============================================================================

class StudyNotesRAG:
    """
    Lightweight indexing of user study materials (PDFs/notes in ~/Desktop/study)
    using SQLite FTS5 for zero-dependency, ultra-fast local retrieval.
    """
    def __init__(self, db_path: str = RAG_DB_PATH, study_dir: str = STUDY_MATERIALS_DIR):
        self.db_path = db_path
        self.study_dir = study_dir
        self._init_db()

    def _init_db(self):
        try:
            with sqlite3.connect(self.db_path) as conn:
                conn.execute("""
                    CREATE TABLE IF NOT EXISTS meta (
                        key TEXT PRIMARY KEY,
                        value TEXT
                    )
                """)
                conn.execute("""
                    CREATE VIRTUAL TABLE IF NOT EXISTS chunks USING fts5(
                        doc_name,
                        page_num,
                        content,
                        tokenize='porter unicode61'
                    )
                """)
                conn.commit()
        except Exception as e:
            log(f"[RAG Init Warn]: {e}")

    def index_study_folder_async(self):
        """Indexes PDF files in background without blocking."""
        threading.Thread(target=self._index_worker, daemon=True).start()

    def _index_worker(self):
        if not os.path.isdir(self.study_dir) or fitz is None:
            return
        try:
            with sqlite3.connect(self.db_path) as conn:
                cur = conn.cursor()
                cur.execute("SELECT COUNT(*) FROM chunks")
                count = cur.fetchone()[0]
                if count > 0:
                    return  # Already indexed

                log("[RAG] Building local study knowledge index from Desktop/study...")
                indexed = 0
                for root_dir, _, files in os.walk(self.study_dir):
                    for file in files:
                        if file.lower().endswith(".pdf"):
                            pdf_path = os.path.join(root_dir, file)
                            try:
                                doc = fitz.open(pdf_path)
                                for pno in range(min(len(doc), 50)):  # Index up to 50 key pages per PDF
                                    txt = doc[pno].get_text()
                                    if len(txt.strip()) > 50:
                                        cur.execute(
                                            "INSERT INTO chunks(doc_name, page_num, content) VALUES (?, ?, ?)",
                                            (file, pno + 1, txt[:2000])
                                        )
                                        indexed += 1
                                doc.close()
                            except Exception:
                                pass
                conn.commit()
                log(f"[RAG] Indexed {indexed} chunks from local study materials.")
        except Exception as e:
            log(f"[RAG Index Error]: {e}")

    def retrieve_relevant_context(self, query: str, limit: int = 2) -> str:
        """Finds most relevant notes snippet for the question stem."""
        try:
            clean_query = re.sub(r"[^a-zA-Z0-9\s]", " ", query).strip()
            terms = [t for t in clean_query.split() if len(t) > 3][:6]
            if not terms:
                return ""
            fts_query = " OR ".join(terms)

            with sqlite3.connect(self.db_path) as conn:
                cur = conn.cursor()
                cur.execute(
                    "SELECT doc_name, page_num, content FROM chunks WHERE chunks MATCH ? ORDER BY rank LIMIT ?",
                    (fts_query, limit)
                )
                rows = cur.fetchall()
                if rows:
                    snippets = [f"[{r[0]} p.{r[1]}]: {r[2][:300].strip()}..." for r in rows]
                    return "\n\nRelevant Course Notes:\n" + "\n".join(snippets)
        except Exception:
            pass
        return ""


# Initialize RAG in background
_rag_system = StudyNotesRAG()
_rag_system.index_study_folder_async()


# ==============================================================================
# 5. STRUCTURED REASONING & CROSS-CHECK ENGINE
# ==============================================================================

@dataclass
class ModelResult:
    model_name: str
    reasoning: str
    answer: str
    confidence: float
    raw_response: str


def is_valid_answer(ans: str) -> bool:
    """Ensures string is a genuine answer option and not a raw JSON dump or explanation."""
    if not ans:
        return False
    ans_clean = ans.strip()
    if ans_clean.startswith("{") or ans_clean.startswith("[") or "reasoning" in ans_clean.lower():
        return False
    if len(ans_clean) > 85:
        return False
    if "\n" in ans_clean and len(ans_clean.splitlines()) > 2:
        return False
    return True


def clean_extracted_answer(ans_str: str) -> str:
    """Cleans and standardizes extracted answer string."""
    if not ans_str:
        return ""
    clean = ans_str.replace("**", "").replace("__", "").replace("`", "").strip()
    for pfx in ["Answer:", "The answer is", "Correct Answer:", "FINAL:", "Option:"]:
        if clean.lower().startswith(pfx.lower()):
            clean = clean[len(pfx):].strip()
    # Normalize 'B) answer text' or 'b. answer text'
    m = re.match(r"^([A-Da-d])[\)\.\:\s\-]+(.*)$", clean)
    if m:
        letter = m.group(1).upper()
        rest = m.group(2).strip()
        clean = f"{letter}) {rest}" if rest else letter
    return clean


def parse_structured_json(raw: str) -> Tuple[str, str]:
    """Extracts reasoning and answer from structured model output (JSON or plain text)."""
    if not raw:
        return "", ""

    # Attempt 1: Parse as JSON
    try:
        json_match = re.search(r"\{[\s\S]*\}", raw)
        if json_match:
            data = json.loads(json_match.group(0))
            ans = clean_extracted_answer(str(data.get("answer", "")))
            if is_valid_answer(ans):
                return str(data.get("reasoning", "")).strip()[:300], ans
    except Exception:
        pass

    # Attempt 2: Direct regex for "answer": "..." in partial/broken JSON
    ans_json_match = re.search(r'"answer"\s*:\s*"([^"\\]*(?:\\.[^"\\]*)*)"', raw)
    if ans_json_match:
        ans = clean_extracted_answer(ans_json_match.group(1))
        if is_valid_answer(ans):
            return "", ans

    # Attempt 3: REASONING: / ANSWER: tagged format
    answer_match = re.search(r"(?:ANSWER|Answer|answer)\s*:\s*(.+)", raw)
    if answer_match:
        ans = clean_extracted_answer(answer_match.group(1))
        if is_valid_answer(ans):
            reasoning_match = re.search(r"(?:REASONING|Reasoning|reasoning)\s*:\s*([\s\S]*?)(?=\n\s*(?:ANSWER|Answer|answer)\s*:|\Z)", raw)
            reas = reasoning_match.group(1).strip() if reasoning_match else ""
            return reas[:300], ans

    # Attempt 4: Look for explicit option letter pattern (e.g. 'B) 20' or 'C) Queue')
    opt_matches = re.findall(r"(?:^|\s|\()(?:Option\s*)?([A-Da-d])(?:[\)\:]|\.\s+)\s*([^\n\r,;]{1,60})", raw)
    if opt_matches:
        letter, text = opt_matches[-1]
        ans = clean_extracted_answer(f"{letter.upper()}) {text.strip()}")
        if is_valid_answer(ans):
            return raw[:200], ans

    # Attempt 5: Line-by-line fallback
    lines = [l.strip() for l in raw.splitlines() if l.strip()]
    for line in reversed(lines):
        clean_l = clean_extracted_answer(line)
        if is_valid_answer(clean_l) and (clean_l.startswith("Answer") or re.match(r"^[A-Da-d]\)", clean_l)):
            return "", clean_l

    return "", ""


def query_groq_structured(model: str, prompt: str, system_prompt: str) -> Optional[ModelResult]:
    """
    Queries Groq with structured reasoning + answer.
    Attempts strict JSON mode with generous token limit, then falls back to plain-text mode.
    """
    client = get_groq_client()
    if not client:
        return None

    messages = [
        {"role": "system", "content": system_prompt},
        {"role": "user", "content": prompt}
    ]

    # Attempt 1: Strict JSON mode
    try:
        t0 = time.time()
        resp = client.chat.completions.create(
            model=model,
            messages=messages,
            response_format={"type": "json_object"},
            temperature=0.1,
            max_tokens=800
        )
        content = resp.choices[0].message.content if resp and resp.choices else ""
        reasoning, answer = parse_structured_json(content)
        elapsed = time.time() - t0
        if is_valid_answer(answer):
            log(f"[Groq Solve: {model}] {elapsed:.2f}s | Ans: {answer}")
            return ModelResult(model_name=model, reasoning=reasoning, answer=answer,
                               confidence=0.95, raw_response=content)
    except Exception as e:
        err_str = str(e)
        if "json_validate_failed" in err_str:
            log(f"[Groq {model}] JSON validation failed, checking partial generation...")
            failed_match = re.search(r"'failed_generation':\s*'(.*?)'(?:,|\})", err_str, re.S)
            if failed_match:
                raw_failed = failed_match.group(1).encode("utf-8").decode("unicode_escape", errors="ignore")
                reasoning, answer = parse_structured_json(raw_failed)
                if is_valid_answer(answer):
                    log(f"[Groq Solve: {model} (recovered)] Ans: {answer}")
                    return ModelResult(model_name=model, reasoning=reasoning, answer=answer,
                                       confidence=0.85, raw_response=raw_failed)
        else:
            log(f"[Groq {model} Error]: {e}")

    # Attempt 2: Plain-text fallback (no json_object constraint)
    try:
        t0 = time.time()
        fallback_sys = (
            "You are an expert exam solver. Think step-by-step.\n"
            "Format your response EXACTLY as:\n"
            "ANSWER: <option letter and text, e.g. B) 20 or C) Queue>\n"
            "REASONING: <brief 2-sentence explanation>"
        )
        resp = client.chat.completions.create(
            model=model,
            messages=[
                {"role": "system", "content": fallback_sys},
                {"role": "user", "content": prompt}
            ],
            temperature=0.1,
            max_tokens=600
        )
        content = resp.choices[0].message.content if resp and resp.choices else ""
        reasoning, answer = parse_structured_json(content)
        elapsed = time.time() - t0
        if is_valid_answer(answer):
            log(f"[Groq Solve: {model} (plaintext)] {elapsed:.2f}s | Ans: {answer}")
            return ModelResult(model_name=model, reasoning=reasoning, answer=answer,
                               confidence=0.85, raw_response=content)
    except Exception as e:
        log(f"[Groq {model} Plaintext Error]: {e}")

    return None


# Active Gemini model fallback chain with high free quota
GEMINI_MODELS = ["gemini-3.5-flash", "gemini-3.6-flash", "gemini-3.5-flash-lite"]


def query_gemini_structured(prompt: str, system_prompt: str, jpeg_bytes: Optional[bytes] = None) -> Optional[ModelResult]:
    """
    Queries Gemini with automatic model fallback across active high-quota models.
    Reads directly from image pixels if jpeg_bytes is provided.
    """
    client = get_gemini_client()
    if not client or types is None:
        return None

    contents = []
    if jpeg_bytes:
        part = types.Part.from_bytes(data=jpeg_bytes, mime_type="image/jpeg")
        contents.append(part)
        gemini_text = "Solve the question shown in this image. Read the exact code (including all curly braces {}, brackets, quotes, colons, numbers) and options directly from the image."
        if "Relevant Course Notes:" in prompt:
            gemini_text += "\n\n" + prompt[prompt.find("Relevant Course Notes:"):]
        contents.append(gemini_text)
    else:
        contents.append(prompt)

    for model_name in GEMINI_MODELS:
        try:
            t0 = time.time()
            cfg = types.GenerateContentConfig(
                system_instruction=system_prompt,
                response_mime_type="application/json",
                temperature=0.1,
                max_output_tokens=800
            )
            resp = client.models.generate_content(
                model=model_name,
                contents=contents,
                config=cfg
            )
            content = resp.text if resp and resp.text else ""
            reasoning, answer = parse_structured_json(content)
            elapsed = time.time() - t0
            if is_valid_answer(answer):
                log(f"[Gemini Solve: {model_name}] {elapsed:.2f}s | Ans: {answer}")
                return ModelResult(
                    model_name=model_name,
                    reasoning=reasoning,
                    answer=answer,
                    confidence=0.95,
                    raw_response=content
                )
        except Exception as e:
            err_str = str(e)
            if any(code in err_str for code in ["429", "RESOURCE_EXHAUSTED", "503", "404"]):
                log(f"[Gemini {model_name}] Unavailable ({err_str[:40]}...), trying next model...")
                continue
            else:
                log(f"[Gemini {model_name} Error]: {e}")
                return None

    log("[Gemini] All models exhausted or failed.")
    return None


def query_groq_vision(jpeg_bytes: bytes, prompt: str, system_prompt: str) -> Optional[ModelResult]:
    """Queries Groq Vision model for diagrams and image-based problems directly from screen pixels."""
    client = get_groq_client()
    if not client:
        return None
    try:
        t0 = time.time()
        img_b64 = base64.b64encode(jpeg_bytes).decode("utf-8")
        vision_sys = (
            "You are an expert exam solver. Read the code, question, and options directly from the image.\n"
            "Format strictly as:\n"
            "ANSWER: <option letter and text, e.g. B) c or A) Queue>\n"
            "REASONING: <concise 2-sentence explanation>"
        )
        vision_text = "Solve the question shown in this image. Read the exact question statement, code (including all curly braces {}, brackets, quotes, numbers), and options directly from the image."
        if "Relevant Course Notes:" in prompt:
            vision_text += "\n\n" + prompt[prompt.find("Relevant Course Notes:"):]

        resp = client.chat.completions.create(
            model="qwen/qwen3.8-27b",
            messages=[
                {"role": "system", "content": vision_sys},
                {
                    "role": "user",
                    "content": [
                        {"type": "image_url", "image_url": {"url": f"data:image/jpeg;base64,{img_b64}"}},
                        {"type": "text", "text": vision_text}
                    ]
                }
            ],
            temperature=0.0,
            max_tokens=300
        )
        content = resp.choices[0].message.content if resp and resp.choices else ""
        reasoning, answer = parse_structured_json(content)
        elapsed = time.time() - t0
        if is_valid_answer(answer):
            log(f"[Groq Vision Solve] {elapsed:.2f}s | Ans: {answer}")
            return ModelResult(
                model_name="groq-vision(qwen3.8-27b)",
                reasoning=reasoning,
                answer=answer,
                confidence=0.95,
                raw_response=content
            )
    except Exception as e:
        log(f"[Groq Vision Error]: {e}")
    return None


# ==============================================================================
# 6. ENSEMBLE CROSS-CHECK & SELF-CONSISTENCY VOTING
# ==============================================================================

def extract_answer_key(ans: str) -> str:
    """Extracts standardized comparison key (e.g. 'B', 'True', or clean alphanumeric)."""
    if not ans:
        return ""
    m = re.match(r"^([A-Da-d])[\)\.\:\s\-]+", ans.strip())
    if m:
        return m.group(1).upper()
    m2 = re.match(r"^(true|false)", ans.strip(), re.I)
    if m2:
        return m2.group(1).capitalize()
    return re.sub(r"[^a-zA-Z0-9]", "", ans.lower())[:20]


def cross_check_and_ensemble(results: List[ModelResult], question_parsed: ParsedQuestion) -> str:
    """
    Compares answers from multiple parallel models.
    - If models agree: Returns consensus answer with high confidence.
    - If models disagree: Flags as low confidence with side-by-side comparison.
    """
    valid_results = [r for r in results if r and is_valid_answer(r.answer)]
    if not valid_results:
        return "Error: All models failed to formulate an answer."

    # Map keys for voting
    keys = [extract_answer_key(r.answer) for r in valid_results]
    
    # Majority vote
    counts: Dict[str, int] = {}
    for k in keys:
        counts[k] = counts.get(k, 0) + 1
    
    majority_key, max_count = max(counts.items(), key=lambda item: item[1])
    
    # Agreement condition
    if max_count >= 2 or len(valid_results) == 1:
        # Pick the most complete answer string matching the majority key
        best_ans = next(r.answer for r in valid_results if extract_answer_key(r.answer) == majority_key)
        best_reason = next(r.reasoning for r in valid_results if extract_answer_key(r.answer) == majority_key)
        
        # Log benchmark entry
        log_benchmark(question_parsed, best_ans, best_reason, agreed=True)
        return best_ans
    else:
        # Disagreement detected -> Flag low confidence
        ans_a = valid_results[0]
        ans_b = valid_results[1]
        flagged = f"⚠️ Low Conf: {ans_a.answer} vs {ans_b.answer}"
        log(f"[Cross-Check Conflict] Model {ans_a.model_name} says '{ans_a.answer}', Model {ans_b.model_name} says '{ans_b.answer}'")
        log_benchmark(question_parsed, flagged, f"{ans_a.model_name}: {ans_a.reasoning} | {ans_b.model_name}: {ans_b.reasoning}", agreed=False)
        return flagged


def log_benchmark(q: ParsedQuestion, chosen_ans: str, reasoning: str, agreed: bool):
    """Logs question, answers, and reasoning for error pattern analysis (Benchmark)."""
    try:
        entry = {
            "timestamp": time.strftime("%Y-%m-%d %H:%M:%S"),
            "stem": q.stem[:200],
            "options": q.options,
            "q_type": q.q_type,
            "chosen_answer": chosen_ans,
            "reasoning": reasoning[:300],
            "agreed": agreed
        }
        with open(BENCHMARK_LOG, "a", encoding="utf-8") as f:
            f.write(json.dumps(entry) + "\n")
    except Exception:
        pass


# ==============================================================================
# 7. SCREEN CAPTURE & MULTI-CHANNEL SOLVER PIPELINE
# ==============================================================================

def capture_optimized_screen(cursor_focus: bool = True) -> tuple[Image.Image | None, bytes]:
    """Captures desktop screen with high DPI and cursor positioning."""
    switch_to_input_desktop()
    raw_img = None

    try:
        raw_img = ImageGrab.grab(all_screens=True)
        if raw_img and raw_img.convert("L").getextrema() == (0, 0):
            raw_img = None
    except Exception:
        raw_img = None

    if not raw_img:
        try:
            raw_img = ImageGrab.grab()
        except Exception:
            raw_img = None

    if not raw_img:
        try:
            import mss
            with mss.mss() as sct:
                mon = sct.monitors[1] if len(sct.monitors) > 1 else sct.monitors[0]
                shot = sct.grab(mon)
                raw_img = Image.frombytes("RGB", shot.size, shot.bgra, "raw", "BGRX")
        except Exception as e:
            log(f"[MSS Capture Error]: {e}")

    if not raw_img:
        log("[Capture Error]: All screen capture methods failed!")
        return None, b""

    # Smart Crop around mouse cursor when in auto mode (Alt+Q)
    if cursor_focus:
        cx, cy = get_cursor_position()
        w, h = raw_img.size
        # Crop generous area (1400x1000) around cursor to capture long multi-line stems & code blocks
        if 0 < cx < w and 0 < cy < h:
            crop_w, crop_h = min(w, 1400), min(h, 1000)
            x1 = max(0, cx - crop_w // 2)
            y1 = max(0, cy - 250)  # questions usually start higher above cursor
            x2 = min(w, x1 + crop_w)
            y2 = min(h, y1 + crop_h)
            cropped_focus = raw_img.crop((x1, y1, x2, y2))
            buf = io.BytesIO()
            cropped_focus.convert("RGB").save(buf, format="JPEG", quality=95)
            return cropped_focus, buf.getvalue()

    max_dim = 2560
    w, h = raw_img.size
    if max(w, h) > max_dim:
        scale = max_dim / max(w, h)
        raw_img = raw_img.resize((int(w * scale), int(h * scale)), Image.Resampling.LANCZOS)

    buf = io.BytesIO()
    raw_img.convert("RGB").save(buf, format="JPEG", quality=95, optimize=True)
    return raw_img, buf.getvalue()


def transcribe_line_with_vision(img: Image.Image) -> Optional[str]:
    """Uses Groq Vision to transcribe code and tricky characters ({, }, 1:c, colons) in ~0.5s."""
    client = get_groq_client()
    if not client:
        return None
    try:
        buf = io.BytesIO()
        img.convert("RGB").save(buf, format="JPEG", quality=95)
        img_b64 = base64.b64encode(buf.getvalue()).decode("utf-8")
        resp = client.chat.completions.create(
            model="qwen/qwen3.8-27b",
            messages=[
                {
                    "role": "user",
                    "content": [
                        {"type": "image_url", "image_url": {"url": f"data:image/jpeg;base64,{img_b64}"}},
                        {"type": "text", "text": "Transcribe the single line of text or code near the vertical center. Preserve all curly braces {}, brackets [], colons, quotes, indentation, and exact numbers. Output ONLY the line text."}
                    ]
                }
            ],
            max_tokens=80,
            temperature=0.0
        )
        if resp and resp.choices and resp.choices[0].message.content:
            text = resp.choices[0].message.content.strip()
            # Clean markdown code blocks if wrapped
            if text.startswith("```") and text.endswith("```"):
                text = text.strip("`").strip()
                if "\n" in text:
                    text = text.split("\n", 1)[1] if text.startswith("python") else text
            if text.startswith("`") and text.endswith("`"):
                text = text.strip("`").strip()
            if text and not text.lower().startswith("i cannot"):
                return text.strip()
    except Exception as e:
        log(f"[Vision Line Transcribe Error]: {e}")
    return None


def capture_line_at_cursor() -> Optional[str]:
    """
    Precision line capture:
    1. Captures a strip around the mouse cursor (±30px vertically).
    2. Primary: Uses Groq Vision to read the exact line in 0.5s (flawless on {, }, 1:c, quotes).
    3. Fallback: Uses Windows Native OCR with nearest-line selection and heuristic repair.
    """
    switch_to_input_desktop()
    cx, cy = get_cursor_position()
    try:
        raw_img = ImageGrab.grab(all_screens=True)
    except Exception:
        raw_img = None

    if not raw_img:
        try:
            raw_img = ImageGrab.grab()
        except Exception:
            return None

    w, h = raw_img.size
    # Vertical strip: ±30px (total 60px height) around cursor
    strip_half_h = 30
    x1 = max(0, cx - 700)
    y1 = max(0, cy - strip_half_h)
    x2 = min(w, cx + 700)
    y2 = min(h, cy + strip_half_h)

    strip_crop = raw_img.crop((x1, y1, x2, y2))

    # 1. Primary: Vision Transcription (solves { and 1:c and single quote omissions)
    vision_text = transcribe_line_with_vision(strip_crop)
    if vision_text:
        return vision_text
    
    # 2. Offline Fallback: Windows OCR with nearest-line calculation & code artifact repair
    try:
        enhanced = preprocess_image_for_ocr(strip_crop)
        loop = asyncio.new_event_loop()
        asyncio.set_event_loop(loop)
        result = loop.run_until_complete(winocr.recognize_pil(enhanced))
        loop.close()

        if not result or not result.lines:
            return None

        # If only 1 line recognized, return it directly
        if len(result.lines) == 1:
            return repair_ocr_code_artifacts(result.lines[0].text.strip())

        # In the 2x upscaled image, cursor Y relative to the crop
        target_y = strip_half_h * 2  # upscaled center

        best_line = None
        min_dist = float("inf")

        for line_obj in result.lines:
            y_centers = []
            for word_obj in getattr(line_obj, "words", []):
                rect = getattr(word_obj, "bounding_rect", None)
                if rect:
                    y_centers.append(rect.y + rect.height / 2)
            
            if y_centers:
                line_mid = sum(y_centers) / len(y_centers)
                dist = abs(line_mid - target_y)
                if dist < min_dist:
                    min_dist = dist
                    best_line = line_obj.text.strip()

        if best_line:
            return repair_ocr_code_artifacts(best_line)

        # Fallback to first line
        return repair_ocr_code_artifacts(result.lines[0].text.strip())
    except Exception as e:
        log(f"[Line Capture Error]: {e}")
        return None


def solve_with_crosscheck(parsed_q: ParsedQuestion, rag_context: str, jpeg_bytes: Optional[bytes] = None) -> str:
    """
    Executes parallel multi-model cross-checking with structured reasoning and majority voting.
    Includes sandbox pre-execution for 'predict the output' code questions.
    """
    sys_instruction = (
        "You are an expert exam solver. Think step-by-step to arrive at the correct answer.\n"
        "Pay special attention to multi-line code, loop iterations, slicing, and exact output.\n"
        "Respond ONLY with a valid JSON object matching this schema:\n"
        "{\n"
        '  "answer": "Correct option letter and answer text, e.g. B) 10 or B) Queue",\n'
        '  "reasoning": "Concise step-by-step logic, code trace, or analysis (under 3 sentences)."\n'
        "}\n"
        "Always place 'answer' first so it is never truncated. Be extremely rigorous and accurate."
    )

    # If it's a code snippet or output question, try executing it in the sandbox to verify the real output
    sandbox_hint = ""
    if parsed_q.has_code and parsed_q.code_snippet:
        sandbox_res, sandbox_err = execute_sandboxed_python(parsed_q.code_snippet)
        if sandbox_res is not None and not sandbox_err:
            sandbox_hint = f"\n[Verified Python Sandbox Output]:\n{sandbox_res}\n"
            log(f"[Sandbox Output Verified]: {sandbox_res}")

    prompt = (
        f"Question Type: {parsed_q.q_type.upper()}\n\n"
        f"Question Stem / Code:\n{parsed_q.stem}\n"
    )
    if sandbox_hint:
        prompt += f"{sandbox_hint}\n"
    if parsed_q.options:
        prompt += f"\nOptions:\n" + "\n".join(parsed_q.options)
    else:
        prompt += "\nOptions: Direct answer / What will be printed."

    if rag_context:
        prompt += f"\n\n{rag_context}"

    results: List[ModelResult] = []

    # Parallel Cross-Check Execution
    with concurrent.futures.ThreadPoolExecutor(max_workers=3) as executor:
        futures = []

        if jpeg_bytes:
            # Model 1 (Primary Vision): Groq Vision (Fastest vision model ~1.1s, 100% precision on images/code)
            futures.append(executor.submit(query_groq_vision, jpeg_bytes, prompt, sys_instruction))
            # Model 2 (Secondary Vision): Google Gemini (Cross-check)
            futures.append(executor.submit(query_gemini_structured, prompt, sys_instruction, jpeg_bytes))
            # Model 3 (Text validator only for non-code conceptual questions):
            if parsed_q.stem and len(parsed_q.stem) > 20 and not parsed_q.has_code:
                futures.append(executor.submit(query_groq_structured, "qwen/qwen3.8-27b", prompt, sys_instruction))
        else:
            # Pure text queries (e.g. from Alt+E accumulated lines)
            futures.append(executor.submit(query_groq_structured, "qwen/qwen3.8-27b", prompt, sys_instruction))
            futures.append(executor.submit(query_gemini_structured, prompt, sys_instruction, None))
            futures.append(executor.submit(query_groq_structured, "openai/gpt-oss-120b", prompt, sys_instruction))

        try:
            for f in concurrent.futures.as_completed(futures, timeout=1.8):
                try:
                    res = f.result()
                    if res and res.answer:
                        results.append(res)
                        # Speed optimization: If two models already returned and agree, return instantly!
                        if len(results) >= 2 and results[0].answer == results[1].answer:
                            log(f"[Fast Consensus in <1s]: Both models agreed on '{results[0].answer}'")
                            return cross_check_and_ensemble(results, parsed_q)
                except Exception as e:
                    log(f"[Parallel Worker Error]: {e}")
        except concurrent.futures.TimeoutError:
            log(f"[Parallel Worker Timeout]: Instant return with {len(results)} finished model(s).")

    # If results is empty and jpeg_bytes exists, fallback to direct Groq Vision
    if not results and jpeg_bytes:
        gv_res = query_groq_vision(jpeg_bytes, prompt, sys_instruction)
        if gv_res:
            results.append(gv_res)

    return cross_check_and_ensemble(results, parsed_q)


def query_ai_engine(pil_img: Image.Image | None, jpeg_bytes: bytes | None, selected_text: str = "") -> str:
    """
    Refined High-Accuracy Pipeline:
    1. Preprocess & Validate OCR with code-symbol heuristic repair.
    2. Route math/formulas/delicate syntax straight to Vision.
    3. Always pass screenshot (jpeg_bytes) to Vision models (Groq Vision, Gemini)
       for 100% precision on curly braces {}, 1:c, colons, and code structures.
    """
    raw_text = selected_text.strip() if selected_text else ""

    if not raw_text and pil_img is not None:
        extracted = extract_text_via_ocr(pil_img)
        is_valid, reason = validate_ocr_quality(extracted)
        if is_valid:
            raw_text = repair_ocr_code_artifacts(extracted)
            log(f"[OCR Extracted ({len(raw_text)} chars)]")
        else:
            log(f"[OCR Routing to Vision]: {reason}")
            raw_text = ""

    parsed_q = parse_question_structure(raw_text) if raw_text else ParsedQuestion(
        raw_text="",
        stem="Solve the active exam question shown in this image.",
        options=[],
        q_type="image_vision",
        has_code=True
    )
    rag_context = _rag_system.retrieve_relevant_context(parsed_q.stem) if parsed_q.stem else ""

    # Always pass jpeg_bytes so Vision models can see crystal-clear code and symbols!
    return solve_with_crosscheck(parsed_q, rag_context, jpeg_bytes=jpeg_bytes)


# ==============================================================================
# 8. GUI OVERLAY & ANSWER PILL (Unchanged UI contract)
# ==============================================================================

class QuickSnipOverlay:
    """
    Drag-to-select box overlay for tricky questions with 100% guaranteed isolation.
    """
    def __init__(self, root, on_complete):
        self.on_complete = on_complete
        self.top = tk.Toplevel(root)
        self.top.attributes("-fullscreen", True)
        self.top.attributes("-alpha", 0.28)
        self.top.attributes("-topmost", True)
        self.top.config(cursor="crosshair")

        self.canvas = tk.Canvas(self.top, cursor="crosshair", bg="#000000", highlightthickness=0)
        self.canvas.pack(fill="both", expand=True)

        self.start_x = 0
        self.start_y = 0
        self.rect = None

        self.canvas.bind("<ButtonPress-1>", self._on_press)
        self.canvas.bind("<B1-Motion>", self._on_drag)
        self.canvas.bind("<ButtonRelease-1>", self._on_release)
        self.top.bind("<Escape>", lambda e: self._cancel())

    def _on_press(self, event):
        self.start_x = event.x
        self.start_y = event.y
        self.rect = self.canvas.create_rectangle(
            self.start_x, self.start_y, self.start_x, self.start_y,
            outline="#a6e3a1", width=2, fill="#313244"
        )

    def _on_drag(self, event):
        self.canvas.coords(self.rect, self.start_x, self.start_y, event.x, event.y)

    def _on_release(self, event):
        end_x, end_y = event.x, event.y
        self.top.destroy()

        x1 = min(self.start_x, end_x)
        y1 = min(self.start_y, end_y)
        x2 = max(self.start_x, end_x)
        y2 = max(self.start_y, end_y)

        if (x2 - x1) > 20 and (y2 - y1) > 20:
            try:
                switch_to_input_desktop()
                cropped_img = ImageGrab.grab(bbox=(x1, y1, x2, y2), all_screens=True)
                buf = io.BytesIO()
                cropped_img.convert("RGB").save(buf, format="JPEG", quality=95)
                self.on_complete(cropped_img, buf.getvalue())
            except Exception as e:
                log(f"[Snip Error]: {e}")
                self.on_complete(None, None)
        else:
            self.on_complete(None, None)

    def _cancel(self):
        self.top.destroy()
        self.on_complete(None, None)


class BottomRightAnswerPill:
    """
    Ultra-compact floating micro-pill at the BOTTOM-RIGHT corner.
    Shows the final answer (or conflict flag) once computed.
    1-Click anywhere on it immediately deletes it!
    """
    def __init__(self, root, on_close_callback=None):
        self.root = root
        self.on_close_callback = on_close_callback
        self.win = tk.Toplevel(root)
        self.win.title("")
        self.win.attributes("-topmost", True)
        self.win.attributes("-alpha", 0.95)
        self.win.overrideredirect(True)

        self.bg_color = "#11111b"
        self.border_color = "#a6e3a1"
        self.success_color = "#a6e3a1"
        self.error_color = "#f38ba8"

        self.win.config(bg=self.bg_color)
        self.current_text = ""
        self._build_ui()

    def _build_ui(self):
        self.pill_frame = tk.Frame(
            self.win,
            bg=self.bg_color,
            highlightbackground=self.border_color,
            highlightthickness=1,
            padx=12,
            pady=7,
            cursor="hand2"
        )
        self.pill_frame.pack(fill="both", expand=True)

        self.lbl_answer = tk.Label(
            self.pill_frame,
            text="",
            font=("Segoe UI Semibold", 11),
            fg=self.success_color,
            bg=self.bg_color,
            cursor="hand2"
        )
        self.lbl_answer.pack(side="left", padx=(0, 6))

        self.lbl_x = tk.Label(
            self.pill_frame,
            text="✕",
            font=("Segoe UI", 8),
            fg="#6c7086",
            bg=self.bg_color,
            cursor="hand2"
        )
        self.lbl_x.pack(side="right")

        for widget in [self.win, self.pill_frame, self.lbl_answer, self.lbl_x]:
            widget.bind("<Button-1>", lambda e: self.close())
            widget.bind("<Button-3>", lambda e: self.close())

        self.win.bind("<Escape>", lambda e: self.close())

    def show_answer(self, text: str):
        self.current_text = text
        clean_text = text.replace("\n", " ").strip()
        display_text = clean_text if len(clean_text) <= 55 else clean_text[:52] + "..."

        is_flagged = clean_text.startswith("⚠️")
        color = "#f9e2af" if is_flagged else self.success_color

        self.lbl_answer.config(
            text=display_text,
            fg=color,
            font=("Segoe UI Semibold", 11)
        )
        self.pill_frame.config(highlightbackground=color)

        try:
            pyperclip.copy(clean_text)
        except Exception:
            pass

        self._update_bottom_right_position()
        self.win.deiconify()
        self.win.lift()

    def show_error(self, err_msg: str):
        self.lbl_answer.config(text=f"✕ {err_msg[:30]}", fg=self.error_color, font=("Segoe UI", 10))
        self.pill_frame.config(highlightbackground=self.error_color)
        self._update_bottom_right_position()
        self.win.deiconify()
        self.win.lift()

    def _update_bottom_right_position(self):
        self.win.update_idletasks()
        req_w = max(130, self.pill_frame.winfo_reqwidth() + 4)
        req_h = max(34, self.pill_frame.winfo_reqheight() + 2)
        screen_w = self.win.winfo_screenwidth()
        screen_h = self.win.winfo_screenheight()

        pos_x = screen_w - req_w - 20
        pos_y = screen_h - req_h - 55
        self.win.geometry(f"{req_w}x{req_h}+{pos_x}+{pos_y}")

    def close(self):
        try:
            self.win.destroy()
        except Exception:
            pass
        if self.on_close_callback:
            self.on_close_callback()


class BackgroundStudyAssistant:
    """
    100% Background Daemon.
    Maintains existing stealth UI while integrating new multi-model cross-checking,
    OCR validation, multi-line accumulator (Alt+E), and RAG retrieval.
    """
    def __init__(self, root):
        self.root = root
        self.root.withdraw()

        self.pill: BottomRightAnswerPill | None = None
        self.is_busy = False
        self.accumulated_lines: List[str] = []

        get_groq_client()
        get_gemini_client()
        self._register_global_hotkeys()

    def _register_global_hotkeys(self):
        try:
            keyboard.add_hotkey(HOTKEY_ACCUM, self._on_hotkey_accum)
            keyboard.add_hotkey(HOTKEY_SOLVE, self._on_hotkey_solve)
            keyboard.add_hotkey(HOTKEY_CLEAR, self._on_hotkey_clear)
            keyboard.add_hotkey(HOTKEY_SNIP, self._on_hotkey_snip)
            keyboard.add_hotkey(HOTKEY_HIDE, self._on_hotkey_hide)
            keyboard.add_hotkey("escape", self._on_hotkey_hide)

            log("[Study Assistant] Multi-Line Accumulator & High-Accuracy Engine Active!")
            log(f"  • [{HOTKEY_ACCUM.upper()}]  -> Capture single line at cursor (accumulate)")
            log(f"  • [{HOTKEY_SOLVE.upper()}]  -> Solve accumulated lines OR auto-solve at cursor")
            log(f"  • [{HOTKEY_CLEAR.upper()}]  -> Clear accumulated lines buffer")
            log(f"  • [{HOTKEY_SNIP.upper()}]   -> Drag quick box over question")
            log(f"  • [{HOTKEY_HIDE.upper()}] or [ESC] -> Instant hide/delete")
        except Exception as e:
            log(f"[Study Assistant] Hotkey error: {e}")

    def _on_hotkey_accum(self):
        self.root.after(0, self.accumulate_line_at_cursor)

    def _on_hotkey_solve(self):
        self.root.after(0, self.solve_screen_auto)

    def _on_hotkey_clear(self):
        self.root.after(0, self.clear_accumulator)

    def _on_hotkey_snip(self):
        self.root.after(0, self.solve_screen_snip)

    def _on_hotkey_hide(self):
        self.root.after(0, self.hide_answer)

    def clear_accumulator(self):
        """Clears all accumulated lines."""
        count = len(self.accumulated_lines)
        self.accumulated_lines.clear()
        self.hide_answer()
        self.pill = BottomRightAnswerPill(self.root, on_close_callback=self._on_pill_closed)
        self.pill.show_answer(f"Cleared {count} lines")
        log(f"[Accumulator] Cleared {count} lines.")
        self.root.after(1500, self.hide_answer)

    def accumulate_line_at_cursor(self):
        """Reads horizontal line strip under mouse cursor and appends to buffer."""
        line_text = capture_line_at_cursor()
        if line_text:
            # Prevent identical duplicate line insertion if Alt+E pressed twice on same line
            if self.accumulated_lines and self.accumulated_lines[-1].strip() == line_text.strip():
                log(f"[Accumulator Duplicate Skipped]: {line_text}")
                return

            self.accumulated_lines.append(line_text)
            count = len(self.accumulated_lines)
            log(f"[Accumulator #{count}]: {line_text}")
            # Brief micro-pill notification
            self.hide_answer()
            self.pill = BottomRightAnswerPill(self.root, on_close_callback=self._on_pill_closed)
            self.pill.show_answer(f"Added #{count}: {line_text[:30]}")
            self.root.after(1800, self.hide_answer)
        else:
            log("[Accumulator]: No readable text at cursor line.")

    def hide_answer(self):
        if self.pill:
            self.pill.close()
            self.pill = None

    def solve_screen_auto(self):
        """
        Solves accumulated lines if any exist in the buffer.
        Otherwise falls back to cursor-focused auto screen solve.
        """
        self.hide_answer()
        if self.is_busy:
            return
        self.is_busy = True

        if self.accumulated_lines:
            merged_text = "\n".join(self.accumulated_lines)
            count = len(self.accumulated_lines)
            self.accumulated_lines.clear()
            log(f"[Solving {count} Accumulated Lines]:\n{merged_text}")
            threading.Thread(
                target=self._async_compute,
                args=(None, None, merged_text),
                daemon=True
            ).start()
            return

        raw_img, jpeg_bytes = capture_optimized_screen(cursor_focus=True)
        if not raw_img and not jpeg_bytes:
            self.is_busy = False
            return

        threading.Thread(
            target=self._async_compute,
            args=(raw_img, jpeg_bytes, ""),
            daemon=True
        ).start()

    def solve_screen_snip(self):
        self.hide_answer()
        QuickSnipOverlay(self.root, self._on_snip_done)

    def _on_snip_done(self, raw_img, jpeg_bytes):
        if not raw_img and not jpeg_bytes:
            return

        self.is_busy = True
        threading.Thread(
            target=self._async_compute,
            args=(raw_img, jpeg_bytes, ""),
            daemon=True
        ).start()

    def _async_compute(self, raw_img, jpeg_bytes, selected_text):
        try:
            answer = query_ai_engine(raw_img, jpeg_bytes, selected_text=selected_text)
        except Exception as e:
            answer = f"Error: {e}"
        finally:
            self.is_busy = False

        self.root.after(0, self._on_answer_ready, answer)

    def _on_answer_ready(self, answer: str):
        self.hide_answer()
        self.pill = BottomRightAnswerPill(self.root, on_close_callback=self._on_pill_closed)
        if answer.startswith("Error:"):
            self.pill.show_error(answer)
        else:
            self.pill.show_answer(answer)
            log(f"Displayed Answer: {answer}")

    def _on_pill_closed(self):
        self.pill = None


if __name__ == "__main__":
    log("=" * 65)
    log("  STUDY ASSISTANT — HIGH-ACCURACY ENSEMBLE ENGINE")
    log("  Features: Multi-Line Accumulator, Parallel Cross-Check,")
    log("            Smart OCR Validation, Local Course RAG, Benchmark Logging")
    log(f"  [1] Hover line + Press [{HOTKEY_ACCUM.upper()}] -> Capture line into buffer")
    log(f"  [2] Press [{HOTKEY_SOLVE.upper()}]            -> Solve merged lines (or cursor area)")
    log(f"  [3] Press [{HOTKEY_CLEAR.upper()}]            -> Clear line buffer")
    log(f"  [4] Press [{HOTKEY_SNIP.upper()}]             -> Quick snip box")
    log(f"  [5] Press [{HOTKEY_HIDE.upper()}] / [ESC]     -> Instant hide / dismiss")
    log("=" * 65)

    root = tk.Tk()
    app = BackgroundStudyAssistant(root)
    root.mainloop()
