from pathlib import Path
from datetime import datetime
import subprocess
import sys
import re
from collections import defaultdict

# ============================================================
# CONFIG
# ============================================================

ROOT_DIR = Path(__file__).parent.resolve()
OUTPUT_FILE = ROOT_DIR / "project_dump.txt"
MAX_FILE_SIZE_MB = 2

ALLOWED_EXTENSIONS = {
    ".py", ".json", ".yaml", ".yml", ".ini", ".env", ".txt", ".sh", ".bat", ".md"
}

EXCLUDED_DIRS = {
    "venv", ".venv", "env", ".git", "__pycache__",
    "node_modules", ".idea", ".vscode",
    "build", "dist", ".pytest_cache", ".mypy_cache",
    ".ruff_cache", ".tox", ".cache", "coverage",
    ".coverage", "log", "logs"
}

EXCLUDED_FILES = {"project_dump.txt"}

SEQUENCE_COLLAPSE_THRESHOLD = 5

IMAGE_EXTS = {".jpg", ".jpeg", ".png", ".gif", ".bmp", ".webp", ".tiff", ".ico", ".svg"}
AUDIO_EXTS = {".mp3", ".wav", ".ogg", ".flac", ".aac", ".m4a", ".wma"}
VIDEO_EXTS = {".mp4", ".avi", ".mkv", ".mov", ".wmv", ".flv", ".webm", ".m4v"}

# ============================================================
# UTILS
# ============================================================

def is_excluded(path: Path) -> bool:
    return any(part in EXCLUDED_DIRS for part in path.parts)


def is_log_file(path: Path) -> bool:
    """Identifica file di log senza colpire file come dialog.py o login.py"""
    name_lower = path.name.lower()
    return (
        path.suffix.lower() == ".log" or
        name_lower in {"log.txt", "logs.txt"} or
        name_lower.endswith(".log.txt")
    )


def get_media_type(ext: str) -> str | None:
    ext_lower = ext.lower()
    if ext_lower in IMAGE_EXTS:
        return "immagine"
    if ext_lower in AUDIO_EXTS:
        return "audio"
    if ext_lower in VIDEO_EXTS:
        return "video"
    return None


def should_include(path: Path) -> bool:
    if is_excluded(path):
        return False
    if path.name in EXCLUDED_FILES:
        return False
    if is_log_file(path):
        return False
    if path.name == ".env":
        return True
    return path.suffix.lower() in ALLOWED_EXTENSIONS


# ============================================================
# ALBERO CON COLLASSO SEQUENZE
# ============================================================

def detect_sequences(files: list[Path]):
    """Rileva sequenze numerate di file media per collassarle."""
    pattern = re.compile(r"^(.*[^0-9])?(\d+)((?:[-_ ][^.]+)?)(\.[^.]+)$")
    groups = defaultdict(list)

    for f in files:
        match = pattern.match(f.name)
        if not match:
            continue
        prefix, number, suffix, ext = match.groups()
        prefix = prefix or ""
        suffix = suffix or ""
        has_suffix = bool(suffix)

        groups[(prefix, ext, has_suffix)].append(
            (int(number), len(number), f, suffix)
        )
    return groups


def generate_tree(root: Path):
    lines = []

    def walk(path: Path, prefix=""):
        try:
            all_entries = sorted(
                [e for e in path.iterdir()
                 if not is_excluded(e) and e.name not in EXCLUDED_FILES and not is_log_file(e)],
                key=lambda x: (x.is_file(), x.name.lower())
            )
        except PermissionError:
            return

        files = [e for e in all_entries if e.is_file()]
        dirs = [e for e in all_entries if e.is_dir()]

        # Collassa sequenze media
        sequences = detect_sequences(files)
        consumed = set()
        compressed = []

        for (prefix_name, ext, has_suffix), items in sequences.items():
            if len(items) <= SEQUENCE_COLLAPSE_THRESHOLD:
                continue

            media_type = get_media_type(ext)
            if not media_type:
                continue

            numbers = sorted(x[0] for x in items)
            digits = items[0][1]
            first_num = str(numbers[0]).zfill(digits)
            last_num = str(numbers[-1]).zfill(digits)

            total_size = sum(x[2].stat().st_size for x in items)
            size_mb = total_size / (1024 * 1024)

            pattern_str = prefix_name + ("X" * digits) + ("*" if has_suffix else "") + ext

            compressed.append(
                "[" + str(len(items)) + " " + media_type.upper() + " " + ext.upper().replace(".", "") +
                " | " + f"{size_mb:.1f}" + " MB] " +
                "pattern: " + pattern_str + ", range: " + first_num + " -> " + last_num
            )

            for _, _, file_obj, _ in items:
                consumed.add(file_obj)

        normal_files = [f for f in files if f not in consumed]
        all_elements = dirs + normal_files

        # Stampa elementi compressi
        for comp in compressed:
            connector = "├── "
            lines.append(prefix + connector + comp)

        # Stampa directory e file normali
        for i, entry in enumerate(all_elements):
            is_last = (i == len(all_elements) - 1)
            connector = "└── " if is_last else "├── "
            next_prefix = prefix + ("    " if is_last else "│   ")

            if entry.is_dir():
                lines.append(prefix + connector + entry.name)
                walk(entry, next_prefix)
            else:
                size_mb = entry.stat().st_size / (1024 * 1024)
                if entry.suffix.lower() in ALLOWED_EXTENSIONS:
                    lines.append(prefix + connector + entry.name)
                else:
                    lines.append(prefix + connector + entry.name + " (" + f"{size_mb:.2f}" + " MB - Escluso)")

    lines.append(root.name)
    walk(root)
    return "\n".join(lines)


