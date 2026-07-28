#!/usr/bin/env bash
# Generate the deck's illustrations via cliproxyapi (gpt-image-2) and save as PNG.
# Usage:  with-secret.sh cliproxyapi_api_key --env IMG_API_KEY -- ./gen-images.sh
set -uo pipefail
HERE="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
OUT="$HERE/img"; mkdir -p "$OUT"
URL="${IMG_BASE_URL:-http://127.0.0.1:8317/v1}"
: "${IMG_API_KEY:?set IMG_API_KEY}"

# name|size|prompt   — never ask for text in the image; models render it as gibberish
GENS=(
"hero|1536x1024|A minimalist editorial illustration: a single magnifying glass revealing one glowing clue line inside a vast dark river of streaming code-like texture. Deep indigo, teal and warm amber accent. Thin geometric linework, subtle film grain, generous negative space. Absolutely no words, no letters, no numbers, no glyphs. Flat modern tech-editorial poster style."
"formats|1536x1024|A minimalist editorial illustration: many differently-shaped abstract ribbons of data - some striped, some dotted, some blocky, each a different texture and colour - all converging and merging into one single clean unified beam of light. Deep indigo background, teal and amber accents, thin geometric linework, subtle grain. No words, no letters, no numbers, no glyphs. Flat modern tech-editorial style."
"coverage|1536x1024|A minimalist editorial illustration: a dark grid of many closed identical doors, where only a few are open and lit with warm amber light spilling out, while most stay shut in shadow. Sense of something important missed behind the closed ones. Deep indigo and teal, thin geometric linework, subtle grain, generous negative space. No words, no letters, no numbers, no glyphs. Flat modern tech-editorial style."
)

for g in "${GENS[@]}"; do
  IFS='|' read -r name size prompt <<<"$g"
  echo "▶ $name"
  python3 - "$URL" "$IMG_API_KEY" "$name" "$size" "$prompt" "$OUT" <<'PY'
import base64, json, sys, urllib.request, os
url, key, name, size, prompt, out = sys.argv[1:7]
body = json.dumps({"model": os.environ.get("IMG_MODEL", "gpt-image-2"),
                   "prompt": prompt, "size": size, "n": 1}).encode()
req = urllib.request.Request(url.rstrip("/") + "/images/generations", data=body,
        headers={"Authorization": "Bearer " + key, "Content-Type": "application/json",
                 "User-Agent": "sherlock-deck/1.0"})
try:
    with urllib.request.urlopen(req, timeout=600) as r:
        d = json.load(r)
    b64 = d["data"][0].get("b64_json")
    if not b64:
        print("  ! no b64 in response"); sys.exit(1)
    p = os.path.join(out, name + ".png")
    open(p, "wb").write(base64.b64decode(b64))
    print("  ✓ %s (%d KB)" % (p, os.path.getsize(p) // 1024))
except Exception as e:
    print("  ✗ %s: %s" % (name, e))
PY
done
