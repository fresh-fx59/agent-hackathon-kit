#!/usr/bin/env bash
# Illustrations for the PITCH deck (pitch.html) via cliproxyapi (gpt-image-2).
# Usage:  with-secret.sh cliproxyapi_api_key --env IMG_API_KEY -- ./gen-images-pitch.sh
#
# NOTE, learned on this project: never ask the model for text in the image — it
# renders Cyrillic as gibberish. Every labelled diagram in the deck is inline SVG;
# these PNGs are atmosphere only, generated in the deck palette
# (deep indigo #050A12 / teal / amber #F2A63B) so they sit next to img/*.png.
set -uo pipefail
HERE="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
OUT="$HERE/img"; mkdir -p "$OUT"
URL="${IMG_BASE_URL:-http://127.0.0.1:8317/v1}"
: "${IMG_API_KEY:?set IMG_API_KEY}"

# name|size|prompt
GENS=(
"p-problem|1536x1024|A minimalist editorial illustration: a lone small figure standing at the foot of an immense towering wall built of dense horizontal striated data bands, the wall stretching far beyond the frame, oppressive scale, one faint warm amber glow buried deep inside the mass. Deep indigo background, teal midtones, warm amber accent. Thin geometric linework, subtle film grain, generous negative space. Absolutely no words, no letters, no numbers, no glyphs. Flat modern tech-editorial poster style."
"p-funnel|1536x1024|A minimalist editorial illustration: an enormous heavy cloud of countless dark particles being drawn through a narrow elegant aperture and emerging on the other side as a single small brilliant amber shard, dramatic reduction of volume, sense of distillation and lightness. Deep indigo background, teal midtones, warm amber accent. Thin geometric linework, subtle grain, generous negative space. Absolutely no words, no letters, no numbers, no glyphs. Flat modern tech-editorial poster style."
"p-universal|1536x1024|A minimalist editorial illustration: one glowing amber cube at the centre snapping cleanly into several different abstract host structures arranged around it, each host a distinct geometric silhouette, connected by thin light filaments, sense of universal fit and portability. Deep indigo background, teal midtones, warm amber accent. Thin geometric linework, subtle grain, generous negative space. Absolutely no words, no letters, no numbers, no glyphs. Flat modern tech-editorial poster style."
"p-aiops|1536x1024|A minimalist editorial illustration: a remote dark server silhouette on a distant horizon linked by a long thin luminous thread to a calm circular pulse monitor in the foreground, repeating concentric rings suggesting a steady heartbeat over time, night atmosphere. Deep indigo background, teal midtones, warm amber accent. Thin geometric linework, subtle grain, generous negative space. Absolutely no words, no letters, no numbers, no glyphs. Flat modern tech-editorial poster style."
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
echo GEN-PITCH-DONE