# ============================================================
# SCANSIONE FILE
# ============================================================

print("Scansione file in corso...")

files = [
    f for f in ROOT_DIR.rglob("*")
    if f.is_file() and should_include(f)
]

files.sort()

py_files = [f for f in files if f.suffix == ".py"]
print("   File Python trovati:", len(py_files))
print("   Totale file inclusi:", len(files))

# ============================================================
# SCRITTURA OUTPUT
# ============================================================

dump_text = []

dump_text.append("=" * 120)
dump_text.append("PROJECT DUMP")
dump_text.append("Data: " + str(datetime.now()))
dump_text.append("Root: " + str(ROOT_DIR))
dump_text.append("File totali: " + str(len(files)) + " | File Python: " + str(len(py_files)))
dump_text.append("=" * 120 + "\n")

dump_text.append("STRUTTURA PROGETTO")
dump_text.append("-" * 120)
dump_text.append(generate_tree(ROOT_DIR))
dump_text.append("\n")

dump_text.append("INDICE FILE")
dump_text.append("-" * 120)

for i, f in enumerate(files, 1):
    size_mb = f.stat().st_size / (1024 * 1024)
    marker = " [PY]" if f.suffix == ".py" else ""
    dump_text.append(f"{i:04d} | {f.relative_to(ROOT_DIR)} ({size_mb:.2f} MB){marker}")

dump_text.append("\n")

# CONTENUTI
for i, f in enumerate(files, 1):
    rel = f.relative_to(ROOT_DIR)
    size_mb = f.stat().st_size / (1024 * 1024)

    dump_text.append("#" * 120)
    dump_text.append("FILE: " + f.name)
    dump_text.append("PATH: " + str(rel))
    dump_text.append("SIZE: " + f"{size_mb:.2f}" + " MB")
    dump_text.append("#" * 120 + "\n")

    try:
        if size_mb > MAX_FILE_SIZE_MB:
            dump_text.append("[SKIPPED - " + f"{size_mb:.2f}" + " MB > " + str(MAX_FILE_SIZE_MB) + " MB]\n")
            continue

        try:
            content = f.read_text(encoding="utf-8", errors="replace")
        except Exception:
            content = f.read_text(encoding="latin-1", errors="replace")

        lang = f.suffix.lstrip(".").lower()
        if lang == "yml":
            lang = "yaml"
        elif lang == "env":
            lang = "properties"
        elif not lang:
            lang = "text"

        dump_text.append("```" + lang)
        dump_text.append(content)
        if not content.endswith("\n"):
            dump_text.append("")
        dump_text.append("```\n")

    except Exception as e:
        dump_text.append("[ERROR] " + str(e) + "\n")


final_text = "\n".join(dump_text)

# Salva file
OUTPUT_FILE.write_text(final_text, encoding="utf-8")


# ============================================================
# COPIA NEGLI APPUNTI (WSL + Linux)
# ============================================================

def copy_to_clipboard(text: str):
    try:
        subprocess.run("clip.exe", input=text.encode(), check=True)
        return True
    except Exception:
        try:
            subprocess.run("xclip -selection clipboard", input=text.encode(), shell=True, check=True)
            return True
        except Exception:
            return False


print("\n" + "=" * 60)
print("DONE")
print("File: " + str(OUTPUT_FILE))
print("File Python inclusi: " + str(len(py_files)))
print("Dimensione dump: " + f"{len(final_text) / 1024 / 1024:.2f}" + " MB")

if len(final_text) < 10 * 1024 * 1024:  # Copia solo se < 10MB
    if copy_to_clipboard(final_text):
        print("Contenuto copiato negli appunti")
    else:
        print("Clipboard non disponibile")
else:
    print("Dump troppo grande per clipboard (>10MB)")

print("=" * 60)
