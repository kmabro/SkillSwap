import re
from pathlib import Path

import requests

ROOT = Path(__file__).resolve().parents[1]
MD_PATH = ROOT / "docs" / "er-diagram.md"
OUT_SVG = ROOT / "docs" / "er-diagram-clear.svg"
OUT_PNG = ROOT / "docs" / "er-diagram-clear.png"
OUT_JPG = ROOT / "docs" / "er-diagram-clear.jpg"

md = MD_PATH.read_text(encoding="utf-8")
match = re.search(r"```mermaid\s*(.*?)\s*```", md, re.S)
if not match:
    raise SystemExit("Mermaid block not found in docs/er-diagram.md")

base_mermaid = match.group(1).strip()

# Force a clearer render profile for exports.
init = (
    "%%{init: {"
    "'theme': 'default', "
    "'themeVariables': {"
    "'fontFamily': 'Segoe UI, Arial, sans-serif', "
    "'fontSize': '18px', "
    "'lineColor': '#111827', "
    "'primaryTextColor': '#111827', "
    "'secondaryTextColor': '#111827', "
    "'tertiaryColor': '#ffffff'"
    "}, "
    "'er': {'layoutDirection': 'TB'}"
    "}}%%"
)
mermaid = init + "\n" + base_mermaid

svg_resp = requests.post("https://kroki.io/mermaid/svg", data=mermaid.encode("utf-8"), timeout=40)
svg_resp.raise_for_status()
svg_text = svg_resp.content.decode("utf-8", errors="ignore")

# Improve visibility of relationships in exported SVG.
injected = (
    "<style>"
    ".er.relationshipLine{stroke:#111827 !important;stroke-width:2.4px !important;opacity:1 !important;}"
    ".er.relationshipLabelBox{fill:#ffffff !important;opacity:1 !important;}"
    ".er.relationshipLabel{fill:#111827 !important;font-weight:600 !important;}"
    ".er.entityBox{stroke:#334155 !important;stroke-width:1.5px !important;}"
    "text,tspan{fill:#111827 !important;}"
    "</style>"
    "<rect width='100%' height='100%' fill='#ffffff'/>"
)
svg_text = re.sub(r"(<svg[^>]*>)", r"\1" + injected, svg_text, count=1, flags=re.S)
OUT_SVG.write_text(svg_text, encoding="utf-8")

# Use high scale for a sharper PNG export.
png_resp = requests.post("https://kroki.io/mermaid/png?scale=4", data=mermaid.encode("utf-8"), timeout=40)
png_resp.raise_for_status()
OUT_PNG.write_bytes(png_resp.content)

try:
    from PIL import Image
except Exception:
    import subprocess
    import sys
    subprocess.check_call([sys.executable, "-m", "pip", "install", "Pillow", "-q"])
    from PIL import Image

img = Image.open(OUT_PNG).convert("RGB")
img.save(OUT_JPG, format="JPEG", quality=96, optimize=True)

print("Exported:")
print(OUT_SVG)
print(OUT_PNG)
print(OUT_JPG)
