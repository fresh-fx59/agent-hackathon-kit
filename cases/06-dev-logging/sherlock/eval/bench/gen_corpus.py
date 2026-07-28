#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
gen_corpus.py -- deterministic, seeded generator for a large heterogeneous log corpus.

Stdlib only. Single-threaded, fixed RNG call order => byte-identical output for a
given (SEED, CORPUS_SCALE).

Scenario
--------
Fictional company "ACME Shop", Kubernetes cluster prod-eu-1, 2026-07-28 09:00-16:00 UTC.
A multi-hop production incident is embedded in ~25 log files across ~20 formats, together
with two red herrings and several independent defects.

Env knobs
---------
  CORPUS_SCALE   float, multiplies every byte target (default 1.0). Use 0.02 for a fast
                 determinism smoke test.
  CORPUS_OUT     output corpus dir
  CORPUS_KEY     answer-key dir (MUST be outside CORPUS_OUT)

Usage:  python3 gen_corpus.py
"""

import calendar
import gzip
import io
import json
import os
import random
import shutil
import sys
import time

SEED = 20260728
SCALE = float(os.environ.get("CORPUS_SCALE", "1.0"))
BASE = "/tmp/claude-1000/-home-claude-developer-personal-os/6247eb6b-45c7-4309-abd2-5e2043ec2fd0/scratchpad"
ROOT = os.environ.get("CORPUS_OUT", os.path.join(BASE, "hetero-corpus"))
KEYDIR = os.environ.get("CORPUS_KEY", os.path.join(BASE, "hetero-answer-key"))

rnd = random.Random(SEED)

MB = 1024 * 1024

# --------------------------------------------------------------------------------------
# time helpers
# --------------------------------------------------------------------------------------
MON = ["Jan", "Feb", "Mar", "Apr", "May", "Jun", "Jul", "Aug", "Sep", "Oct", "Nov", "Dec"]


def ep(y, mo, d, h, mi, s):
    return calendar.timegm((y, mo, d, h, mi, s, 0, 0, 0))


W0 = ep(2026, 7, 28, 9, 0, 0)     # window start (UTC)
W1 = ep(2026, 7, 28, 16, 0, 0)    # window end   (UTC)
BOOT_A = ep(2026, 7, 25, 4, 11, 3)   # node-a boot (for dmesg monotonic clock)
BOOT_B = ep(2026, 7, 26, 21, 47, 19)  # node-b boot


def T(h, m, s=0, ms=0):
    return ep(2026, 7, 28, h, m, s) + ms / 1000.0


_tc = {}


def parts(ts):
    i = int(ts)
    p = _tc.get(i)
    if p is None:
        g = time.gmtime(i)
        p = (g.tm_year, g.tm_mon, g.tm_mday, g.tm_hour, g.tm_min, g.tm_sec, MON[g.tm_mon - 1])
        _tc[i] = p
    return p


def _ms(ts):
    return min(999, int((ts - int(ts)) * 1000 + 0.5))


def iso_ms(ts):
    y, mo, d, h, mi, s, _ = parts(ts)
    return "%04d-%02d-%02dT%02d:%02d:%02d.%03dZ" % (y, mo, d, h, mi, s, _ms(ts))


def logback(ts):
    y, mo, d, h, mi, s, _ = parts(ts)
    return "%04d-%02d-%02d %02d:%02d:%02d.%03d" % (y, mo, d, h, mi, s, _ms(ts))


def comma_ts(ts):
    y, mo, d, h, mi, s, _ = parts(ts)
    return "%04d-%02d-%02d %02d:%02d:%02d,%03d" % (y, mo, d, h, mi, s, _ms(ts))


def pg_ts(ts):
    y, mo, d, h, mi, s, _ = parts(ts)
    return "%04d-%02d-%02d %02d:%02d:%02d.%03d UTC" % (y, mo, d, h, mi, s, _ms(ts))


def sysl(ts):
    y, mo, d, h, mi, s, mon = parts(ts)
    return "%s %2d %02d:%02d:%02d" % (mon, d, h, mi, s)


def clf(ts, off=0):
    y, mo, d, h, mi, s, mon = parts(ts + off)
    sign = "+" if off >= 0 else "-"
    a = abs(int(off)) // 60
    return "%02d/%s/%04d:%02d:%02d:%02d %s%02d%02d" % (d, mon, y, h, mi, s, sign, a // 60, a % 60)


def nano(ts):
    y, mo, d, h, mi, s, _ = parts(ts)
    frac = int((ts - int(ts)) * 1e9) % 1000000000
    return "%04d-%02d-%02dT%02d:%02d:%02d.%09dZ" % (y, mo, d, h, mi, s, frac)


def ems(ts):
    return int(ts * 1000)


def us(ts):
    return int(ts * 1000000)


def promo_ts(ts, off=10800):
    """in-house promo-engine bespoke stamp:  20260728|173302.144|+0300"""
    y, mo, d, h, mi, s, _ = parts(ts + off)
    return "%04d%02d%02d|%02d%02d%02d.%03d|+0300" % (y, mo, d, h, mi, s, _ms(ts))


def ru_ts(ts, off=10800):
    y, mo, d, h, mi, s, _ = parts(ts + off)
    return "%02d.%02d.%04d %02d:%02d:%02d,%03d" % (d, mo, y, h, mi, s, _ms(ts))


# --------------------------------------------------------------------------------------
# pools
# --------------------------------------------------------------------------------------
def _uuid():
    return "%08x-%04x-4%03x-%04x-%012x" % (
        rnd.getrandbits(32), rnd.getrandbits(16), rnd.getrandbits(12),
        0x8000 | rnd.getrandbits(14), rnd.getrandbits(48))


RIDS = [_uuid() for _ in range(4096)]
CLIENT_IPS = ["10.42.%d.%d" % (rnd.randrange(1, 40), rnd.randrange(2, 250)) for _ in range(2048)]
PUB_IPS = ["%d.%d.%d.%d" % (rnd.randrange(3, 223), rnd.randrange(0, 255),
                            rnd.randrange(0, 255), rnd.randrange(1, 254)) for _ in range(1024)]
SKUS = ["SKU-%05d" % rnd.randrange(10000, 99999) for _ in range(3000)]
# orders that carry planted evidence must NEVER be produced by the noise generators,
# otherwise the answer key's "this order appears here and nowhere else" claims break.
RESERVED_ORDERS = set(["ORD-88231", "ORD-88377", "ORD-88402", "ORD-88407", "ORD-88411",
                       "ORD-87104", "ORD-88214"] + ["ORD-88%03d" % n for n in range(240, 251)])
ORDERS = [o for o in ("ORD-%05d" % n for n in range(87000, 89400)) if o not in RESERVED_ORDERS]
USER_AGENTS = [
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/141.0.0.0 Safari/537.36",
    "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/605.1.15 (KHTML, like Gecko) Version/18.2 Safari/605.1.15",
    "Mozilla/5.0 (iPhone; CPU iPhone OS 18_3 like Mac OS X) AppleWebKit/605.1.15 (KHTML, like Gecko) Mobile/15E148",
    "acme-shop-android/7.4.1 (okhttp/4.12.0)",
    "acme-shop-ios/7.4.0 (CFNetwork/1494.0.7 Darwin/24.0.0)",
    "python-requests/2.32.3",
    "Go-http-client/2.0",
]
PROBE_UAS = ["kube-probe/1.29", "Prometheus/2.54.1", "blackbox_exporter/0.25.0", "ELB-HealthChecker/2.0"]
APP_PATHS = ["/api/v1/catalog/search", "/api/v1/catalog/item", "/api/v1/cart", "/api/v1/cart/items",
             "/api/v1/checkout", "/api/v1/orders", "/api/v1/orders/status", "/api/v1/promo/apply",
             "/api/v1/inventory/reserve", "/api/v1/user/profile", "/api/v1/recommendations",
             "/static/js/app.9f2a1c.js", "/static/css/main.3b81de.css", "/api/v1/shipping/quote"]
PROBE_PATHS = ["/healthz", "/healthz", "/healthz", "/ready", "/livez", "/metrics", "/-/healthy"]

PODS = {
    "checkout-api": ["checkout-api-5f8b9c7d4-r9h2v", "checkout-api-5f8b9c7d4-t4k8p", "checkout-api-5f8b9c7d4-x7m1c"],
    "catalog-svc": ["catalog-svc-6b7d9c4f8-q2w9e", "catalog-svc-6b7d9c4f8-l8n3r"],
    "inventory-svc": ["inventory-svc-7d9c4b8f6-2xq7z", "inventory-svc-7d9c4b8f6-8hf4d",
                      "inventory-svc-7d9c4b8f6-b1v6s", "inventory-svc-7d9c4b8f6-k9p2w",
                      "inventory-svc-7d9c4b8f6-m3r7t", "inventory-svc-7d9c4b8f6-z5c8y"],
    "payments-worker": ["payments-worker-849fb6cd7-nn4jq", "payments-worker-849fb6cd7-vv7bz"],
    "notify-svc": ["notify-svc-6c8d7f9b4-mn2kq", "notify-svc-6c8d7f9b4-p6s1x"],
    "promo-engine": ["promo-engine-5b6c7d8e9-w4t2m"],
    "ordersync": ["ordersync-cron-28921456-4dqlp"],
}

# ---- the ORD-88231 request chain: the SAME logical id, spelled 5 different ways -------
RID_881 = "9f2c1d7e-4b6a-4c31-8e55-1a2b3c4d5e6f"    # X-Request-ID / request_id (edge, envoy)
RID_881_J = "9f2c1d7e4b6a4c318e551a2b3c4d5e6f"       # traceId (java MDC, dashes stripped)
RID_881_S = "9f2c1d7e4b6a"                            # RID~ (in-house promo, truncated to 12)
ORD_881 = "ORD-88231"
PSP_881 = "PSP-4471902"

# storm-window ids used by the 503 chain
RID_STORM = ["3b91c0d5-77aa-4e18-9c02-51ff2ad9e410", "c41d77e9-2b30-4a56-8f61-90cd3e7b1a22",
             "5e8a1f34-9d62-4c7b-b0e3-77aa12cf9d08"]

PROBE_IPS = ["10.42.0.1", "10.42.0.2", "10.42.1.1", "10.42.1.2", "10.42.9.14", "10.42.9.15"]
AMOUNTS = [990, 1490, 2390, 3990, 4990, 7990, 9990, 12990, 19900, 24900, 39900, 59900, 129900,
           189900, 249900, 399900, 649900, 1299900]


def lat_ms(base=6.0, span=120.0, p=5):
    """right-skewed latency: p50 ~ base+span/2**p, p99 ~ base+span"""
    return int(base + (rnd.random() ** p) * span)


def order_at(t):
    """order ids advance through the day, with local jitter"""
    frac = (t - W0) / float(W1 - W0)
    idx = int(frac * (len(ORDERS) - 80)) + rnd.randrange(0, 80)
    return ORDERS[max(0, min(len(ORDERS) - 1, idx))]


PROOFS = []


def proof(defect, path, l0, l1, note):
    PROOFS.append({"defect": defect, "file": path, "line_start": l0, "line_end": l1, "note": note})


# --------------------------------------------------------------------------------------
# writer
# --------------------------------------------------------------------------------------
class LogFile(object):
    def __init__(self, rel, gz=False):
        self.rel = rel
        full = os.path.join(ROOT, rel)
        d = os.path.dirname(full)
        if d:
            os.makedirs(d, exist_ok=True)
        self._extra = []
        if gz:
            # mtime=0 so the gzip header is byte-stable across runs
            raw = open(full, "wb")
            gzf = gzip.GzipFile(filename=os.path.basename(full)[:-3], mode="wb",
                                compresslevel=6, fileobj=raw, mtime=0)
            self.f = io.TextIOWrapper(gzf, encoding="utf-8", newline="\n")
            self._extra = [gzf, raw]
        else:
            self.f = open(full, "w", encoding="utf-8", newline="\n")
        self.lineno = 0
        self.buf = []
        self.nbytes = 0

    def w(self, line):
        self.lineno += 1
        self.buf.append(line)
        self.nbytes += len(line) + 1
        if len(self.buf) >= 20000:
            self.f.write("\n".join(self.buf))
            self.f.write("\n")
            del self.buf[:]

    def wb(self, lines):
        for ln in lines:
            self.w(ln)

    def mark(self, defect, lines, note):
        if isinstance(lines, str):
            lines = [lines]
        a = self.lineno + 1
        self.wb(lines)
        proof(defect, self.rel, a, self.lineno, note)

    def close(self, partial=None):
        if self.buf:
            self.f.write("\n".join(self.buf))
            self.f.write("\n")
            del self.buf[:]
        if partial is not None:
            self.f.write(partial)   # deliberately no trailing newline: truncated tail
            self.lineno += 1
            self.nbytes += len(partial)
        self.f.close()
        for h in self._extra:
            h.close()
        return self


def run_bytes(lf, target, noise, specials, t0=W0, t1=W1):
    """Fill lf up to `target` uncompressed bytes with noise(t, i), splicing `specials`
    (list of dicts: t, id, note, lines) at their proportional time position."""
    sp = sorted(specials, key=lambda s: s["t"])
    si = 0
    i = 0
    span = float(t1 - t0)
    while lf.nbytes < target:
        frac = lf.nbytes / float(target)
        t = t0 + span * frac
        while si < len(sp) and sp[si]["t"] <= t:
            s = sp[si]
            lf.mark(s["id"], s["lines"], s["note"])
            si += 1
        out = noise(t, i)
        if type(out) is list:
            lf.wb(out)
        else:
            lf.w(out)
        i += 1
    while si < len(sp):
        s = sp[si]
        lf.mark(s["id"], s["lines"], s["note"])
        si += 1
    return lf


def sp(t, did, note, lines):
    return {"t": t, "id": did, "note": note, "lines": lines if type(lines) is list else [lines]}


# --------------------------------------------------------------------------------------
# incident timeline constants (UTC)
# --------------------------------------------------------------------------------------
T_HERRING = T(8, 12, 4)        # (before window) node-b SYN flood / conntrack  -- RED HERRING
T_DEPLOY = T(11, 0, 41)        # catalog-svc 4.7.2 rollout
T_P99 = T(11, 5, 0)            # catalog p99 shift begins
T_RELOAD = T(12, 58, 11)       # admin config reload needles begin (gz side)
T_RELOAD2 = T(13, 1, 2)        # ... continue after rotation (plain side)
T_SLOW = T(13, 10, 22)         # postgres slow queries
T_DEADLOCK = T(13, 22, 41)     # postgres deadlock
T_POOL = T(13, 25, 41)         # inventory pool exhaustion
T_SSH = T(13, 29, 44)          # unauthorized ssh
T_SCALE = T(13, 31, 2)         # unauthorized kubectl scale 6 -> 2
T_OOM = T(13, 40, 12)          # OOMKill
T_503 = T(13, 41, 7)           # 503 storm starts
T_503_END = T(14, 22, 0)
T_RETENTION = T(13, 52, 3)     # kafka topic retention override pushed
T_REBAL = T(14, 2, 11)         # kafka consumer group rebalance
T_PAY = T(14, 1, 58)           # ORD-88231 captured
T_RESET = T(14, 3, 27)         # notify-svc offset reset -> 2867 records skipped
T_GOPANIC = T(14, 5, 12)       # go panic
T_NPE = T(14, 12, 33)          # java NPE
T_SYNC = T(14, 6, 3)           # ordersync reconciliation warn
T_PROMO = T(14, 33, 2)         # promo discount blow-up
T_FX = T(14, 40, 11)           # russian billing stale FX
T_FIX = T(15, 10, 0)           # recovery


# ======================================================================================
# 1. nginx access (combined + upstream timings + x_request_id), 90% health-check spam
# ======================================================================================
def nginx_line(t, i, off=0):
    r = rnd.random()
    ts = clf(t, off)
    if r < 0.90:
        return ('%s - - [%s] "GET %s HTTP/1.1" 200 %d "-" "%s" "-" rt=0.00%d uct="-" uht="-" urt="0.00%d" rid=-'
                % (PROBE_IPS[rnd.randrange(6)], ts, PROBE_PATHS[rnd.randrange(7)], 2 + rnd.randrange(41),
                   PROBE_UAS[rnd.randrange(4)], 1 + rnd.randrange(3), rnd.randrange(3)))
    ip = CLIENT_IPS[rnd.randrange(2048)]
    path = APP_PATHS[rnd.randrange(14)]
    ua = USER_AGENTS[rnd.randrange(7)]
    rid = RIDS[i & 4095]
    storm = T_503 <= t <= T_503_END
    if storm and rnd.random() < 0.34:
        st = (502, 504, 503)[rnd.randrange(3)]
        rt = 30.001 if st == 504 else 0.031
        return ('%s - - [%s] "POST %s HTTP/1.1" %d %d "https://shop.acme-internal.net/cart" "%s" "-" '
                'rt=%.3f uct="0.000" uht="-" urt="-" rid=%s'
                % (ip, ts, path, st, 166 + rnd.randrange(30), ua, rt, rid))
    rt = lat_ms(9.0, 210.0) / 1000.0
    if path.startswith("/api/v1/catalog") and t >= T_P99 and rnd.random() < 0.031:
        rt = 0.66 + rnd.random() * 0.9
    return ('%s - - [%s] "%s %s HTTP/1.1" 200 %d "https://shop.acme-internal.net/" "%s" "-" '
            'rt=%.3f uct="0.001" uht="%.3f" urt="%.3f" rid=%s'
            % (ip, ts, "POST" if rnd.random() < 0.2 else "GET", path, 380 + rnd.randrange(48000), ua,
               rt, rt * 0.4, rt * 0.9, rid))


ADMIN_RELOAD = ('10.42.9.31 - - [%s] "POST /admin/config/reload HTTP/1.1" 204 0 "-" '
                '"curl/8.7.1" "-" rt=%.3f uct="0.001" uht="0.%03d" urt="%.3f" rid=%s')


def gen_nginx():
    # rotated .gz half of the needle
    lf = LogFile("nginx/access.log.1.gz", gz=True)
    specials = []
    for k, (hh, mm, ss) in enumerate([(12, 58, 11), (12, 58, 39), (12, 59, 12), (12, 59, 44)]):
        tt = T(hh, mm, ss, 100 + k)
        specials.append(sp(tt, "D07",
                           "admin config-reload #%d of 7 (needle, rotated side)" % (k + 1),
                           ADMIN_RELOAD % (clf(tt), 0.31 + k * 0.04, 180 + k, 0.29 + k * 0.04, RIDS[900 + k])))
    run_bytes(lf, int(58 * MB * SCALE), lambda t, i: nginx_line(t, i), specials,
              t0=ep(2026, 7, 28, 6, 0, 0), t1=T(13, 0, 0))
    lf.close()
    stats["nginx/access.log.1.gz"] = lf

    lf = LogFile("nginx/access.log")
    specials = []
    for k, (hh, mm, ss) in enumerate([(13, 1, 2), (13, 2, 17), (13, 2, 55)]):
        tt = T(hh, mm, ss, 400 + k)
        specials.append(sp(tt, "D07",
                           "admin config-reload #%d of 7 (needle, live side)" % (k + 5),
                           ADMIN_RELOAD % (clf(tt), 0.47 + k * 0.05, 220 + k, 0.44 + k * 0.05, RIDS[910 + k])))
    specials.append(sp(T(14, 1, 57, 880), "D05",
                       "ORD-88231 checkout request at the edge, X-Request-ID spelling",
                       '10.42.3.7 - - [%s] "POST /api/v1/checkout HTTP/1.1" 200 511 '
                       '"https://shop.acme-internal.net/cart" "acme-shop-ios/7.4.0 (CFNetwork/1494.0.7 Darwin/24.0.0)" '
                       '"-" rt=1.884 uct="0.002" uht="1.102" urt="1.879" rid=%s' % (clf(T(14, 1, 57, 880)), RID_881)))
    run_bytes(lf, int(128 * MB * SCALE), lambda t, i: nginx_line(t, i), specials, t0=T(13, 0, 0), t1=W1)
    # truncated final line (log rotation / SIGKILL mid-write)
    lf.close(partial='10.42.18.204 - - [28/Jul/2026:15:59:59 +0000] "GET /api/v1/catalog/sear')
    stats["nginx/access.log"] = lf

    lf = LogFile("nginx/error.log")

    def noise(t, i):
        if rnd.random() < 0.55:
            return ('%s [warn] 1123#1123: *%d upstream server temporarily disabled while reading response header '
                    'from upstream, client: %s, server: shop.acme-internal.net, request: "GET %s HTTP/1.1", '
                    'upstream: "http://10.42.12.%d:8080%s", host: "shop.acme-internal.net"'
                    % (nginx_err_ts(t), 700000 + i, CLIENT_IPS[i & 2047], APP_PATHS[i % 14],
                       20 + (i % 40), APP_PATHS[i % 14]))
        return ('%s [error] 1123#1123: *%d upstream timed out (110: Connection timed out) while reading response '
                'header from upstream, client: %s, server: shop.acme-internal.net, request: "POST %s HTTP/1.1", '
                'upstream: "http://10.42.12.%d:8080%s", host: "shop.acme-internal.net", referrer: '
                '"https://shop.acme-internal.net/cart"'
                % (nginx_err_ts(t), 700000 + i, CLIENT_IPS[(i * 3) & 2047], APP_PATHS[i % 14],
                   20 + (i % 40), APP_PATHS[i % 14]))

    specials = [
        sp(T(13, 41, 7, 113), "D03",
           "nginx sees inventory-svc upstream refusing connections right after the OOMKill",
           '%s [error] 1123#1123: *884213 connect() failed (111: Connection refused) while connecting to upstream, '
           'client: 10.42.3.7, server: shop.acme-internal.net, request: "POST /api/v1/checkout HTTP/1.1", '
           'upstream: "http://10.42.12.31:8080/api/v1/checkout", host: "shop.acme-internal.net", '
           'referrer: "https://shop.acme-internal.net/cart"' % nginx_err_ts(T(13, 41, 7, 113))),
        sp(T(13, 41, 9, 2), "D03",
           "no live endpoints left for the inventory-svc upstream group",
           '%s [error] 1123#1123: *884219 no live upstreams while connecting to upstream, client: 10.42.4.19, '
           'server: shop.acme-internal.net, request: "POST /api/v1/inventory/reserve HTTP/1.1", '
           'upstream: "http://inventory_upstream/api/v1/inventory/reserve", host: "shop.acme-internal.net"'
           % nginx_err_ts(T(13, 41, 9, 2))),
    ]
    run_bytes(lf, int(11 * MB * SCALE), noise, specials)
    lf.close()
    stats["nginx/error.log"] = lf


def nginx_err_ts(ts):
    y, mo, d, h, mi, s, _ = parts(ts)
    return "%04d/%02d/%02d %02d:%02d:%02d" % (y, mo, d, h, mi, s)


# ======================================================================================
# 2. Istio / Envoy access logs -- JSON + text
# ======================================================================================
CLUSTERS = (
    ["outbound|8080||catalog-svc.prod.svc.cluster.local"] * 34 +
    ["outbound|8080||inventory-svc.prod.svc.cluster.local"] * 24 +
    ["outbound|8080||checkout-api.prod.svc.cluster.local"] * 18 +
    ["outbound|8080||payments-worker.prod.svc.cluster.local"] * 10 +
    ["outbound|3000||notify-svc.prod.svc.cluster.local"] * 8 +
    ["outbound|8080||promo-engine.prod.svc.cluster.local"] * 6
)
CL_PATH = {
    "catalog-svc": ["/api/v1/catalog/search", "/api/v1/catalog/item", "/api/v1/recommendations"],
    "inventory-svc": ["/v1/reserve", "/v1/stock", "/v1/release"],
    "checkout-api": ["/api/v1/checkout", "/api/v1/cart", "/api/v1/orders"],
    "payments-worker": ["/v1/capture", "/v1/refund"],
    "notify-svc": ["/v1/email", "/v1/push"],
    "promo-engine": ["/v1/quote", "/v1/apply"],
}
UP_IP = {"catalog-svc": 21, "inventory-svc": 31, "checkout-api": 41, "payments-worker": 51,
         "notify-svc": 61, "promo-engine": 71}


def envoy_pick(t, i):
    cl = CLUSTERS[rnd.randrange(100)]
    svc = cl.split("||")[1].split(".")[0]
    path = CL_PATH[svc][rnd.randrange(len(CL_PATH[svc]))]
    code, flags, dur = 200, "-", lat_ms(6.0, 120.0)
    if svc == "catalog-svc":
        if t >= T_P99 and rnd.random() < 0.031:
            dur = 640 + int(rnd.random() * 900)      # <-- D08 p99 shift, no errors
    if svc == "inventory-svc":
        if T_POOL <= t < T_OOM:
            dur = 900 + int(rnd.random() * 4200)
            if rnd.random() < 0.18:
                code, flags, dur = 504, "UT", 5000
        elif T_OOM <= t <= T_503_END:
            r = rnd.random()
            if r < 0.62:
                code, flags, dur = 503, "UF", 1 + (i % 40)
            elif r < 0.74:
                code, flags, dur = 503, "UH", 0
            elif r < 0.80:
                code, flags, dur = 504, "UT", 5000
    if svc == "checkout-api" and T_OOM <= t <= T_503_END and rnd.random() < 0.29:
        code, flags, dur = 503, "URX", 120 + rnd.randrange(300)
    return cl, svc, path, code, flags, dur


ENVOY_DETAIL = {
    "UF": "upstream_reset_before_response_started{connection_failure}",
    "UH": "no_healthy_upstream",
    "UT": "upstream_response_timeout",
    "URX": "upstream_reset_before_response_started{overflow}",
    "-": "via_upstream",
}


def envoy_json_line(t, i):
    cl, svc, path, code, flags, dur = envoy_pick(t, i)
    rid = RIDS[i & 4095]
    ust = "null" if flags in ("UF", "UH") else str(max(0, dur - 2))
    fail = ('"delayed_connect_error:111"' if flags == "UF" else "null")
    return ('{"start_time":"%s","method":"%s","path":"%s","protocol":"HTTP/1.1","response_code":%d,'
            '"response_flags":"%s","response_code_details":"%s","connection_termination_details":null,'
            '"upstream_transport_failure_reason":%s,"bytes_received":%d,"bytes_sent":%d,"duration":%d,'
            '"upstream_service_time":%s,"x_forwarded_for":"%s","user_agent":"%s","request_id":"%s",'
            '"authority":"shop.acme-internal.net","upstream_host":"10.42.12.%d:8080","upstream_cluster":"%s",'
            '"upstream_local_address":"10.42.0.9:%d","downstream_local_address":"10.42.0.9:8443",'
            '"downstream_remote_address":"%s:%d","requested_server_name":null,"route_name":"%s-route",'
            '"istio_policy_status":null}'
            % (iso_ms(t), "POST" if rnd.random() < 0.25 else "GET", path, code, flags,
               ENVOY_DETAIL[flags], fail, 120 + rnd.randrange(900), 60 + rnd.randrange(40000), dur, ust,
               CLIENT_IPS[rnd.randrange(2048)], USER_AGENTS[rnd.randrange(7)], rid,
               UP_IP[svc] + rnd.randrange(6), cl, 40000 + rnd.randrange(20000),
               CLIENT_IPS[rnd.randrange(2048)], 30000 + rnd.randrange(30000), svc))


def envoy_text_line(t, i):
    cl, svc, path, code, flags, dur = envoy_pick(t, i)
    rid = RIDS[i & 4095]
    return ('[%s] "%s %s HTTP/1.1" %d %s %d %d %d %s "%s" "%s" "%s" "shop.acme-internal.net" '
            '"10.42.12.%d:8080" %s 10.42.12.5:%d 10.42.0.9:8443 %s:%d - %s-route'
            % (iso_ms(t), "POST" if rnd.random() < 0.25 else "GET", path, code, flags,
               120 + rnd.randrange(900), 60 + rnd.randrange(9000), dur,
               "-" if flags in ("UF", "UH") else str(max(0, dur - 2)),
               CLIENT_IPS[rnd.randrange(2048)], USER_AGENTS[rnd.randrange(7)], rid,
               UP_IP[svc] + rnd.randrange(6), cl, 40000 + rnd.randrange(20000),
               CLIENT_IPS[rnd.randrange(2048)], 30000 + rnd.randrange(30000), svc))


def gen_envoy():
    lf = LogFile("istio/ingressgateway-access.json.log")
    specials = []
    # D08 statistical anchors (indistinguishable from emergent lines; answer-key anchors only)
    for k, tt in enumerate([T(11, 6, 12, 41), T(12, 14, 3, 907), T(15, 41, 22, 18)]):
        specials.append(sp(tt, "D08",
                           "catalog-svc slow request AFTER the 4.7.2 rollout, HTTP 200 (no error line exists)",
                           '{"start_time":"%s","method":"GET","path":"/api/v1/catalog/search","protocol":"HTTP/1.1",'
                           '"response_code":200,"response_flags":"-","response_code_details":"via_upstream",'
                           '"connection_termination_details":null,"upstream_transport_failure_reason":null,'
                           '"bytes_received":311,"bytes_sent":18422,"duration":%d,"upstream_service_time":%d,'
                           '"x_forwarded_for":"10.42.7.%d","user_agent":"acme-shop-android/7.4.1 (okhttp/4.12.0)",'
                           '"request_id":"%s","authority":"shop.acme-internal.net","upstream_host":"10.42.12.2%d:8080",'
                           '"upstream_cluster":"outbound|8080||catalog-svc.prod.svc.cluster.local",'
                           '"upstream_local_address":"10.42.0.9:44%03d","downstream_local_address":"10.42.0.9:8443",'
                           '"downstream_remote_address":"10.42.7.%d:5%04d","requested_server_name":null,'
                           '"route_name":"catalog-svc-route","istio_policy_status":null}'
                           % (iso_ms(tt), 1180 + k * 37, 1177 + k * 37, 40 + k, RIDS[1200 + k], k,
                              100 + k, 40 + k, 1000 + k)))
    for k, (tt, rid) in enumerate(zip([T(13, 41, 7, 113), T(13, 41, 7, 908), T(13, 44, 2, 51)], RID_STORM)):
        specials.append(sp(tt, "D03",
                           "Envoy 503 UF against inventory-svc: upstream connection refused (pod gone)",
                           '{"start_time":"%s","method":"POST","path":"/v1/reserve","protocol":"HTTP/1.1",'
                           '"response_code":503,"response_flags":"UF","response_code_details":'
                           '"upstream_reset_before_response_started{connection_failure}",'
                           '"connection_termination_details":null,'
                           '"upstream_transport_failure_reason":"delayed_connect_error:111",'
                           '"bytes_received":412,"bytes_sent":91,"duration":%d,"upstream_service_time":null,'
                           '"x_forwarded_for":"10.42.3.7","user_agent":"acme-shop-web/3.2.1","request_id":"%s",'
                           '"authority":"shop.acme-internal.net","upstream_host":"10.42.12.31:8080",'
                           '"upstream_cluster":"outbound|8080||inventory-svc.prod.svc.cluster.local",'
                           '"upstream_local_address":null,"downstream_local_address":"10.42.0.9:8443",'
                           '"downstream_remote_address":"10.42.3.7:51%03d","requested_server_name":null,'
                           '"route_name":"inventory-svc-route","istio_policy_status":null}'
                           % (iso_ms(tt), 29 + k, rid, 400 + k)))
    specials.append(sp(T(14, 1, 57, 884), "D05",
                       "ORD-88231 checkout succeeded at the mesh layer (request_id spelling #2)",
                       '{"start_time":"%s","method":"POST","path":"/api/v1/checkout","protocol":"HTTP/1.1",'
                       '"response_code":200,"response_flags":"-","response_code_details":"via_upstream",'
                       '"connection_termination_details":null,"upstream_transport_failure_reason":null,'
                       '"bytes_received":902,"bytes_sent":511,"duration":1884,"upstream_service_time":1879,'
                       '"x_forwarded_for":"10.42.3.7","user_agent":"acme-shop-ios/7.4.0 (CFNetwork/1494.0.7 '
                       'Darwin/24.0.0)","request_id":"%s","authority":"shop.acme-internal.net",'
                       '"upstream_host":"10.42.12.41:8080",'
                       '"upstream_cluster":"outbound|8080||checkout-api.prod.svc.cluster.local",'
                       '"upstream_local_address":"10.42.0.9:41022","downstream_local_address":"10.42.0.9:8443",'
                       '"downstream_remote_address":"10.42.3.7:51422","requested_server_name":null,'
                       '"route_name":"checkout-api-route","istio_policy_status":null}'
                       % (iso_ms(T(14, 1, 57, 884)), RID_881)))
    run_bytes(lf, int(96 * MB * SCALE), envoy_json_line, specials)
    lf.close(partial='{"start_time":"2026-07-28T15:59:59.902Z","method":"GET","path":"/api/v1/catalog/it')
    stats["istio/ingressgateway-access.json.log"] = lf

    lf = LogFile("istio/sidecar-catalog-svc-access.log")
    specials = [
        sp(T(11, 5, 44, 201), "D08",
           "sidecar text-format view of the same catalog-svc latency shift (duration column, 200 OK)",
           '[%s] "GET /api/v1/catalog/search HTTP/1.1" 200 - 311 18422 1207 1204 "10.42.7.41" '
           '"acme-shop-android/7.4.1 (okhttp/4.12.0)" "%s" "shop.acme-internal.net" "10.42.12.21:8080" '
           'outbound|8080||catalog-svc.prod.svc.cluster.local 10.42.12.5:44012 10.42.0.9:8443 10.42.7.41:51001 -'
           % (iso_ms(T(11, 5, 44, 201)), RIDS[1301])),
    ]
    run_bytes(lf, int(34 * MB * SCALE), envoy_text_line, specials)
    lf.close()
    stats["istio/sidecar-catalog-svc-access.log"] = lf


# ======================================================================================
# 3. HAProxy (logs in Europe/Moscow local time = UTC+3  -> deliberate TZ skew)
# ======================================================================================
def gen_haproxy():
    lf = LogFile("haproxy/haproxy.log")
    OFF = 10800

    def noise(t, i):
        storm = T_503 <= t <= T_503_END
        conn = 380 + rnd.randrange(520)
        if storm and rnd.random() < 0.30:
            return ('%s haproxy-lb-1 haproxy[2291]: %s:%d [%s] fe_https be_edge/edge-nginx-%d 0/0/1/-1/30003 '
                    '504 213 - - sHVN %d/%d/%d/%d/0 0/0 {%s} "POST %s HTTP/1.1"'
                    % (sysl(t + OFF), CLIENT_IPS[rnd.randrange(2048)], 30000 + rnd.randrange(30000),
                       clf(t, OFF)[:-6] + "." + ("%03d" % rnd.randrange(1000)), 1 + rnd.randrange(3),
                       conn + 400, conn + 390, 8 + rnd.randrange(20), rnd.randrange(12),
                       RIDS[i & 4095], APP_PATHS[rnd.randrange(14)]))
        tt = lat_ms(11.0, 260.0)
        return ('%s haproxy-lb-1 haproxy[2291]: %s:%d [%s] fe_https be_edge/edge-nginx-%d 0/0/1/%d/%d '
                '200 %d - - ---- %d/%d/%d/%d/0 0/0 {%s} "%s %s HTTP/1.1"'
                % (sysl(t + OFF), CLIENT_IPS[rnd.randrange(2048)], 30000 + rnd.randrange(30000),
                   clf(t, OFF)[:-6] + "." + ("%03d" % rnd.randrange(1000)), 1 + rnd.randrange(3), tt, tt + 2,
                   300 + rnd.randrange(48000), conn, conn - 10, 4 + rnd.randrange(14), rnd.randrange(6),
                   RIDS[i & 4095], "POST" if rnd.random() < 0.2 else "GET", APP_PATHS[rnd.randrange(14)]))

    specials = [
        sp(T(13, 41, 12, 4), "D03",
           "HAProxy marks the edge backend DOWN (L7 check 503) -- 3h skewed clock: line stamp is 16:41 MSK",
           '%s haproxy-lb-1 haproxy[2291]: Server be_edge/edge-nginx-2 is DOWN, reason: Layer7 wrong status, '
           'code: 503, info: "Service Unavailable", check duration: 3ms. 2 active and 0 backup servers left. '
           '112 sessions active, 0 requeued, 0 remaining in queue.' % sysl(T(13, 41, 12, 4) + OFF)),
        sp(T(14, 1, 57, 878), "D05",
           "ORD-88231 at the outermost hop; the id is captured as a {} header block, not a field",
           '%s haproxy-lb-1 haproxy[2291]: 10.42.3.7:51422 [%s.878] fe_https be_edge/edge-nginx-1 0/0/1/1881/1884 '
           '200 511 - - ---- 812/810/9/4/0 0/0 {%s} "POST /api/v1/checkout HTTP/1.1"'
           % (sysl(T(14, 1, 57, 878) + OFF), clf(T(14, 1, 57), OFF)[:-6], RID_881)),
        sp(T(15, 12, 41, 9), "D03",
           "backend recovers after replicas are restored",
           '%s haproxy-lb-1 haproxy[2291]: Server be_edge/edge-nginx-2 is UP, reason: Layer7 check passed, '
           'code: 200, info: "OK", check duration: 2ms. 3 active and 0 backup servers online.'
           % sysl(T(15, 12, 41, 9) + OFF)),
    ]
    run_bytes(lf, int(28 * MB * SCALE), noise, specials)
    lf.close()
    stats["haproxy/haproxy.log"] = lf


# ======================================================================================
# 4. Java / logback -- checkout-api  (D01 NPE, interleaved traces) and catalog-svc
# ======================================================================================
JCLASS = ["c.a.checkout.web.CheckoutController", "c.a.checkout.cart.CartService",
          "c.a.checkout.client.InventoryClient", "c.a.checkout.client.PromoClient",
          "c.a.checkout.kafka.OrderEventProducer", "o.s.web.servlet.DispatcherServlet",
          "c.a.checkout.promo.PromoService", "c.a.common.tracing.TraceFilter"]


def gen_checkout_java():
    lf = LogFile("apps/checkout-api/checkout-api.log")

    def noise(t, i):
        lvl = "INFO "
        r = rnd.random()
        if r < 0.22:
            lvl = "DEBUG"
        elif r < 0.26:
            lvl = "WARN "
        storm = T_503 <= t <= T_503_END
        if storm and rnd.random() < 0.18:
            return ('%s WARN  1 --- [http-nio-8080-exec-%d] c.a.checkout.client.InventoryClient      : '
                    'retrying inventory reserve after 503 (attempt %d/3) [traceId=%s, orderId=%s]'
                    % (logback(t), 1 + rnd.randrange(40), 1 + rnd.randrange(3),
                       RIDS[i & 4095].replace("-", ""), order_at(t)))
        k = rnd.randrange(8)
        return ('%s %s 1 --- [http-nio-8080-exec-%d] %-40s : %s [traceId=%s, orderId=%s]'
                % (logback(t), lvl, 1 + rnd.randrange(40), JCLASS[k],
                   ["cart loaded", "checkout started", "promo evaluated", "order persisted",
                    "inventory reserved", "payment intent created", "order event published",
                    "session resolved"][k],
                   RIDS[i & 4095].replace("-", ""), order_at(t)))

    npe = [
        '%s ERROR 1 --- [http-nio-8080-exec-27] c.a.checkout.web.CheckoutController      : Unhandled exception while '
        'applying promotion [traceId=41b8ee02c9a34f7d9a0c6f5b1e2d3c4a, orderId=ORD-88407]' % logback(T_NPE),
        'java.lang.NullPointerException: Cannot invoke "java.lang.String.toUpperCase()" because the return value of '
        '"com.acme.checkout.promo.PromoCode.normalized()" is null',
        '\tat com.acme.checkout.promo.PromoCodeResolver.resolve(PromoCodeResolver.java:88)',
        '%s WARN  1 --- [http-nio-8080-exec-12] c.a.checkout.client.InventoryClient      : slow inventory call 4813ms '
        '[traceId=c41d77e92b304a568f6190cd3e7b1a22, orderId=ORD-88402]' % logback(T_NPE + 0.001),
        '\tat com.acme.checkout.promo.PromoService.applyBestOffer(PromoService.java:141)',
        '%s ERROR 1 --- [http-nio-8080-exec-12] c.a.checkout.client.InventoryClient      : Inventory lookup failed '
        '[traceId=c41d77e92b304a568f6190cd3e7b1a22, orderId=ORD-88402]' % logback(T_NPE + 0.002),
        'java.net.SocketTimeoutException: Read timed out',
        '\tat com.acme.checkout.promo.PromoService.quote(PromoService.java:97)',
        '\tat java.base/sun.nio.ch.NioSocketImpl.timedRead(NioSocketImpl.java:283)',
        '\tat com.acme.checkout.web.CheckoutController.checkout(CheckoutController.java:206)',
        '\tat java.base/sun.nio.ch.NioSocketImpl.implRead(NioSocketImpl.java:309)',
        '\tat java.base/jdk.internal.reflect.DirectMethodHandleAccessor.invoke(DirectMethodHandleAccessor.java:103)',
        '\tat java.base/java.net.Socket$SocketInputStream.read(Socket.java:966)',
        '\tat org.springframework.web.method.support.InvocableHandlerMethod.doInvoke(InvocableHandlerMethod.java:255)',
        '\tat org.apache.hc.core5.http.impl.io.SessionInputBufferImpl.fillBuffer(SessionInputBufferImpl.java:152)',
        '\tat org.springframework.web.servlet.DispatcherServlet.doDispatch(DispatcherServlet.java:1089)',
        '\tat com.acme.checkout.client.InventoryClient.reserve(InventoryClient.java:174)',
        '\tat org.apache.catalina.core.ApplicationFilterChain.doFilter(ApplicationFilterChain.java:167)',
        '\t... 47 common frames omitted',
        '\tat java.base/java.lang.Thread.run(Thread.java:1583)',
    ]

    specials = [
        sp(T(11, 0, 55, 12), "D04",
           "checkout-api notices the catalog dependency got slower right after the 4.7.2 rollout",
           '%s WARN  1 --- [http-nio-8080-exec-3] c.a.checkout.client.CatalogClient        : catalog p95 breached SLO '
           '(1180ms > 250ms) after upstream version change catalog-svc=4.7.2 [traceId=%s]'
           % (logback(T(11, 0, 55, 12)), RIDS[1401].replace("-", ""))),
        sp(T(13, 45, 2, 118), "D03",
           "checkout-api read timeouts against inventory-svc during the 503 storm (multi-line stack trace)",
           ['%s ERROR 1 --- [http-nio-8080-exec-19] c.a.checkout.client.InventoryClient      : Inventory reserve '
            'failed after 3 attempts [traceId=%s, orderId=ORD-88377]'
            % (logback(T(13, 45, 2, 118)), RID_STORM[0].replace("-", "")),
            'org.springframework.web.client.HttpServerErrorException$ServiceUnavailable: 503 Service Unavailable: '
            '"upstream connect error or disconnect/reset before headers. reset reason: connection failure"',
            '\tat org.springframework.web.client.DefaultResponseErrorHandler.handleError('
            'DefaultResponseErrorHandler.java:189)',
            '\tat com.acme.checkout.client.InventoryClient.reserve(InventoryClient.java:174)',
            '\tat com.acme.checkout.web.CheckoutController.checkout(CheckoutController.java:198)',
            '\t... 63 common frames omitted']),
        sp(T(14, 1, 57, 902), "D05",
           "ORD-88231 in the Java service: traceId is the edge X-Request-ID with the dashes stripped",
           ['%s INFO  1 --- [http-nio-8080-exec-8] c.a.checkout.web.CheckoutController      : checkout accepted '
            '[traceId=%s, orderId=%s, amountMinor=1299900, currency=EUR]'
            % (logback(T(14, 1, 57, 902)), RID_881_J, ORD_881),
            '%s INFO  1 --- [http-nio-8080-exec-8] c.a.checkout.kafka.OrderEventProducer    : published '
            'orders.events key=%s partition=3 offset=5512034 [traceId=%s]'
            % (logback(T(14, 1, 58, 41)), ORD_881, RID_881_J)]),
        sp(T_NPE, "D01",
           "NPE in PromoCodeResolver -- the trace is INTERLEAVED line-by-line with a concurrent "
           "SocketTimeoutException trace from thread exec-12",
           npe),
        sp(T(14, 12, 41, 8), "D01",
           "second NPE occurrence, same call site, different lowercase promo code",
           ['%s ERROR 1 --- [http-nio-8080-exec-31] c.a.checkout.web.CheckoutController      : Unhandled exception '
            'while applying promotion [traceId=7c02aa19b4d84e0f9911e3d5c6b7a8f0, orderId=ORD-88411]'
            % logback(T(14, 12, 41, 8)),
            'java.lang.NullPointerException: Cannot invoke "java.lang.String.toUpperCase()" because the return value '
            'of "com.acme.checkout.promo.PromoCode.normalized()" is null',
            '\tat com.acme.checkout.promo.PromoCodeResolver.resolve(PromoCodeResolver.java:88)',
            '\tat com.acme.checkout.promo.PromoService.applyBestOffer(PromoService.java:141)',
            '\tat com.acme.checkout.web.CheckoutController.checkout(CheckoutController.java:206)',
            '\t... 47 common frames omitted']),
    ]
    run_bytes(lf, int(42 * MB * SCALE), noise, specials)
    lf.close()
    stats["apps/checkout-api/checkout-api.log"] = lf


def gen_catalog_java():
    lf = LogFile("apps/catalog-svc/catalog-svc.log")
    CC = ["c.a.catalog.web.CatalogController", "c.a.catalog.repo.ItemRepository",
          "c.a.catalog.repo.VendorRefLookupRepository", "c.a.catalog.cache.ReadThroughCache",
          "c.a.catalog.CacheEvictionScheduler", "o.h.engine.jdbc.spi.SqlStatementLogger"]

    def noise(t, i):
        if rnd.random() < 0.03:
            return ('%s WARN  1 --- [pool-2-thread-1] c.a.catalog.CacheEvictionScheduler       : '
                    'eviction pass took %dms for %d entries (threshold 500ms)'
                    % (logback(t), 1200 + rnd.randrange(900), 140000 + rnd.randrange(60000)))
        lvl = "DEBUG" if rnd.random() < 0.35 else "INFO "
        k = rnd.randrange(6)
        return ('%s %s 1 --- [http-nio-8080-exec-%d] %-40s : %s sku=%s [traceId=%s]'
                % (logback(t), lvl, 1 + rnd.randrange(24), CC[k],
                   ["item fetched", "search executed", "cache hit", "cache miss", "vendor ref resolved",
                    "facet built"][k], SKUS[rnd.randrange(3000)], RIDS[i & 4095].replace("-", "")))

    specials = [
        sp(T_DEPLOY, "D04",
           "catalog-svc 4.7.2 starts -- this is the change that introduces the un-indexed vendor_ref lookup",
           ['%s INFO  1 --- [           main] c.a.catalog.CatalogApplication           : Started CatalogApplication '
            'in 11.442 seconds (process running for 12.108) version=4.7.2 git=8fa21c7 profile=prod' % logback(T_DEPLOY),
            '%s INFO  1 --- [           main] o.f.core.internal.command.DbMigrate       : Migrating schema "public" '
            'to version 4.7.2 - add vendor ref lookup path' % logback(T_DEPLOY + 0.4),
            '%s INFO  1 --- [           main] o.f.core.internal.command.DbMigrate       : Successfully applied 1 '
            'migration to schema "public" (execution time 00:00.211s)' % logback(T_DEPLOY + 0.9)]),
        sp(T(11, 5, 12, 771), "D08",
           "the new lookup runs a JSONB ->> comparison with no supporting index; latency, not errors",
           '%s DEBUG 1 --- [http-nio-8080-exec-7] c.a.catalog.repo.VendorRefLookupRepository: executing '
           "SELECT c.sku_id, c.title, c.attrs FROM catalog_items c WHERE c.attrs ->> 'vendor_ref' = ? "
           'ORDER BY c.updated_at DESC -- took 1188ms rows=3 [traceId=%s]'
           % (logback(T(11, 5, 12, 771)), RIDS[1501].replace("-", ""))),
        sp(T(12, 59, 2, 330), "D07",
           "the admin config reload invalidated the entire read-through cache -> thundering herd",
           ['%s WARN  1 --- [config-reload-1] c.a.catalog.config.RuntimeConfigListener  : configuration reloaded '
            'from /etc/catalog/runtime.yaml (source=POST /admin/config/reload, peer=10.42.9.31)'
            % logback(T(12, 59, 2, 330)),
            '%s WARN  1 --- [config-reload-1] c.a.catalog.cache.ReadThroughCache        : cache invalidated: '
            'entries=184203 hitRatio was 0.976; cold reads will hit postgres directly'
            % logback(T(12, 59, 2, 341))]),
        sp(T(13, 12, 4, 55), "D04",
           "catalog-svc holding DB connections far beyond normal after the cache flush",
           '%s WARN  1 --- [http-nio-8080-exec-15] com.zaxxer.hikari.pool.HikariPool        : HikariPool-1 - '
           'Connection is not available, request timed out after 30001ms (total=40, active=40, idle=0, waiting=61)'
           % logback(T(13, 12, 4, 55))),
        sp(T(15, 9, 41, 2), "D04",
           "the actual fix: an expression index on attrs->>'vendor_ref' ends the incident",
           '%s INFO  1 --- [           main] c.a.catalog.ops.HotfixRunner              : applied hotfix 4.7.3: '
           "CREATE INDEX CONCURRENTLY idx_catalog_items_vendor_ref ON catalog_items ((attrs ->> 'vendor_ref'))"
           % logback(T(15, 9, 41, 2))),
    ]
    run_bytes(lf, int(28 * MB * SCALE), noise, specials)
    lf.close()
    stats["apps/catalog-svc/catalog-svc.log"] = lf

    # ---- ANSI-coloured console capture (red herring D13 lives here) --------------------
    lf = LogFile("apps/catalog-svc/catalog-console-ansi.log")
    G, Y, R, C, D, X = "\x1b[32m", "\x1b[33m", "\x1b[31m", "\x1b[36m", "\x1b[2m", "\x1b[0m"

    def anoise(t, i):
        if rnd.random() < 0.25:
            return ('%s%s%s %sWARN%s %s%-42s%s eviction pass took %dms for %d entries'
                    % (D, logback(t), X, Y, X, C, "c.a.catalog.CacheEvictionScheduler", X,
                       1200 + rnd.randrange(900), 140000 + rnd.randrange(60000)))
        k = rnd.randrange(6)
        return ('%s%s%s %sINFO%s %s%-42s%s %s sku=%s'
                % (D, logback(t), X, G, X, C, CC[k], X,
                   ["item fetched", "search executed", "cache hit", "cache miss", "vendor ref resolved",
                    "facet built"][k], SKUS[rnd.randrange(3000)]))

    specials = [
        sp(T(10, 3, 12, 8), "D13",
           "RED HERRING: alarming-looking TLS expiry warning, 14 days of runway, unrelated to the incident",
           '%s%s%s %sWARN%s %s%-42s%s TLS certificate for shop.acme-internal.net expires in 14 days '
           '(notAfter=2026-08-11T09:00:00Z, issuer=CN=ACME Internal CA G3)'
           % (D, logback(T(10, 3, 12, 8)), X, Y, X, C, "c.a.common.tls.CertificateMonitor", X)),
        sp(T(13, 50, 1, 41), "D13",
           "RED HERRING: eviction-scheduler WARN spam runs at the same rate all day, before and after the incident",
           '%s%s%s %sWARN%s %s%-42s%s eviction pass took 1523ms for 184203 entries (threshold 500ms)'
           % (D, logback(T(13, 50, 1, 41)), X, Y, X, C, "c.a.catalog.CacheEvictionScheduler", X)),
    ]
    run_bytes(lf, int(7 * MB * SCALE), anoise, specials)
    lf.close()
    stats["apps/catalog-svc/catalog-console-ansi.log"] = lf


# ======================================================================================
# 5. Go zap JSON (payments-worker) + Go panic inside a Docker json-file log
# ======================================================================================
def gen_zap():
    lf = LogFile("apps/payments-worker/payments.zap.json")

    def noise(t, i):
        lvl = "info"
        r = rnd.random()
        if r < 0.12:
            lvl = "debug"
        elif r < 0.145:
            lvl = "warn"
        msg = ["payment captured", "payment authorized", "webhook received", "idempotency hit",
               "refund processed", "psp latency sample"][i % 6]
        if lvl == "warn":
            msg = "psp retry scheduled"
        return ('{"level":"%s","ts":%.6f,"caller":"payments/%s.go:%d","msg":"%s","service":"payments-worker",'
                '"pod":"%s","trace_id":"%s","order_id":"%s","psp":"%s","psp_ref":"PSP-%07d",'
                '"amount_minor":%d,"currency":"%s","attempt":%d,"latency_ms":%d}'
                % (lvl, t, ["capture", "authorize", "webhook", "retrier"][rnd.randrange(4)],
                   60 + rnd.randrange(260), msg,
                   PODS["payments-worker"][rnd.randrange(2)], RIDS[i & 4095].replace("-", ""), order_at(t),
                   ["adyen", "stripe", "cloudpayments"][rnd.randrange(3)], 4400000 + rnd.randrange(90000),
                   AMOUNTS[rnd.randrange(len(AMOUNTS))], ["EUR", "USD", "RUB"][rnd.randrange(3)],
                   1 + rnd.randrange(3), lat_ms(40.0, 1400.0, 3)))

    specials = [
        sp(T_PAY, "D05",
           "ORD-88231 money was actually captured -- id is now `trace_id`, dash-less spelling",
           '{"level":"info","ts":%.6f,"caller":"payments/capture.go:214","msg":"payment captured",'
           '"service":"payments-worker","pod":"payments-worker-849fb6cd7-nn4jq","trace_id":"%s",'
           '"order_id":"%s","psp":"adyen","psp_ref":"%s","amount_minor":1299900,"currency":"EUR",'
           '"attempt":1,"latency_ms":412}' % (T_PAY, RID_881_J, ORD_881, PSP_881)),
        sp(T(14, 1, 58, 900), "D05",
           "payments emitted the confirmation event to orders.events partition 3",
           '{"level":"info","ts":%.6f,"caller":"payments/capture.go:266","msg":"emitting payment.captured",'
           '"service":"payments-worker","topic":"orders.events","partition":3,"offset":5512034,'
           '"order_id":"%s","trace_id":"%s"}' % (T(14, 1, 58, 900), ORD_881, RID_881_J)),
        sp(T(14, 4, 51, 20), "D02",
           "the retrier batch that is about to panic: PSP returned fewer results than requested",
           '{"level":"warn","ts":%.6f,"caller":"payments/retrier.go:104","msg":"psp returned partial capture set",'
           '"service":"payments-worker","requested":4,"returned":3,"batch_id":"B-77412",'
           '"trace_id":"%s"}' % (T(14, 4, 51, 20), RID_STORM[1].replace("-", ""))),
    ]
    run_bytes(lf, int(33 * MB * SCALE), noise, specials)
    lf.close()
    stats["apps/payments-worker/payments.zap.json"] = lf


PANIC = [
    "panic: runtime error: index out of range [3] with length 3",
    "",
    "goroutine 4711 [running]:",
    "github.com/acme/payments-worker/internal/batch.(*Retrier).flush(0xc000212a80, {0xc0004b2000, 0x3, 0x3}, 0x4)",
    "\t/src/internal/batch/retrier.go:118 +0x2a4",
    "github.com/acme/payments-worker/internal/batch.(*Retrier).drain(0xc000212a80, {0x14f2b40, 0xc00019e0f0})",
    "\t/src/internal/batch/retrier.go:87 +0x9c",
    "github.com/acme/payments-worker/internal/batch.(*Retrier).loop(0xc000212a80)",
    "\t/src/internal/batch/retrier.go:74 +0x11d",
    "created by github.com/acme/payments-worker/internal/batch.NewRetrier in goroutine 1",
    "\t/src/internal/batch/retrier.go:52 +0x145",
    "exit status 2",
]


def gen_docker():
    lf = LogFile("docker/payments-worker-3a7f2b1c9d8e4f5a6b7c8d9e0f1a2b3c-json.log")

    def noise(t, i):
        if rnd.random() < 0.08:
            txt = ("time=%s level=warn msg=\"psp slow response\" psp=%s latency_ms=%d attempt=%d"
                   % (iso_ms(t), ["adyen", "stripe"][rnd.randrange(2)], 900 + rnd.randrange(2400),
                      1 + rnd.randrange(3)))
            stream = "stderr"
        else:
            txt = ("time=%s level=info msg=\"capture ok\" order_id=%s psp_ref=PSP-%07d amount_minor=%d currency=%s"
                   % (iso_ms(t), order_at(t), 4400000 + rnd.randrange(90000),
                      AMOUNTS[rnd.randrange(len(AMOUNTS))], ["EUR", "USD", "RUB"][rnd.randrange(3)]))
            stream = "stdout"
        return ('{"log":%s,"stream":"%s","time":"%s"}'
                % (json.dumps(txt + "\n"), stream, nano(t)))

    specials = [sp(T_GOPANIC + n * 0.000117, "D02",
                   "goroutine panic frame %d/%d (each physical line is one docker json record; the \\n is escaped)"
                   % (n + 1, len(PANIC)),
                   '{"log":%s,"stream":"stderr","time":"%s"}'
                   % (json.dumps(ln + "\n"), nano(T_GOPANIC + n * 0.000117)))
                for n, ln in enumerate(PANIC)]
    specials.append(sp(T(14, 5, 13, 900), "D02",
                       "container restarted by the runtime after the panic",
                       '{"log":%s,"stream":"stderr","time":"%s"}'
                       % (json.dumps("time=%s level=info msg=\"payments-worker starting\" version=1.19.4 "
                                     "go=go1.23.4\n" % iso_ms(T(14, 5, 13, 900))), nano(T(14, 5, 13, 900)))))
    run_bytes(lf, int(28 * MB * SCALE), noise, specials)
    lf.close()
    stats["docker/payments-worker-3a7f2b1c9d8e4f5a6b7c8d9e0f1a2b3c-json.log"] = lf


# ======================================================================================
# 6. Python / uvicorn (inventory-svc)
# ======================================================================================
def gen_uvicorn():
    lf = LogFile("apps/inventory-svc/uvicorn.log")

    def noise(t, i):
        if rnd.random() < 0.6:
            return ('INFO:     %s:%d - "%s %s HTTP/1.1" %d %s'
                    % (CLIENT_IPS[rnd.randrange(2048)], 30000 + rnd.randrange(30000),
                       "POST" if rnd.random() < 0.66 else "GET",
                       ["/v1/reserve", "/v1/stock", "/v1/release", "/healthz"][rnd.randrange(4)],
                       200, "OK"))
        return ('%s INFO [inventory.api] reserve ok sku=%s qty=%d warehouse=WH-%d correlation_id=%s'
                % (comma_ts(t), SKUS[rnd.randrange(3000)], 1 + rnd.randrange(5), 1 + rnd.randrange(4),
                   RIDS[i & 4095]))

    tb = [
        '%s ERROR [uvicorn.error] Exception in ASGI application correlation_id=%s' % (comma_ts(T_POOL), RID_STORM[0]),
        'Traceback (most recent call last):',
        '  File "/usr/local/lib/python3.12/site-packages/uvicorn/protocols/http/httptools_impl.py", line 419, in run_asgi',
        '    result = await app(  # type: ignore[func-returns-value]',
        '             ^^^^^^^^^^',
        '  File "/app/inventory/api/routes.py", line 142, in reserve',
        '    async with pool.acquire(timeout=5.0) as conn:',
        '  File "/usr/local/lib/python3.12/site-packages/asyncpg/pool.py", line 1092, in _acquire',
        '    return await asyncio.wait_for(self._acquire_impl(), timeout)',
        '           ^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^',
        '  File "/usr/local/lib/python3.12/asyncio/tasks.py", line 519, in wait_for',
        '    raise TimeoutError from exc',
        'TimeoutError',
    ]

    specials = [
        sp(T_POOL, "D04",
           "inventory-svc cannot get a postgres connection: the pool is drained by the lock waits",
           tb),
        sp(T(13, 26, 12, 4), "D04",
           "pool saturation + request queue growth -- the mechanism that turns lock waits into memory growth",
           '%s WARNING [inventory.db] asyncpg pool exhausted: size=32 in_use=32 waiters=417 '
           'avg_wait_ms=4881 correlation_id=%s' % (comma_ts(T(13, 26, 12, 4)), RID_STORM[1])),
        sp(T(13, 38, 44, 900), "D03",
           "RSS climbing towards the 2Gi container limit as the queue grows -- last words before the OOMKill",
           ['%s WARNING [inventory.runtime] rss=1974MB limit=2048MB inflight=2211 queue_depth=4402'
            % comma_ts(T(13, 38, 44, 900)),
            '%s WARNING [inventory.runtime] rss=2036MB limit=2048MB inflight=2288 queue_depth=4711'
            % comma_ts(T(13, 39, 51, 12))]),
        sp(T(14, 1, 57, 890), "D05",
           "ORD-88231 reservation: the id is now spelled `correlation_id` and keeps the dashes",
           '%s INFO [inventory.api] reserve ok sku=SKU-40318 qty=1 warehouse=WH-2 order=%s correlation_id=%s'
           % (comma_ts(T(14, 1, 57, 890)), ORD_881, RID_881)),
    ]
    run_bytes(lf, int(26 * MB * SCALE), noise, specials)
    lf.close()
    stats["apps/inventory-svc/uvicorn.log"] = lf

    # partially written file (killed mid-write)
    lf = LogFile("misc/inventory-svc-partial.log")

    def pnoise(t, i):
        frac = (t - W0) / float(W1 - W0)
        rss = 380 + int(frac * 260) + (i % 17)
        if T_POOL <= t:
            rss = 1400 + int((t - T_POOL) / 4.0) + (i % 23)
        return ('%s [inventory.runtime] heap_sample rss_mb=%d gc_gen0=%d gc_gen2=%d pool_wait_ms=%d '
                'inflight=%d queue_depth=%d' % (comma_ts(t), rss, 4000 + i % 900, 12 + i % 40,
                                                2 + (i % 30) if t < T_POOL else 3800 + (i % 900),
                                                40 + (i % 60) if t < T_POOL else 2100 + (i % 300),
                                                0 if t < T_POOL else 4000 + (i % 800)))

    run_bytes(lf, int(2 * MB * SCALE), pnoise, [], t0=W0, t1=T_OOM)
    lf.close(partial="2026-07-28 13:40:12,88")
    stats["misc/inventory-svc-partial.log"] = lf


# ======================================================================================
# 7. Node pino (notify-svc)  -- D09 error-rate ramp, D05 offset reset
# ======================================================================================
TLS_STACK = ("Error: 140234914293632:error:1408F10B:SSL routines:ssl3_get_record:wrong version number\n"
             "    at TLSSocket.onConnectSecure (node:_tls_wrap:1650:34)\n"
             "    at TLSSocket.emit (node:events:519:28)\n"
             "    at TLSSocket._finishInit (node:_tls_wrap:1064:8)\n"
             "    at ssl.onhandshakedone (node:_tls_wrap:875:12)")


def gen_pino():
    lf = LogFile("apps/notify-svc/notify.pino.json")

    def noise(t, i):
        frac = (t - W0) / float(W1 - W0)
        err_p = 0.001 + 0.119 * (frac ** 1.6)     # <-- D09: 0.1% -> ~12% ramp
        if rnd.random() < err_p:
            return json.dumps({
                "level": 50, "time": ems(t), "pid": 1, "hostname": PODS["notify-svc"][i & 1],
                "name": "notify-svc",
                "err": {"type": "Error",
                        "message": "140234914293632:error:1408F10B:SSL routines:ssl3_get_record:wrong version number",
                        "stack": TLS_STACK, "code": "EPROTO", "library": "SSL routines"},
                "relay": "smtp-relay.acme-internal.net:587", "attempt": 1 + rnd.randrange(3),
                "order_id": order_at(t),
                "msg": "smtp relay handshake failed"}, separators=(",", ":"))
        return ('{"level":%d,"time":%d,"pid":1,"hostname":"%s","name":"notify-svc","channel":"%s",'
                '"order_id":"%s","template":"%s","queue_depth":%d,"msg":"%s"}'
                % (30 if rnd.random() < 0.89 else 20, ems(t), PODS["notify-svc"][rnd.randrange(2)],
                   ["email", "push", "sms"][rnd.randrange(3)], order_at(t),
                   ["order_confirmed", "shipment_created", "payment_receipt",
                    "promo_reminder"][rnd.randrange(4)],
                   int(40 + 900 * (frac ** 2)) + rnd.randrange(30),
                   ["notification sent", "notification queued", "template rendered"][rnd.randrange(3)]))

    specials = [
        sp(T(9, 4, 11, 2), "D09",
           "baseline: the same handshake error already exists at the start of the window, ~0.1% of sends",
           json.dumps({"level": 50, "time": ems(T(9, 4, 11, 2)), "pid": 1,
                       "hostname": "notify-svc-6c8d7f9b4-mn2kq", "name": "notify-svc",
                       "err": {"type": "Error",
                               "message": "140234914293632:error:1408F10B:SSL routines:ssl3_get_record:"
                                          "wrong version number",
                               "stack": TLS_STACK, "code": "EPROTO", "library": "SSL routines"},
                       "relay": "smtp-relay.acme-internal.net:587", "attempt": 1,
                       "order_id": "ORD-87104", "msg": "smtp relay handshake failed"},
                      separators=(",", ":"))),
        sp(T(9, 4, 12, 100), "D09",
           "the retry succeeds, which is why the ramp produces no user-visible failure until late",
           '{"level":30,"time":%d,"pid":1,"hostname":"notify-svc-6c8d7f9b4-mn2kq","name":"notify-svc",'
           '"channel":"email","order_id":"ORD-87104","attempt":2,"msg":"notification sent after retry"}'
           % ems(T(9, 4, 12, 100))),
        sp(T(14, 2, 12, 900), "D05",
           "notify-svc leaves the consumer group (rebalance) -- it stops consuming for ~75s",
           '{"level":40,"time":%d,"pid":1,"hostname":"notify-svc-6c8d7f9b4-mn2kq","name":"notify-svc",'
           '"group":"notify-svc-consumers","generation":41,"msg":"consumer group is rebalancing, pausing fetch"}'
           % ems(T(14, 2, 12, 900))),
        sp(T_RESET, "D05",
           "THE DROP: committed offset is below the log start offset, auto.offset.reset=latest "
           "skips 2867 records (5512034 is inside that hole)",
           ['{"level":40,"time":%d,"pid":1,"hostname":"notify-svc-6c8d7f9b4-mn2kq","name":"notify-svc",'
            '"topic":"orders.events","partition":3,"committed":5512034,"logStartOffset":5514901,'
            '"msg":"Offset out of range, resetting to latest per auto.offset.reset=latest"}' % ems(T_RESET),
            '{"level":40,"time":%d,"pid":1,"hostname":"notify-svc-6c8d7f9b4-mn2kq","name":"notify-svc",'
            '"topic":"orders.events","partition":3,"from":5512034,"to":5514901,"skipped":2867,'
            '"msg":"fetch position advanced past unconsumed records"}' % ems(T_RESET + 0.04)]),
        sp(T(15, 30, 2, 4), "D09",
           "late consequence of the ramp: retry queue depth is now an order of magnitude above baseline",
           '{"level":40,"time":%d,"pid":1,"hostname":"notify-svc-6c8d7f9b4-mn2kq","name":"notify-svc",'
           '"queue_depth":911,"oldest_age_s":1841,"msg":"retry queue backlog growing"}' % ems(T(15, 30, 2, 4))),
    ]
    run_bytes(lf, int(36 * MB * SCALE), noise, specials)
    lf.close()
    stats["apps/notify-svc/notify.pino.json"] = lf


# ======================================================================================
# 8. PostgreSQL server log
# ======================================================================================
def gen_postgres():
    lf = LogFile("db/postgresql-2026-07-28.log")

    def noise(t, i):
        r = rnd.random()
        pid = 24000 + rnd.randrange(140) * 3
        if r < 0.55:
            d = 0.2 + (rnd.random() ** 4) * 140.0
            if t >= T_SLOW and rnd.random() < 0.25:
                d = 900.0 + rnd.random() * 8000
            return ('%s [%d] app_runtime@shopdb LOG:  duration: %.3f ms  execute <unnamed>: %s'
                    % (pg_ts(t), pid, d,
                       ["SELECT sku_id, title, price_minor FROM catalog_items WHERE sku_id = $1",
                        "SELECT qty FROM inventory_reservations WHERE sku_id = $1 AND warehouse_id = $2",
                        "INSERT INTO orders (id, customer_id, amount_minor, currency) VALUES ($1,$2,$3,$4)",
                        "UPDATE inventory_reservations SET qty = qty - $1 WHERE sku_id = $2 AND warehouse_id = $3",
                        "SELECT c.sku_id, c.title, c.attrs FROM catalog_items c WHERE c.attrs ->> 'vendor_ref' = $1 "
                        "ORDER BY c.updated_at DESC"][rnd.randrange(5)]))
        if r < 0.72:
            w = 18.0 + rnd.random() * 44
            return ('%s [%d] LOG:  checkpoint complete: wrote %d buffers (%.1f%%); 0 WAL file(s) added, '
                    '0 removed, %d recycled; write=%.3f s, sync=%.3f s, total=%.3f s' %
                    (pg_ts(t), pid, 4000 + rnd.randrange(21000), rnd.random() * 9.0, rnd.randrange(12),
                     w, 0.004 + rnd.random() / 40, w + 0.9))
        if r < 0.86:
            return ('%s [%d] LOG:  automatic vacuum of table "shopdb.public.%s": index scans: 1, pages: 0 removed, '
                    '%d remain, tuples: %d removed, %d remain, %d are dead but not yet removable'
                    % (pg_ts(t), pid, ["orders", "catalog_items", "inventory_reservations",
                                       "order_events"][rnd.randrange(4)],
                       10000 + rnd.randrange(90000), rnd.randrange(4000), 200000 + rnd.randrange(900000),
                       rnd.randrange(900)))
        return ('%s [%d] app_runtime@shopdb LOG:  statement: SELECT pg_advisory_unlock_all()' % (pg_ts(t), pid))

    deadlock = [
        '%s [24417] app_runtime@shopdb ERROR:  deadlock detected' % pg_ts(T_DEADLOCK),
        '%s [24417] app_runtime@shopdb DETAIL:  Process 24417 waits for ShareLock on transaction 918237; '
        'blocked by process 24402.' % pg_ts(T_DEADLOCK),
        '\tProcess 24402 waits for ShareLock on transaction 918241; blocked by process 24417.',
        '\tProcess 24417: UPDATE inventory_reservations SET qty = qty - $1 WHERE sku_id = $2 AND warehouse_id = $3',
        "\tProcess 24402: SELECT c.sku_id, c.title, c.attrs FROM catalog_items c JOIN inventory_reservations r "
        "ON r.sku_id = c.sku_id WHERE c.attrs ->> 'vendor_ref' = $1 ORDER BY c.updated_at DESC FOR UPDATE",
        '%s [24417] app_runtime@shopdb HINT:  See server log for query details.' % pg_ts(T_DEADLOCK),
        '%s [24417] app_runtime@shopdb CONTEXT:  while updating tuple (48,12) in relation "inventory_reservations"'
        % pg_ts(T_DEADLOCK),
        '%s [24417] app_runtime@shopdb STATEMENT:  UPDATE inventory_reservations SET qty = qty - $1 '
        'WHERE sku_id = $2 AND warehouse_id = $3' % pg_ts(T_DEADLOCK + 0.001),
    ]

    specials = [
        sp(T_SLOW, "D04",
           "the un-indexed JSONB lookup introduced by catalog-svc 4.7.2, 8.8 s per execution",
           ['%s [24390] app_runtime@shopdb LOG:  duration: 8842.113 ms  execute <unnamed>: '
            "SELECT c.sku_id, c.title, c.attrs FROM catalog_items c WHERE c.attrs ->> 'vendor_ref' = $1 "
            'ORDER BY c.updated_at DESC' % pg_ts(T_SLOW),
            '%s [24390] app_runtime@shopdb DETAIL:  parameters: $1 = \'VND-7741-A\'' % pg_ts(T_SLOW),
            '%s [24390] app_runtime@shopdb LOG:  temporary file: path "base/pgsql_tmp/pgsql_tmp24390.0", size 91230208'
            % pg_ts(T_SLOW + 0.002)]),
        sp(T(13, 18, 2, 41), "D04",
           "lock waits pile up on inventory_reservations -- this is what drains the inventory-svc pool",
           ['%s [24402] app_runtime@shopdb LOG:  process 24402 still waiting for ShareLock on transaction 918201 '
            'after 1000.114 ms' % pg_ts(T(13, 18, 2, 41)),
            '%s [24402] app_runtime@shopdb DETAIL:  Process holding the lock: 24390. Wait queue: 24402, 24411, '
            '24418, 24422, 24431, 24444, 24451, 24460.' % pg_ts(T(13, 18, 2, 41)),
            '%s [24402] app_runtime@shopdb CONTEXT:  while locking tuple (48,12) in relation "inventory_reservations"'
            % pg_ts(T(13, 18, 2, 41))]),
        sp(T_DEADLOCK, "D04", "deadlock between the catalog vendor_ref FOR UPDATE scan and the inventory decrement",
           deadlock),
        sp(T(13, 33, 12, 8), "D04",
           "second deadlock, same pair of statements",
           '%s [24455] app_runtime@shopdb ERROR:  deadlock detected' % pg_ts(T(13, 33, 12, 8))),
        sp(T(15, 9, 41, 400), "D04",
           "index build that resolves the lock contention",
           ['%s [26001] app_admin@shopdb LOG:  statement: CREATE INDEX CONCURRENTLY '
            "idx_catalog_items_vendor_ref ON catalog_items ((attrs ->> 'vendor_ref'))" % pg_ts(T(15, 9, 41, 400)),
            '%s [26001] app_admin@shopdb LOG:  duration: 41221.882 ms' % pg_ts(T(15, 10, 22, 622))]),
    ]
    run_bytes(lf, int(20 * MB * SCALE), noise, specials)
    lf.close()
    stats["db/postgresql-2026-07-28.log"] = lf


# ======================================================================================
# 9. Kafka broker log
# ======================================================================================
def gen_kafka():
    lf = LogFile("kafka/server.log")

    def noise(t, i):
        y, mo, d, h, mi, s, _ = parts(t)
        ts = "%04d-%02d-%02d %02d:%02d:%02d,%03d" % (y, mo, d, h, mi, s, _ms(t))
        r = rnd.random()
        frac = (t - W0) / float(W1 - W0)
        off = 5500000 + int(frac * 22000) + rnd.randrange(400)
        if r < 0.45:
            return ('[%s] INFO [GroupMetadataManager brokerId=3] Removed %d expired offsets in %d milliseconds. '
                    '(kafka.coordinator.group.GroupMetadataManager)' % (ts, rnd.randrange(4), 1 + rnd.randrange(6)))
        if r < 0.70:
            return ('[%s] INFO [ProducerStateManager partition=%s-%d] Wrote producer snapshot at offset %d with '
                    '%d producer ids in %d ms. (kafka.log.ProducerStateManager)'
                    % (ts, ["orders.events", "inventory.updates", "catalog.changes",
                            "notify.outbox"][rnd.randrange(4)], rnd.randrange(6), off, 2 + rnd.randrange(30),
                       1 + rnd.randrange(9)))
        if r < 0.85:
            return ('[%s] INFO [Log partition=%s-%d, dir=/var/lib/kafka/data] Rolled new log segment at offset %d '
                    'in %d ms. (kafka.log.Log)'
                    % (ts, ["orders.events", "inventory.updates", "catalog.changes"][rnd.randrange(3)],
                       rnd.randrange(6), off, 1 + rnd.randrange(40)))
        return ('[%s] INFO [Partition %s-%d broker=3] ISR updated to 3,1,2 and version updated to %d '
                '(kafka.cluster.Partition)'
                % (ts, ["orders.events", "inventory.updates"][rnd.randrange(2)], rnd.randrange(6),
                   100 + rnd.randrange(900)))

    def kts(t):
        y, mo, d, h, mi, s, _ = parts(t)
        return "%04d-%02d-%02d %02d:%02d:%02d,%03d" % (y, mo, d, h, mi, s, _ms(t))

    specials = [
        sp(T_RETENTION, "D05",
           "ROOT CAUSE of the lost notification: a dynamic config push set orders.events retention.ms to 600000 "
           "(10 minutes)",
           ['[%s] INFO Processing override for entityPath: topics/orders.events with config: '
            'HashMap(retention.ms -> 600000, segment.ms -> 300000) (kafka.server.DynamicConfigManager)'
            % kts(T_RETENTION),
            '[%s] INFO [Log partition=orders.events-3, dir=/var/lib/kafka/data] Loading producer state till offset '
            '5514901 with message format version 2 (kafka.log.Log)' % kts(T_RETENTION + 0.4)]),
        sp(T(13, 57, 41, 118), "D05",
           "segments containing offset 5512034 are deleted 5 minutes later because of the new retention",
           ['[%s] INFO [Log partition=orders.events-3, dir=/var/lib/kafka/data] Deleting segment LogSegment('
            'baseOffset=5510000, size=104857412, lastModifiedTime=1785243180000, largestRecordTimestamp=Some('
            '1785243402000)) due to retention time 600000ms breach (kafka.log.Log)' % kts(T(13, 57, 41, 118)),
            '[%s] INFO [Log partition=orders.events-3, dir=/var/lib/kafka/data] Incrementing log start offset to '
            '5514901 (kafka.log.Log)' % kts(T(13, 57, 41, 902))]),
        sp(T_REBAL, "D05",
           "notify-svc consumer group rebalances while its committed offset is already below the log start offset",
           ['[%s] INFO [GroupCoordinator 3]: Preparing to rebalance group notify-svc-consumers in state '
            'PreparingRebalance with old generation 41 (__consumer_offsets-27) (reason: removing member '
            'consumer-notify-svc-1-9a2f on heartbeat expiration) (kafka.coordinator.group.GroupCoordinator)'
            % kts(T_REBAL),
            '[%s] INFO [GroupCoordinator 3]: Stabilized group notify-svc-consumers generation 42 '
            '(__consumer_offsets-27) with 2 members (kafka.coordinator.group.GroupCoordinator)'
            % kts(T_REBAL + 61.4)]),
        sp(T(14, 3, 26, 55), "D05",
           "broker-side view of the out-of-range fetch that the consumer then resolves with reset-to-latest",
           '[%s] WARN [ReplicaManager broker=3] Fetch request with correlation id 8841 from client '
           'consumer-notify-svc-consumers-1 on partition orders.events-3 failed due to '
           'org.apache.kafka.common.errors.OffsetOutOfRangeException: Received request for offset 5512034 for '
           'partition orders.events-3, but we only have log segments in the range 5514901 to 5518220. '
           '(kafka.server.ReplicaManager)' % kts(T(14, 3, 26, 55))),
    ]
    run_bytes(lf, int(20 * MB * SCALE), noise, specials)
    lf.close()
    stats["kafka/server.log"] = lf


# ======================================================================================
# 10. systemd journald export + syslog + dmesg + auth.log
# ======================================================================================
BOOT_ID_A = "4f2a91c07b8e4d3a9f1e2c5b6a7d8e9f"
BOOT_ID_B = "b71c3e5d9a2f4c6b8d0e1f2a3b4c5d6e"


def journal_record(ts, ident, pid, prio, msg, host="node-a", boot=BOOT_ID_A, comm=None, exe=None, seq=None):
    comm = comm or ident
    exe = exe or ("/usr/bin/" + ident)
    return [
        "__CURSOR=s=6c0e3a0f9a3a4d0b8bd0f0f2a1c3d4e5;i=%x;b=%s;m=%x;t=%x;x=%016x"
        % (seq or 0x1a2b, boot, int((ts - BOOT_A) * 1000000), us(ts), (seq or 0x1a2b) * 2654435761 & 0xffffffffffffffff),
        "__REALTIME_TIMESTAMP=%d" % us(ts),
        "__MONOTONIC_TIMESTAMP=%d" % int((ts - BOOT_A) * 1000000),
        "_BOOT_ID=%s" % boot,
        "_TRANSPORT=stdout",
        "PRIORITY=%d" % prio,
        "SYSLOG_FACILITY=3",
        "SYSLOG_IDENTIFIER=%s" % ident,
        "_PID=%d" % pid,
        "_UID=0",
        "_GID=0",
        "_COMM=%s" % comm,
        "_EXE=%s" % exe,
        "_CMDLINE=%s --config=/etc/%s/config.yaml" % (exe, ident),
        "_MACHINE_ID=9c1d3f7a5b2e4c6d8f0a1b2c3d4e5f60",
        "_HOSTNAME=%s" % host,
        "MESSAGE=%s" % msg,
        "",
    ]


def gen_journald():
    lf = LogFile("systemd/journal-export-node-a.txt")

    def noise(t, i):
        r = rnd.random()
        if r < 0.45:
            return journal_record(
                t, "kubelet", 1188, 6,
                'I%02d%02d %02d:%02d:%02d.%06d    1188 kubelet.go:%d] "SyncLoop (PLEG): event for pod" pod="prod/%s" '
                'event={"ID":"%s","Type":"ContainerStarted"}'
                % (7, 28, parts(t)[3], parts(t)[4], parts(t)[5], (i * 7919) % 1000000, 2200 + (i % 400),
                   PODS["checkout-api"][i % 3], RIDS[i & 4095]), seq=0x100000 + i)
        if r < 0.70:
            return journal_record(
                t, "containerd", 941, 6,
                'time="%s" level=info msg="TaskExit event in podsandbox handler container_id:\\"%032x\\" '
                'pid:%d exit_status:0"' % (iso_ms(t), rnd.getrandbits(128), 20000 + (i % 40000)),
                comm="containerd", exe="/usr/bin/containerd", seq=0x200000 + i)
        if r < 0.85:
            return journal_record(
                t, "calico-node", 1402, 6,
                "%s [INFO][68] felix/int_dataplane.go %d: Applying dataplane updates ipsets=%d policies=%d"
                % (iso_ms(t), 1800 + (i % 200), i % 40, i % 12),
                comm="calico-node", exe="/usr/local/bin/calico-node", seq=0x300000 + i)
        return journal_record(
            t, "systemd", 1, 6,
            "Started Session %d of user deploy." % (10000 + i % 9000),
            comm="systemd", exe="/usr/lib/systemd/systemd", seq=0x400000 + i)

    specials = [
        sp(T_OOM, "D03",
           "kubelet records the OOM kill of inventory-svc-...-2xq7z (journald export format, MESSAGE= field)",
           journal_record(T_OOM, "kubelet", 1188, 3,
                          'E0728 13:40:12.884213    1188 kubelet_pods.go:1442] "Pod terminated by OOM killer" '
                          'pod="prod/inventory-svc-7d9c4b8f6-2xq7z" containerName="inventory-svc" '
                          'exitCode=137 reason="OOMKilled"', seq=0x500001)),
        sp(T(13, 40, 13, 100), "D03",
           "container runtime confirms exit code 137",
           journal_record(T(13, 40, 13, 100), "containerd", 941, 4,
                          'time="%s" level=warning msg="container exited with 137" '
                          'container_id:"3a7f2b1c9d8e4f5a6b7c8d9e0f1a2b3d" pod="inventory-svc-7d9c4b8f6-2xq7z"'
                          % iso_ms(T(13, 40, 13, 100)),
                          comm="containerd", exe="/usr/bin/containerd", seq=0x500002)),
        sp(T(13, 41, 2, 41), "D03",
           "kubelet backing off restarts -> that is why there are no healthy endpoints for ~40 minutes",
           journal_record(T(13, 41, 2, 41), "kubelet", 1188, 4,
                          'I0728 13:41:02.041882    1188 kuberuntime_manager.go:1044] "Back-off restarting failed '
                          'container" container="inventory-svc" pod="prod/inventory-svc-7d9c4b8f6-2xq7z" '
                          'backoff="40s"', seq=0x500003)),
        sp(T(13, 31, 3, 800), "D06",
           "the API-server side of the unauthorized scale: kubelet observes the replica set shrink",
           journal_record(T(13, 31, 3, 800), "kubelet", 1188, 6,
                          'I0728 13:31:03.800112    1188 kubelet.go:2506] "SyncLoop DELETE" source="api" '
                          'pods=["prod/inventory-svc-7d9c4b8f6-k9p2w","prod/inventory-svc-7d9c4b8f6-m3r7t",'
                          '"prod/inventory-svc-7d9c4b8f6-z5c8y","prod/inventory-svc-7d9c4b8f6-b1v6s"]',
                          seq=0x500004)),
        sp(T(9, 41, 2, 12), "D11",
           "the NetworkPolicy that silently blocks egress to cbr.ru was applied on 2026-07-21 and is still active",
           journal_record(T(9, 41, 2, 12), "calico-node", 1402, 6,
                          "%s [INFO][68] felix/policy_resolver.go 231: Policy applied id=default/egress-default-deny "
                          "generation=7 appliedAt=2026-07-21T08:14:02Z selector=all() "
                          "egressRules=[allow->cluster,allow->10.0.0.0/8,deny->0.0.0.0/0]" % iso_ms(T(9, 41, 2, 12)),
                          comm="calico-node", exe="/usr/local/bin/calico-node", seq=0x500005)),
    ]
    run_bytes(lf, int(26 * MB * SCALE), noise, specials)
    lf.close()
    stats["systemd/journal-export-node-a.txt"] = lf


def gen_syslog():
    # ---- node-a syslog -----------------------------------------------------------------
    lf = LogFile("syslog/node-a/syslog")

    def noise(t, i):
        r = rnd.random()
        if r < 0.4:
            return ('%s node-a kubelet[1188]: I0728 %02d:%02d:%02d.%06d    1188 prober.go:107] '
                    'Probe succeeded pod="prod/%s" probe="readiness"'
                    % (sysl(t), parts(t)[3], parts(t)[4], parts(t)[5], (i * 7919) % 1000000,
                       PODS["checkout-api"][i % 3]))
        if r < 0.7:
            return ('%s node-a containerd[941]: time="%s" level=info msg="RemoveContainer for \\"%032x\\" returns '
                    'successfully"' % (sysl(t), iso_ms(t), rnd.getrandbits(128)))
        if r < 0.85:
            return '%s node-a systemd[1]: Started Kubernetes transient mount for volume %d.' % (sysl(t), i % 9000)
        return ('%s node-a chronyd[812]: Selected source 10.42.0.2 (offset %+.6f seconds)'
                % (sysl(t), (rnd.random() - 0.5) / 500.0))

    specials = [
        sp(T_SCALE + 0.4, "D06",
           "syslog copy of the sudo invocation that halved the inventory-svc replica count",
           '%s node-a sudo:  deploy : TTY=pts/2 ; PWD=/home/deploy ; USER=root ; '
           'COMMAND=/usr/bin/kubectl -n prod scale deploy/inventory-svc --replicas=2' % sysl(T_SCALE + 0.4)),
        sp(T(13, 41, 7, 200), "D03",
           "readiness probes failing for every remaining inventory-svc pod",
           ['%s node-a kubelet[1188]: I0728 13:41:07.200114    1188 prober.go:107] Readiness probe failed: '
            'Get "http://10.42.12.31:8080/healthz": dial tcp 10.42.12.31:8080: connect: connection refused'
            % sysl(T(13, 41, 7, 200)),
            '%s node-a kubelet[1188]: I0728 13:41:08.114552    1188 prober.go:107] Readiness probe failed: '
            'Get "http://10.42.12.33:8080/healthz": context deadline exceeded' % sysl(T(13, 41, 8, 114))]),
    ]
    run_bytes(lf, int(12 * MB * SCALE), noise, specials)
    lf.close()
    stats["syslog/node-a/syslog"] = lf

    # ---- node-b syslog: clock is +47 s ahead of node-a --------------------------------
    lf = LogFile("syslog/node-b/syslog")
    SKEW = 47.0

    def noise_b(t, i):
        r = rnd.random()
        if r < 0.5:
            return ('%s node-b kubelet[1201]: I0728 %02d:%02d:%02d.%06d    1201 prober.go:107] Probe succeeded '
                    'pod="prod/%s" probe="liveness"'
                    % (sysl(t + SKEW), parts(t + SKEW)[3], parts(t + SKEW)[4], parts(t + SKEW)[5],
                       (i * 6113) % 1000000, PODS["promo-engine"][0]))
        if r < 0.8:
            return ('%s node-b containerd[955]: time="%s" level=info msg="StartContainer for \\"%032x\\" returns '
                    'successfully"' % (sysl(t + SKEW), iso_ms(t + SKEW), rnd.getrandbits(128)))
        return ('%s node-b chronyd[820]: Can\'t synchronise: no selectable sources' % sysl(t + SKEW))

    specials_b = [
        sp(T(9, 2, 0, 0), "D12",
           "node-b clock is 47 s ahead of node-a and chrony cannot fix it -- every node-b stamp is skewed",
           '%s node-b chronyd[820]: System clock wrong by +47.114882 seconds, adjustment deferred '
           '(makestep disabled)' % sysl(T(9, 2, 0, 0) + SKEW)),
        sp(T_HERRING, "D12",
           "RED HERRING: SYN-flood / conntrack alarm on node-b, ~5.5 hours BEFORE the incident and on a node "
           "that runs neither inventory-svc nor checkout-api",
           ['%s node-b kernel: [%12.6f] TCP: request_sock_TCP: Possible SYN flooding on port 443. Sending cookies.  '
            'Check SNMP counters.' % (sysl(T_HERRING + SKEW), T_HERRING - BOOT_B),
            '%s node-b kernel: [%12.6f] nf_conntrack: nf_conntrack: table full, dropping packet'
            % (sysl(T_HERRING + SKEW + 0.9), T_HERRING + 0.9 - BOOT_B),
            '%s node-b kernel: [%12.6f] nf_conntrack: nf_conntrack: table full, dropping packet'
            % (sysl(T_HERRING + SKEW + 1.1), T_HERRING + 1.1 - BOOT_B)]),
    ]
    run_bytes(lf, int(9 * MB * SCALE), noise_b, specials_b, t0=ep(2026, 7, 28, 7, 0, 0), t1=W1)
    lf.close()
    stats["syslog/node-b/syslog"] = lf

    # ---- auth.log (node-a): the D06 needle ---------------------------------------------
    lf = LogFile("syslog/node-a/auth.log")

    def anoise(t, i):
        r = rnd.random()
        cpid = 20000 + rnd.randrange(40000)
        if r < 0.45:
            return ('%s node-a CRON[%d]: pam_unix(cron:session): session opened for user root(uid=0) by (uid=0)'
                    % (sysl(t), cpid))
        if r < 0.9:
            return ('%s node-a CRON[%d]: pam_unix(cron:session): session closed for user root'
                    % (sysl(t), cpid))
        return ('%s node-a sshd[%d]: Invalid user %s from %s port %d'
                % (sysl(t), 30000 + rnd.randrange(9000),
                   ["admin", "test", "oracle", "ubuntu", "postgres"][rnd.randrange(5)],
                   PUB_IPS[rnd.randrange(1024)], 40000 + rnd.randrange(20000)))

    specials = [
        sp(T_SSH, "D06",
           "NEEDLE 1/3: interactive publickey login by `deploy` from a workstation subnet during the incident",
           ['%s node-a sshd[30122]: Accepted publickey for deploy from 10.42.7.9 port 55214 ssh2: ED25519 '
            'SHA256:9Xf3lQ0kRr5m0PmYd0K1n2r5Tt7cVv9aBcDeFgHiJkL' % sysl(T_SSH),
            '%s node-a sshd[30122]: pam_unix(sshd:session): session opened for user deploy(uid=1002) by (uid=0)'
            % sysl(T_SSH + 0.2)]),
        sp(T_SCALE, "D06",
           "NEEDLE 2/3: the unauthorized `kubectl scale --replicas=2` (deployment was running 6)",
           '%s node-a sudo:  deploy : TTY=pts/2 ; PWD=/home/deploy ; USER=root ; '
           'COMMAND=/usr/bin/kubectl -n prod scale deploy/inventory-svc --replicas=2' % sysl(T_SCALE)),
        sp(T(13, 52, 44, 0), "D06",
           "NEEDLE 3/3: same session also pushed the kafka topic config that causes D05",
           ['%s node-a sudo:  deploy : TTY=pts/2 ; PWD=/home/deploy ; USER=root ; '
            'COMMAND=/opt/kafka/bin/kafka-configs.sh --bootstrap-server kafka-1:9092 --alter --entity-type topics '
            '--entity-name orders.events --add-config retention.ms=600000,segment.ms=300000'
            % sysl(T(13, 52, 44, 0)),
            '%s node-a sshd[30122]: pam_unix(sshd:session): session closed for user deploy'
            % sysl(T(13, 54, 11, 0))]),
    ]
    run_bytes(lf, int(6 * MB * SCALE), anoise, specials)
    lf.close()
    stats["syslog/node-a/auth.log"] = lf

    # ---- dmesg (node-a) ----------------------------------------------------------------
    lf = LogFile("syslog/node-a/dmesg")

    def dnoise(t, i):
        up = t - BOOT_A
        r = rnd.random()
        if r < 0.5:
            return ('[%12.6f] IPv6: ADDRCONF(NETDEV_CHANGE): veth%08x: link becomes ready' % (up, rnd.getrandbits(32)))
        if r < 0.8:
            return ('[%12.6f] cni0: port %d(veth%08x) entered forwarding state' % (up, i % 400, rnd.getrandbits(32)))
        return ('[%12.6f] device veth%08x entered promiscuous mode' % (up, rnd.getrandbits(32)))

    oom = [
        '[%12.6f] python3 invoked oom-killer: gfp_mask=0xcc0(GFP_KERNEL), order=0, oom_score_adj=969'
        % (T_OOM - BOOT_A),
        '[%12.6f] CPU: 2 PID: 24417 Comm: python3 Not tainted 6.8.0-45-generic #45-Ubuntu' % (T_OOM - BOOT_A),
        '[%12.6f] memory: usage 2097152kB, limit 2097152kB, failcnt 88213' % (T_OOM - BOOT_A + 0.000112),
        '[%12.6f] Memory cgroup stats for /kubepods.slice/kubepods-burstable.slice/'
        'kubepods-burstable-pod9f2c1d7e_4b6a_4c31_8e55_1a2b3c4d5e6f.slice: cache:412KB rss:2096740KB '
        'shmem:0KB mapped_file:2048KB' % (T_OOM - BOOT_A + 0.000180),
        '[%12.6f] Tasks state (memory values in pages):' % (T_OOM - BOOT_A + 0.000201),
        '[%12.6f] [  pid  ]   uid  tgid total_vm      rss pgtables_bytes swapents oom_score_adj name'
        % (T_OOM - BOOT_A + 0.000212),
        '[%12.6f] [  24417]  1000 24417  1355797   524185  5242880        0           969 python3'
        % (T_OOM - BOOT_A + 0.000230),
        '[%12.6f] oom-kill:constraint=CONSTRAINT_MEMCG,nodemask=(null),cpuset=cri-containerd-'
        '3a7f2b1c9d8e4f5a6b7c8d9e0f1a2b3d.scope,mems_allowed=0,oom_memcg=/kubepods.slice/kubepods-burstable.slice/'
        'kubepods-burstable-pod9f2c1d7e_4b6a_4c31_8e55_1a2b3c4d5e6f.slice,task_memcg=/kubepods.slice/'
        'kubepods-burstable.slice/kubepods-burstable-pod9f2c1d7e_4b6a_4c31_8e55_1a2b3c4d5e6f.slice/'
        'cri-containerd-3a7f2b1c9d8e4f5a6b7c8d9e0f1a2b3d.scope,task=python3,pid=24417,uid=1000'
        % (T_OOM - BOOT_A + 0.000244),
        '[%12.6f] Memory cgroup out of memory: Killed process 24417 (python3) total-vm:5423188kB, '
        'anon-rss:2088412kB, file-rss:29184kB, shmem-rss:0kB, UID:1000 pgtables:5120kB oom_score_adj:969'
        % (T_OOM - BOOT_A + 0.000261),
    ]
    specials = [sp(T_OOM, "D03",
                   "kernel OOM killer terminates the inventory-svc python process (cgroup limit 2Gi). "
                   "NOTE: dmesg stamps are seconds-since-boot, boot was 2026-07-25T04:11:03Z", oom)]
    run_bytes(lf, int(3 * MB * SCALE), dnoise, specials)
    lf.close()
    stats["syslog/node-a/dmesg"] = lf


# ======================================================================================
# 11. kubectl get events -- repeated snapshots
# ======================================================================================
def age(sec):
    sec = int(max(0, sec))
    if sec < 120:
        return "%ds" % sec
    if sec < 3600:
        return "%dm%ds" % (sec // 60, sec % 60)
    return "%dh%dm" % (sec // 3600, (sec % 3600) // 60)


EV_NOISE = [
    ("Normal", "Scheduled", "pod/%s", "Successfully assigned prod/%s to node-a"),
    ("Normal", "Pulled", "pod/%s", 'Container image "registry.acme.internal/%s" already present on machine'),
    ("Normal", "Created", "pod/%s", "Created container %s"),
    ("Normal", "Started", "pod/%s", "Started container %s"),
    ("Warning", "Unhealthy", "pod/%s", "Readiness probe failed: HTTP probe failed with statuscode: 503"),
    ("Normal", "Killing", "pod/%s", "Stopping container %s"),
    ("Normal", "SuccessfulCreate", "replicaset/%s", "Created pod: %s"),
]
SVC_NAMES = list(PODS.keys())
SVC_VERSION = {"checkout-api": "2.31.0", "catalog-svc": "4.7.2", "inventory-svc": "3.4.0",
               "payments-worker": "1.19.4", "notify-svc": "2.8.1", "promo-engine": "0.9.14",
               "ordersync": "1.2.0"}


def svc_image(svc, t):
    v = SVC_VERSION[svc]
    if svc == "catalog-svc" and t < T_DEPLOY:
        v = "4.7.1"          # the pre-rollout tag: a second, independent way to date the change
    return "%s:%s" % (svc, v)


def gen_k8s_events():
    lf = LogFile("k8s/events-prod-eu-1.txt")
    hdr = "NAMESPACE   LAST SEEN   TYPE      REASON                    OBJECT                                          MESSAGE"
    specials_by_time = [
        (T_DEPLOY, "D04", "the catalog-svc 4.7.2 rollout event",
         "prod        %s   Normal    ScalingReplicaSet         deployment/catalog-svc                          "
         "Scaled up replica set catalog-svc-6b7d9c4f8 to 2 (image registry.acme.internal/catalog-svc:4.7.2)"),
        (T_SCALE + 1.2, "D06", "the replica count drops 6 -> 2 in the middle of the incident (nobody announced it)",
         "prod        %s   Normal    ScalingReplicaSet         deployment/inventory-svc                        "
         "Scaled down replica set inventory-svc-7d9c4b8f6 to 2 from 6"),
        (T_OOM, "D03", "OOMKilling event for the inventory-svc pod",
         "prod        %s   Warning   OOMKilling                pod/inventory-svc-7d9c4b8f6-2xq7z               "
         "Memory cgroup out of memory: Killed process 24417 (python3) total-vm:5423188kB, anon-rss:2088412kB"),
        (T_OOM + 5, "D03", "container exit 137 / restart",
         "prod        %s   Warning   BackOff                   pod/inventory-svc-7d9c4b8f6-2xq7z               "
         "Back-off restarting failed container inventory-svc in pod inventory-svc-7d9c4b8f6-2xq7z_prod"),
        (T(13, 42, 2, 0), "D03", "the service has no endpoints at all -> Envoy UH/no_healthy_upstream",
         "prod        %s   Warning   FailedToUpdateEndpoint    endpoints/inventory-svc                         "
         "Failed to update endpoint prod/inventory-svc: no ready addresses"),
        (T_FIX, "D06", "capacity restored by hand at 15:10",
         "prod        %s   Normal    ScalingReplicaSet         deployment/inventory-svc                        "
         "Scaled up replica set inventory-svc-7d9c4b8f6 to 6 from 2"),
    ]
    marked = set()
    snap_t = W0
    while snap_t <= W1:
        lf.w("=== capture: kubectl get events -A --sort-by=.lastTimestamp @ %s (context prod-eu-1) ==="
             % iso_ms(snap_t))
        lf.w(hdr)
        for j in range(170):
            typ, reason, objf, msgf = EV_NOISE[j % 7]
            svc = SVC_NAMES[j % len(SVC_NAMES)]
            pod = PODS[svc][j % len(PODS[svc])]
            obj = objf % (pod if objf.startswith("pod/") else pod.rsplit("-", 1)[0])
            n = msgf.count("%s")
            if n == 0:
                msg = msgf
            elif reason in ("Created", "Started", "Killing"):
                msg = msgf % svc
            elif reason == "Pulled":
                msg = msgf % svc_image(svc, snap_t)
            elif reason == "SuccessfulCreate":
                msg = msgf % pod
            else:
                msg = msgf % tuple([pod] * n)
            a = age(snap_t - (W0 + (j * 97) % 21000))
            lf.w("prod        %-9s   %-7s   %-24s  %-46s  %s" % (a, typ, reason, obj, msg))
        for (evt, did, note, row) in specials_by_time:
            if evt <= snap_t <= evt + 3600:
                line = row % ("%-9s" % age(snap_t - evt))
                if (did, note) not in marked:
                    lf.mark(did, line, note + " (first snapshot containing it; it repeats in later snapshots)")
                    marked.add((did, note))
                else:
                    lf.w(line)
        lf.w("")
        snap_t += 300
    lf.close()
    stats["k8s/events-prod-eu-1.txt"] = lf


# ======================================================================================
# 12. k8s pod logs (CRI prefix:  <RFC3339Nano> <stream> <F|P> <line>)
# ======================================================================================
def gen_pod_logs():
    # inventory-svc pod log: python lines wrapped in the CRI prefix, with P/F partial splits
    lf = LogFile("k8s/pods/inventory-svc-7d9c4b8f6-2xq7z_prod_inventory-svc.log")

    def noise(t, i):
        body = ('%s INFO [inventory.api] reserve ok sku=%s qty=%d warehouse=WH-%d correlation_id=%s'
                % (comma_ts(t), SKUS[i % 3000], 1 + (i % 5), 1 + (i % 4), RIDS[i & 4095]))
        if i % 211 == 0:
            # a >16KiB line gets split by the runtime into P (partial) chunks
            long = body + " payload=" + ("A" * 300)
            a, b, c = long[:120], long[120:260], long[260:]
            return ['%s stdout P %s' % (nano(t), a),
                    '%s stdout P %s' % (nano(t), b),
                    '%s stdout F %s' % (nano(t), c)]
        return '%s stdout F %s' % (nano(t), body)

    specials = [
        sp(T(13, 39, 51, 12), "D03",
           "the pod's own last lines before exit 137 (CRI-prefixed)",
           ['%s stderr F %s WARNING [inventory.runtime] rss=2036MB limit=2048MB inflight=2288 queue_depth=4711'
            % (nano(T(13, 39, 51, 12)), comma_ts(T(13, 39, 51, 12))),
            '%s stderr F %s ERROR [inventory.db] connection pool acquire timeout after 5.0s (waiters=4711)'
            % (nano(T(13, 39, 58, 400)), comma_ts(T(13, 39, 58, 400)))]),
    ]
    run_bytes(lf, int(9 * MB * SCALE), noise, specials, t0=W0, t1=T_OOM)
    lf.close()
    stats["k8s/pods/inventory-svc-7d9c4b8f6-2xq7z_prod_inventory-svc.log"] = lf

    # istio-proxy sidecar pod log = envoy TEXT lines inside CRI prefix (double-wrapped)
    lf = LogFile("k8s/pods/inventory-svc-7d9c4b8f6-2xq7z_prod_istio-proxy.log")

    def noise2(t, i):
        return '%s stdout F %s' % (nano(t), envoy_text_line(t, i))

    specials = [
        sp(T(13, 40, 44, 100), "D03",
           "sidecar reports the local app is gone (connection refused to 127.0.0.1:8080)",
           '%s stderr F %s\t[warning][config] [external/envoy/source/common/upstream/health_discovery_service.cc:322] '
           'health check failed for cluster inbound|8080|| : delayed_connect_error: 111'
           % (nano(T(13, 40, 44, 100)), iso_ms(T(13, 40, 44, 100)))),
    ]
    run_bytes(lf, int(7 * MB * SCALE), noise2, specials, t0=W0, t1=W1)
    lf.close()
    stats["k8s/pods/inventory-svc-7d9c4b8f6-2xq7z_prod_istio-proxy.log"] = lf


# ======================================================================================
# 13. In-house promo-engine, bespoke pipe format (D10) + Russian billing adapter (D11)
# ======================================================================================
PSEV = ["CHATTER", "CHATTER", "CHATTER", "NOTE", "NOTE", "WARN", "TRACE"]


def promo_line(t, i, sev=None, kind=None, extra=""):
    sev = sev or PSEV[rnd.randrange(7)]
    kind = kind or ["RULE_EVAL", "CACHE_LOAD", "QUOTE", "RULE_APPLY", "HEARTBEAT"][rnd.randrange(5)]
    k = rnd.randrange(5)
    base = AMOUNTS[rnd.randrange(len(AMOUNTS))]
    pct = [0.0, 10.0, 15.0, 5.0, 25.0][k]
    return ("%s|promo-engine|node-b|%s|%s|RID~%s|order=%s|rule=%s|base_minor=%d|discount_pct=%.1f|final_minor=%d%s"
            % (promo_ts(t), sev, kind, RIDS[i & 4095].replace("-", "")[:12], order_at(t),
               ["SUMMER26", "LOYALTY10", "WELCOME15", "SUMMER26_STACK", "FREESHIP"][k],  # noqa
               base, pct, int(base * (100.0 - pct) / 100.0), extra))


def gen_promo():
    lf = LogFile("inhouse/promo-engine.plog")
    SKEW = 47.0   # node-b clock skew, on top of the +0300 offset

    def noise(t, i):
        return promo_line(t + SKEW, i)

    specials = []
    for k in range(11):
        tt = T_PROMO + k * 137.0
        specials.append(sp(tt, "D10",
                           "stacked-discount blow-up #%d/11: discount_pct > 100 produces a NEGATIVE final_minor"
                           % (k + 1),
                           "%s|promo-engine|node-b|ALARM|RULE_APPLY|RID~%s|order=ORD-88%03d|rule=SUMMER26_STACK|"
                           "base_minor=1299900|discount_pct=%.1f|final_minor=%d|"
                           "msg=stacked SUMMER26+LOYALTY10+WELCOME15 multiplied, no ceiling applied"
                           % (promo_ts(tt + SKEW), RIDS[2000 + k].replace("-", "")[:12], 240 + k,
                              137.5 + k * 0.4, -(486212 + k * 5200))))
    specials.append(sp(T(14, 33, 4, 0), "D10",
                       "the downstream effect: the negative total is passed on to the ledger as a credit",
                       "%s|promo-engine|node-b|FATALITY|LEDGER_POST|RID~%s|order=ORD-88240|rule=SUMMER26_STACK|"
                       "base_minor=1299900|discount_pct=137.5|final_minor=-486212|"
                       "msg=ledger refused negative charge, order left in state PENDING_MANUAL"
                       % (promo_ts(T(14, 33, 4, 0) + SKEW), RIDS[2011].replace("-", "")[:12])))
    specials.append(sp(T(14, 1, 57, 895), "D05",
                       "ORD-88231 in the in-house format: the correlation id is TRUNCATED to 12 hex chars (RID~)",
                       "%s|promo-engine|node-b|NOTE|RULE_APPLY|RID~%s|order=%s|rule=SUMMER26|base_minor=1299900|"
                       "discount_pct=10.0|final_minor=1169910|msg=promo applied, code=summer26"
                       % (promo_ts(T(14, 1, 57, 895) + SKEW), RID_881_S, ORD_881)))
    specials.append(sp(T(14, 12, 32, 900), "D01",
                       "the lowercase promo code that trips the Java NPE one second later",
                       "%s|promo-engine|node-b|WARN|RULE_EVAL|RID~41b8ee02c9a3|order=ORD-88407|rule=SUMMER26|"
                       "base_minor=899000|discount_pct=0.0|final_minor=899000|"
                       "msg=code normalisation rejected input code=summer26 pattern=^[A-Z0-9]{4,16}$"
                       % promo_ts(T(14, 12, 32, 900) + SKEW)))
    run_bytes(lf, int(11 * MB * SCALE), noise, specials)
    lf.close()
    stats["inhouse/promo-engine.plog"] = lf

    # promo-engine pod log = the bespoke format wrapped in the CRI prefix
    lf = LogFile("k8s/pods/promo-engine-5b6c7d8e9-w4t2m_prod_promo-engine.log")

    def noise3(t, i):
        return '%s stdout F %s' % (nano(t), promo_line(t + SKEW, i))

    specials = [sp(T_PROMO, "D10",
                   "same blow-up seen through kubectl logs: bespoke format nested inside the CRI prefix",
                   '%s stderr F %s|promo-engine|node-b|ALARM|RULE_APPLY|RID~%s|order=ORD-88240|'
                   'rule=SUMMER26_STACK|base_minor=1299900|discount_pct=137.5|final_minor=-486212|'
                   'msg=stacked SUMMER26+LOYALTY10+WELCOME15 multiplied, no ceiling applied'
                   % (nano(T_PROMO), promo_ts(T_PROMO + SKEW), RIDS[2000].replace("-", "")[:12]))]
    run_bytes(lf, int(6 * MB * SCALE), noise3, specials)
    lf.close()
    stats["k8s/pods/promo-engine-5b6c7d8e9-w4t2m_prod_promo-engine.log"] = lf


RU_LEVELS = ["ОТЛАДКА", "ИНФО", "ИНФО", "ИНФО", "ПРЕДУПРЕЖДЕНИЕ"]


def gen_russian():
    lf = LogFile("inhouse/billing-adapter-ru.log")

    def noise(t, i):
        return ("%s [бухгалтерия-адаптер] УРОВЕНЬ=%s поток=обработчик-%d | %s | заказ=%s сумма=%d валюта=%s"
                % (ru_ts(t), RU_LEVELS[rnd.randrange(5)], 1 + rnd.randrange(8),
                   ["проводка создана", "документ выгружен в 1С", "начислен НДС 20%",
                    "получен курс валют от ЦБ РФ", "счёт-фактура сформирован"][rnd.randrange(5)],
                   order_at(t - 10800), AMOUNTS[rnd.randrange(len(AMOUNTS))],
                   ["EUR", "USD", "RUB"][rnd.randrange(3)]))

    specials = [
        sp(T(9, 15, 2, 0), "D11",
           "the FX fetch has been timing out since 2026-07-21 but is only logged at ПРЕДУПРЕЖДЕНИЕ level",
           "%s [бухгалтерия-адаптер] УРОВЕНЬ=ПРЕДУПРЕЖДЕНИЕ поток=обработчик-2 | не удалось получить курс валют "
           "от ЦБ РФ (таймаут 5000 мс, адрес https://www.cbr.ru/scripts/XML_daily.asp), беру значение из кэша | "
           "кэш_от=21.07.2026 возраст_дней=7" % ru_ts(T(9, 15, 2, 0))),
        sp(T_FX, "D11",
           "ROOT CAUSE (Russian): the cache is 7 days stale, so every EUR order is converted at the 21.07 rate",
           ["%s [бухгалтерия-адаптер] УРОВЕНЬ=ОШИБКА поток=обработчик-4 | курс валют устарел: возраст кэша 7 суток "
            "превышает лимит 1 сутки, конвертация выполнена по устаревшему курсу | заказ=%s сумма=1299900 "
            "валюта=EUR курс=91.4412 курс_актуальный=96.8871 расхождение_проц=5.62"
            % (ru_ts(T_FX), ORD_881),
            "%s [бухгалтерия-адаптер] УРОВЕНЬ=ОШИБКА поток=обработчик-4 | сумма в рублях занижена на 70 812 коп. "
            "по сравнению с актуальным курсом | заказ=%s" % (ru_ts(T_FX + 0.04), ORD_881)]),
        sp(T(15, 2, 11, 0), "D11",
           "the network cause, stated in Russian: egress to cbr.ru is refused by the cluster policy",
           "%s [бухгалтерия-адаптер] УРОВЕНЬ=ОШИБКА поток=обработчик-1 | соединение с www.cbr.ru отклонено "
           "(сетевая политика egress-default-deny, применена 21.07.2026), повтор через 300 с | попытка=412"
           % ru_ts(T(15, 2, 11, 0))),
    ]
    run_bytes(lf, int(6 * MB * SCALE), noise, specials)
    lf.close()
    stats["inhouse/billing-adapter-ru.log"] = lf


# ======================================================================================
# 14. no-extension file; the service name exists ONLY in the filename
# ======================================================================================
def gen_ordersync():
    lf = LogFile("misc/ordersync-prod-2026-07-28")

    def noise(t, i):
        frac = (t - W0) / float(W1 - W0)
        return ("%s %-5s reconciliation batch %d: %d records compared, %d updated, %d skipped"
                % (comma_ts(t), ["INFO", "INFO", "INFO", "DEBUG", "WARN"][rnd.randrange(5)],
                   4200 + int(frac * 560), 200 + rnd.randrange(800), rnd.randrange(40), rnd.randrange(12)))

    specials = [
        sp(T_SYNC, "D05",
           "the only place that notices ORD-88231 was paid but never notified (file names the service; "
           "no line in the file does)",
           ["%s WARN  reconciliation batch 4471: order %s paid (%s) but no notification record; retry 1/5"
            % (comma_ts(T_SYNC), ORD_881, PSP_881),
            "%s WARN  reconciliation batch 4471: order %s paid (%s) but no notification record; retry 2/5"
            % (comma_ts(T_SYNC + 120.4), ORD_881, PSP_881)]),
        sp(T(14, 22, 3, 0), "D05",
           "ordersync gives up; 2867 orders are in the same state",
           ["%s ERROR reconciliation batch 4471: giving up on %s after 5 attempts, escalating to manual queue"
            % (comma_ts(T(14, 22, 3, 0)), ORD_881),
            "%s ERROR reconciliation summary: 2867 orders paid without notification between offsets 5512034-5514901"
            % (comma_ts(T(14, 22, 4, 0)))]),
    ]
    run_bytes(lf, int(4 * MB * SCALE), noise, specials)
    lf.close()
    stats["misc/ordersync-prod-2026-07-28"] = lf


# ======================================================================================
# 15. non-log files sprinkled in the tree (ingest-discipline trap)
# ======================================================================================
def gen_non_logs():
    os.makedirs(os.path.join(ROOT, "k8s"), exist_ok=True)
    with open(os.path.join(ROOT, "README.md"), "w", encoding="utf-8") as f:
        f.write("# prod-eu-1 log capture 2026-07-28\n\n"
                "Captured by the on-call bundle script during the 2026-07-28 incident window\n"
                "(09:00-16:00 UTC). Directories map to sources, not to services.\n\n"
                "WARNING: `haproxy/` and `inhouse/` are stamped in Europe/Moscow (UTC+3).\n"
                "`syslog/node-b/` and `inhouse/` come from node-b, whose clock is ~47s fast.\n"
                "This file is NOT a log. Neither is k8s/deployment-notes.md or nginx/nginx.conf.\n")
    with open(os.path.join(ROOT, "k8s", "deployment-notes.md"), "w", encoding="utf-8") as f:
        f.write("# deploy notes\n\n- 2026-07-28 11:00 catalog-svc 4.7.2 (vendor ref lookup) - approved CR-8841\n"
                "- 2026-07-28 12:00 inventory-svc 3.4.0 - no change to resources\n"
                "- inventory-svc requests: cpu 500m / memory 1Gi, limits: memory 2Gi\n"
                "- inventory-svc replicas: 6 (HPA disabled since 2026-05)\n")
    with open(os.path.join(ROOT, "nginx", "nginx.conf"), "w", encoding="utf-8") as f:
        f.write("upstream inventory_upstream {\n    server 10.42.12.31:8080 max_fails=3 fail_timeout=10s;\n"
                "    server 10.42.12.33:8080 max_fails=3 fail_timeout=10s;\n}\n\n"
                "log_format main '$remote_addr - $remote_user [$time_local] \"$request\" '\n"
                "                '$status $body_bytes_sent \"$http_referer\" \"$http_user_agent\" \"$http_x_forwarded_for\" '\n"
                "                'rt=$request_time uct=\"$upstream_connect_time\" uht=\"$upstream_header_time\" "
                "urt=\"$upstream_response_time\" rid=$http_x_request_id';\n")


# ======================================================================================
# answer key
# ======================================================================================
DEFECTS = [
    {
        "id": "D01",
        "title": "checkout-api NPE: PromoCode.normalized() returns null for lowercase promo codes",
        "difficulty": "single-format read (+ multiline stitching under interleaving)",
        "requires": "single-format read, multiline stitching",
        "description": "Every checkout that carries a promo code containing lowercase characters throws "
                       "NullPointerException inside PromoCodeResolver.resolve and the whole checkout request "
                       "fails with HTTP 500.",
        "root_cause": "PromoCode.normalized() validates against ^[A-Z0-9]{4,16}$ and returns null (instead of "
                      "throwing or upper-casing first) when the code does not match; PromoCodeResolver.java:88 "
                      "then calls .toUpperCase() on that null. The in-house promo-engine log shows the offending "
                      "input: code=summer26.",
        "formats": ["Java logback multi-line stack trace", "in-house pipe-delimited promo log"],
        "trap": "In checkout-api.log the NPE stack trace is INTERLEAVED LINE BY LINE with a concurrent "
                "SocketTimeoutException trace from thread http-nio-8080-exec-12. Naive 'a stack trace is the "
                "lines following an exception header' stitching produces a chimeric trace and the wrong "
                "conclusion (network timeout instead of a null).",
        "confused_with": "D03 (the 503 storm) -- the interleaved SocketTimeoutException frames belong to D03, "
                         "not to the NPE.",
    },
    {
        "id": "D02",
        "title": "payments-worker Go panic: index out of range in batch.Retrier.flush",
        "difficulty": "single-format read (inside Docker json-file escaping)",
        "requires": "single-format read, multiline stitching, JSON unescaping",
        "description": "payments-worker crashes (exit status 2) and is restarted by the container runtime, "
                       "losing the in-flight retry batch.",
        "root_cause": "Retrier.flush (/src/internal/batch/retrier.go:118) indexes the PSP response slice by the "
                      "REQUEST index; when the PSP returns a partial capture set (requested 4, returned 3 -- see "
                      "payments.zap.json) the index runs off the end. The partial responses only happen while the "
                      "PSP is being retried, i.e. only during the 503 storm, which is why the bug had never fired "
                      "before.",
        "formats": ["Docker json-file driver log", "Go zap JSON"],
        "trap": "The panic is not 12 lines of one log record: the docker json-file driver writes ONE JSON OBJECT "
                "PER PHYSICAL LINE with the newline escaped inside the \"log\" field. Grepping for 'panic:' finds "
                "one line; the goroutine trace has to be reassembled from 12 separate JSON records.",
        "confused_with": "D03 -- the panic is a CONSEQUENCE of the storm, not its cause; its timestamp is 24 "
                         "minutes after the 503s start.",
    },
    {
        "id": "D03",
        "title": "Istio 503 storm = inventory-svc OOMKilled and CrashLooping",
        "difficulty": "cross-format correlation",
        "requires": "cross-format correlation",
        "description": "From 13:41 to ~14:22 UTC the ingress gateway returns 503 (response_flags UF/UH) for "
                       "inventory-svc and URX for checkout-api; nginx logs 'connect() failed (111)' and 'no live "
                       "upstreams'; HAProxy marks the edge backend DOWN.",
        "root_cause": "inventory-svc pods exceeded the 2Gi memory cgroup limit and were killed by the kernel OOM "
                      "killer (dmesg + journald kubelet + k8s OOMKilling event), then entered restart back-off, "
                      "leaving the Service with no ready endpoints. The memory growth itself is D04 (request "
                      "queue) amplified by D06 (replicas halved).",
        "formats": ["Envoy JSON access log", "Envoy text access log", "nginx error log", "HAProxy log",
                    "kubectl events", "journald export", "dmesg", "syslog", "CRI pod log", "Python uvicorn log"],
        "trap": "Timestamps do not line up: HAProxy is stamped in Europe/Moscow (UTC+3) so its lines read 16:41, "
                "dmesg is stamped in seconds-since-boot (boot 2026-07-25T04:11:03Z), and journald uses "
                "microsecond epoch in __REALTIME_TIMESTAMP.",
        "confused_with": "D12 (SYN flood / conntrack table full on node-b) -- looks like a network-layer cause of "
                         "the 503s but is 5.5 h earlier and on the wrong node.",
    },
    {
        "id": "D04",
        "title": "The memory growth: catalog-svc 4.7.2 shipped an un-indexed JSONB vendor_ref lookup",
        "difficulty": "cross-format correlation (deepest hop)",
        "requires": "cross-format correlation",
        "description": "From 13:10 the same statement takes 8.8 s, holds row locks on inventory_reservations, "
                       "produces lock queues and two deadlocks; inventory-svc's asyncpg pool drains, requests "
                       "queue in memory and RSS climbs to the 2Gi limit.",
        "root_cause": "catalog-svc 4.7.2 (deployed 11:00:41) introduced "
                      "SELECT ... WHERE c.attrs ->> 'vendor_ref' = $1 ORDER BY c.updated_at DESC (later joined "
                      "FOR UPDATE) with NO expression index on (attrs ->> 'vendor_ref'). It seq-scans "
                      "catalog_items while holding locks. The 12:59 cache flush (D07) removed the read-through "
                      "cache that had been hiding it, so at 13:10 every request reached postgres. Fixed at 15:09 "
                      "by CREATE INDEX CONCURRENTLY idx_catalog_items_vendor_ref.",
        "formats": ["PostgreSQL server log", "Java logback (catalog-svc)", "Python uvicorn (inventory-svc)",
                    "kubectl events", "Java logback (checkout-api)"],
        "trap": "postgres logs no service name -- only a pid and app_runtime@shopdb. The statement text is the "
                "only join key back to catalog-svc, and catalog-svc logs it with '?' placeholders while postgres "
                "logs it with '$1'.",
        "confused_with": "D13 (CacheEvictionScheduler WARN spam) -- also cache-related, also loud, but runs at "
                         "the same rate all day.",
    },
    {
        "id": "D05",
        "title": "2867 paid orders never got a confirmation: kafka retention.ms lowered to 10 minutes",
        "difficulty": "cross-format correlation + rare needle (correlation id renamed 5 times)",
        "requires": "cross-format correlation, rare-event needle",
        "description": "ORD-88231 was captured successfully (PSP-4471902, EUR 12999.00) and the payment.captured "
                       "event was produced to orders.events partition 3 at offset 5512034, but no notification "
                       "was ever sent. 2867 orders are affected.",
        "root_cause": "At 13:52:03 a manual kafka-configs.sh push (same ssh session as D06) set "
                      "retention.ms=600000 and segment.ms=300000 on topic orders.events. At 13:57 the broker "
                      "deleted the segment holding offsets 5510000-5514900. notify-svc was simultaneously stuck "
                      "in a consumer-group rebalance (14:02); when it resumed, its committed offset 5512034 was "
                      "below logStartOffset 5514901, so auto.offset.reset=latest silently skipped 2867 records.",
        "formats": ["Kafka broker log", "Node pino JSON", "Go zap JSON", "Java logback", "Envoy JSON",
                    "nginx access", "HAProxy", "Python uvicorn", "in-house promo log", "no-extension ordersync log",
                    "auth.log"],
        "trap": "The SAME logical request id is spelled five different ways: X-Request-ID/request_id "
                "'9f2c1d7e-4b6a-4c31-8e55-1a2b3c4d5e6f' (haproxy {} capture, nginx rid=, envoy request_id), "
                "traceId '9f2c1d7e4b6a4c318e551a2b3c4d5e6f' (Java MDC, dashes stripped), trace_id (Go zap, "
                "dash-less), correlation_id (Python, dashes kept), and RID~9f2c1d7e4b6a (in-house promo, "
                "truncated to 12 chars). notify-svc carries NO correlation id at all -- it can only be joined by "
                "order_id -- and the decisive evidence for ORD-88231 in notify-svc is an ABSENCE of any line.",
        "confused_with": "D09 (notify-svc SMTP error ramp) -- also notify-svc, also 'notifications not arriving', "
                         "but a completely different mechanism and it produces error lines, not silence.",
    },
    {
        "id": "D06",
        "title": "Unauthorized interactive kubectl scale halved inventory-svc capacity mid-incident",
        "difficulty": "rare needle (3 lines in ~6 MB of CRON noise) + cross-format",
        "requires": "rare-event needle, cross-format correlation",
        "description": "At 13:31:02 someone logged in as `deploy` and scaled deploy/inventory-svc from 6 replicas "
                       "to 2 while the service was already saturated, which is what turned a slow-query incident "
                       "into an OOM/CrashLoop outage. The same session pushed the kafka retention change (D05).",
        "root_cause": "Human action, not code: an interactive ssh session (publickey, user deploy, from 10.42.7.9) "
                      "running `kubectl -n prod scale deploy/inventory-svc --replicas=2` and, 21 minutes later, "
                      "`kafka-configs.sh --alter --add-config retention.ms=600000`. No change ticket; "
                      "k8s/deployment-notes.md still says replicas: 6.",
        "formats": ["auth.log", "syslog", "kubectl events", "journald export"],
        "trap": "auth.log is ~90% pam_unix CRON session open/close noise; the three meaningful lines are <0.001% "
                "of the file. The kubectl scale leaves NO application-level trace -- only the sudo line, one "
                "kubelet SyncLoop DELETE record, and a single ScalingReplicaSet event row that is repeated "
                "verbatim in every later kubectl-events snapshot.",
        "confused_with": "Autoscaling. There is no HPA (deployment-notes.md says HPA disabled since 2026-05), so "
                         "'the HPA scaled it down' is wrong.",
    },
    {
        "id": "D07",
        "title": "Unscheduled POST /admin/config/reload flushed the catalog read-through cache",
        "difficulty": "rare needle in ~186 MB of health-check spam, split across a .gz rotation boundary",
        "requires": "rare-event needle, cross-format correlation, gz decompression",
        "description": "Seven POST /admin/config/reload requests from 10.42.9.31 (curl/8.7.1) between 12:58:11 "
                       "and 13:02:55 invalidated 184203 cache entries whose hit ratio was 0.976, so from 13:10 "
                       "every catalog read reached postgres.",
        "root_cause": "The admin endpoint is reachable without authentication from the pod network and performs a "
                       "full cache invalidation as a side effect of a config reload. It converted D04 from a "
                       "latent latency regression into a full outage.",
        "formats": ["nginx access (combined)", "gzipped rotated nginx access", "Java logback (catalog-svc)"],
        "trap": "Four of the seven lines are in nginx/access.log.1.gz and only three in nginx/access.log -- an "
                "investigator who ignores rotated/compressed files finds three lines and mis-times the event. "
                "The surrounding file is ~90% kube-probe/Prometheus health-check spam.",
        "confused_with": "D13 (cache eviction WARN spam) -- both mention cache; the eviction scheduler is routine "
                         "and runs all day.",
    },
    {
        "id": "D08",
        "title": "catalog-svc p99 latency shift at 11:05 with ZERO error lines",
        "difficulty": "statistical / percentile reasoning",
        "requires": "statistical/rate reasoning",
        "description": "After the 4.7.2 rollout at 11:00:41, ~3.1% of catalog-svc requests take 640-1540 ms. "
                       "MEASURED on istio/ingressgateway-access.json.log `duration` where upstream_cluster "
                       "contains catalog-svc (n=11614 before, n=27105 after): p50 10 -> 10 ms, p75 36 -> 38 ms, "
                       "p90 78 -> 88 ms, p99 120 -> 1251 ms, p99.9 125 -> 1512 ms, mean 26.5 -> 58.1 ms. Every "
                       "one of these requests returns HTTP 200 with response_flags '-' (0 non-200 catalog-svc "
                       "lines in the whole file).",
        "root_cause": "Same code change as D04 (un-indexed attrs->>'vendor_ref' lookup), 2 h 05 min before it "
                      "became visible as an outage. This is the earliest detectable signal in the entire corpus.",
        "formats": ["Envoy JSON access log (duration / upstream_service_time)", "Envoy text access log",
                    "Java logback (catalog-svc)", "nginx access (rt=)"],
        "trap": "There is no ERROR, WARN or non-200 line to grep for. The defect exists only as a change in the "
                "distribution of the `duration` field, grouped by upstream_cluster, before vs after 11:05. "
                "Mean latency barely moves (~+35 ms) -- only the tail moves.",
        "confused_with": "D03 -- the 503 storm is far louder and 2.5 h later; treating 13:41 as 'when it started' "
                         "misses the real change window and therefore the causing deploy.",
    },
    {
        "id": "D09",
        "title": "notify-svc SMTP TLS handshake failure rate ramps ~32x (0.34% -> 10.8%) across the window",
        "difficulty": "statistical / rate reasoning",
        "requires": "statistical/rate reasoning",
        "description": "level:50 records with EPROTO 'wrong version number' against smtp-relay.acme-internal.net "
                       "grow smoothly across the whole 7-hour window. MEASURED share of level==50 per UTC hour "
                       "in apps/notify-svc/notify.pino.json: 09:00 0.34%, 10:00 1.07%, 11:00 2.38%, 12:00 3.93%, "
                       "13:00 5.85%, 14:00 7.63%, 15:00 10.84%. queue_depth grows with them. The identical error "
                       "line exists at 09:04 and at 15:5x -- only the RATE distinguishes normal from broken.",
        "root_cause": "The SMTP relay's TLS configuration no longer accepts the legacy protocol version that "
                      "notify-svc's pooled sockets negotiate; as long-lived pooled connections recycle, an "
                      "ever-larger share of sends hits the new configuration. Every failure is retried "
                      "successfully, so nothing user-visible happens until the retry queue backs up (15:30, "
                      "queue_depth 911, oldest_age_s 1841).",
        "formats": ["Node pino JSON (level field, time field in EPOCH MILLIS)"],
        "trap": "Point-in-time sampling is useless: any single window looks 'a bit noisy'. The answer requires "
                "bucketing level==50 vs total per unit time. pino's `time` is epoch milliseconds, not an ISO "
                "string, so naive text-based time bucketing fails.",
        "confused_with": "D05 -- both end with 'notifications did not arrive'. D09 produces loud error lines and "
                         "no data loss; D05 produces silence and 2867 lost notifications.",
    },
    {
        "id": "D10",
        "title": "promo-engine stacks percentage discounts without a ceiling -> negative order totals",
        "difficulty": "unanticipated custom format",
        "requires": "single-format read of an unknown format",
        "description": "11 orders (ORD-88240..ORD-88250) get discount_pct between 137.5 and 141.5 and a NEGATIVE "
                       "final_minor (-486212 and below); the ledger refuses the negative charge and the orders "
                       "are left in PENDING_MANUAL.",
        "root_cause": "The SUMMER26_STACK rule composes SUMMER26 + LOYALTY10 + WELCOME15 by adding percentages "
                      "with no 100% ceiling and no post-condition that final_minor >= 0.",
        "formats": ["in-house pipe-delimited .plog", "CRI-wrapped copy of the same format in k8s/pods/"],
        "trap": "No public parser knows this layout: fields are pipe-delimited, the timestamp is "
                "'20260728|173302.144|+0300' (three pipe-separated fields, non-ISO, local +0300 AND on a host "
                "whose clock is 47 s fast), and the severity vocabulary is TRACE/CHATTER/NOTE/WARN/ALARM/FATALITY "
                "-- so 'ERROR|FATAL|WARN' greps return nothing and 'WARN' is the third-lowest severity here. The "
                "actual failures are ALARM and FATALITY.",
        "confused_with": "Nothing else in the corpus produces negative amounts; the risk here is a FALSE NEGATIVE "
                         "(the file is skipped as unparseable), not a false positive.",
    },
    {
        "id": "D11",
        "title": "billing-adapter silently converts EUR at a 7-day-old FX rate (Russian-language log)",
        "difficulty": "non-English log + cross-format",
        "requires": "single-format read (Russian), cross-format correlation",
        "description": "Currency conversion for EUR orders uses the cached ЦБ РФ rate from 21.07.2026 (91.4412) "
                       "instead of the current one (96.8871) -- a 5.62% under-charge; ORD-88231 alone is 70812 "
                       "kopecks short.",
        "root_cause": "The NetworkPolicy 'egress-default-deny' applied 2026-07-21 blocks egress to www.cbr.ru; "
                      "the adapter's fetch times out after 5000 ms and falls back to cache. The fallback is "
                      "logged at ПРЕДУПРЕЖДЕНИЕ, and it only escalates to ОШИБКА when cache age exceeds 7 days -- "
                      "so the failure was invisible for a week.",
        "formats": ["Russian in-house log (non-ISO dd.mm.yyyy, comma decimals)", "journald export (calico-node "
                    "policy line)"],
        "trap": "Severity words are Russian (ОШИБКА / ПРЕДУПРЕЖДЕНИЕ / ИНФО / ОТЛАДКА), so ERROR/WARN greps and "
                "English-keyword heuristics find nothing. The date format is dd.mm.yyyy with a comma "
                "millisecond separator and the timestamps are +0300.",
        "confused_with": "D10 -- both are money-wrong-amount defects in in-house services; different services, "
                         "different mechanisms.",
    },
    {
        "id": "D12",
        "title": "RED HERRING: SYN flood + nf_conntrack table full on node-b",
        "difficulty": "red herring",
        "requires": "cross-format correlation to REFUTE",
        "description": "A loud kernel-level network alarm that looks like the obvious cause of a 503 storm.",
        "root_cause": "NOT A CAUSE of anything in this corpus. It happens at 08:12:04 UTC -- 5 h 29 min BEFORE "
                      "the 503 storm -- and on node-b, which runs promo-engine only; every affected pod "
                      "(inventory-svc, checkout-api, catalog-svc) is on node-a. Additionally node-b's clock is "
                      "47 s fast and its syslog lines are therefore stamped 08:12:51, which makes naive "
                      "time-alignment worse, not better.",
        "formats": ["syslog (node-b) with embedded kernel lines"],
        "trap": "It is the single most alarming-sounding text in the corpus and it is in a kernel log, which "
                "investigators tend to trust. Refuting it requires (a) reading the timestamp against the storm "
                "window and (b) knowing which node the affected pods run on (k8s events / journald / CRI pod "
                "log paths).",
        "confused_with": "D03 -- this is exactly what it is designed to be mistaken for.",
    },
    {
        "id": "D13",
        "title": "RED HERRING: catalog-svc cache-eviction WARN spam and a TLS-expiry warning",
        "difficulty": "red herring",
        "requires": "statistical/rate reasoning to REFUTE",
        "description": "The ANSI-coloured console capture is full of 'eviction pass took 1523ms (threshold "
                       "500ms)' WARNs plus a 'TLS certificate expires in 14 days' warning.",
        "root_cause": "NOT A CAUSE. The eviction WARN fires at a constant ~25% of lines from 09:00 to 16:00 -- "
                      "identical rate before and during the incident -- and the certificate has 14 days of "
                      "runway (notAfter 2026-08-11). Both are chronic background noise.",
        "formats": ["ANSI-coloured console log"],
        "trap": "The file is full of ANSI SGR escapes (\\x1b[33m etc.), so level extraction by regex on 'WARN' "
                "either fails or double-counts, and the rate argument that refutes it cannot be computed until "
                "the escapes are stripped.",
        "confused_with": "D04 and D07 (both genuinely cache-related) -- this is the decoy for both.",
    },
]

DIRTINESS = [
    "Clock skew: node-b runs 47 s ahead of node-a (chronyd cannot step it); every node-b line "
    "(syslog/node-b/syslog, inhouse/promo-engine.plog, k8s/pods/promo-engine-*) is shifted.",
    "Mixed timezones: haproxy/haproxy.log and both in-house logs are stamped Europe/Moscow (+0300); "
    "everything else is UTC; nginx uses +0000 CLF; the answer key is in UTC.",
    "Epoch timestamps in three different units: pino `time` = epoch MILLIS, zap `ts` = epoch SECONDS as a "
    "float, journald __REALTIME_TIMESTAMP = epoch MICROS, dmesg = seconds since boot (boot 2026-07-25T04:11:03Z).",
    "Truncated final lines: nginx/access.log ends mid-request-path; "
    "istio/ingressgateway-access.json.log ends with an unterminated JSON object.",
    "Partially-written file: misc/inventory-svc-partial.log stops mid-timestamp ('2026-07-28 13:40:12,88') "
    "because the process was OOM-killed while writing.",
    "Interleaved multi-line traces: in apps/checkout-api/checkout-api.log the D01 NPE trace and a concurrent "
    "SocketTimeoutException trace from another thread are interleaved line by line.",
    "Service name only in the filename: misc/ordersync-prod-2026-07-28 (no extension) never names 'ordersync' "
    "in any line.",
    "ANSI colour escapes: apps/catalog-svc/catalog-console-ansi.log uses SGR codes around level and logger.",
    "Low signal-to-noise: nginx/access.log + access.log.1.gz are ~90% kube-probe/Prometheus health checks; "
    "syslog/node-a/auth.log is ~90% pam_unix CRON open/close.",
    "Inconsistent correlation-id propagation: X-Request-ID (haproxy capture block, nginx rid=), request_id "
    "(envoy), traceId dash-less (Java MDC), trace_id dash-less (Go zap), correlation_id with dashes (Python), "
    "RID~ truncated to 12 hex chars (in-house promo). notify-svc (pino) propagates NO id at all - join by "
    "order_id only. Kafka has none - join by topic/partition/offset.",
    "Compressed rotation: nginx/access.log.1.gz must be decompressed; a 7-line needle is split 4/3 across the "
    "rotation boundary.",
    "No-extension and unusual-extension files: misc/ordersync-prod-2026-07-28, inhouse/promo-engine.plog, "
    "syslog/node-a/dmesg, syslog/node-a/auth.log.",
    "Non-log files inside the tree (ingest-discipline trap): README.md, k8s/deployment-notes.md, "
    "nginx/nginx.conf - a naive ingester will happily turn them into 'log records'.",
    "Multi-line records that are not stack traces: journald export records are ~18 physical lines each with the "
    "payload in MESSAGE=; postgres DETAIL/CONTEXT/STATEMENT continuation lines start with a TAB.",
    "Docker json-file escaping: every physical line is one JSON object and the real newline lives inside the "
    "\"log\" string, so a 12-frame Go panic is 12 separate JSON records.",
    "CRI partial lines: k8s/pods/inventory-svc-*.log splits long lines into consecutive 'P' records terminated "
    "by an 'F' record.",
    "Bespoke severity vocabularies: TRACE/CHATTER/NOTE/WARN/ALARM/FATALITY (promo-engine) and "
    "ОТЛАДКА/ИНФО/ПРЕДУПРЕЖДЕНИЕ/ОШИБКА (billing-adapter). Grepping ERROR/FATAL misses both.",
    "Repeated snapshots: k8s/events-prod-eu-1.txt is 85 consecutive `kubectl get events` captures, so every "
    "event row appears many times with a different relative LAST SEEN age - naive counting inflates everything.",
]

stats = {}


def write_key():
    os.makedirs(KEYDIR, exist_ok=True)
    by_defect = {}
    for p in PROOFS:
        by_defect.setdefault(p["defect"], []).append(p)
    files = []
    total_bytes = 0
    total_lines = 0
    for rel in sorted(stats):
        lf = stats[rel]
        full = os.path.join(ROOT, rel)
        on_disk = os.path.getsize(full)
        files.append({"path": rel, "lines": lf.lineno, "uncompressed_bytes": lf.nbytes,
                      "on_disk_bytes": on_disk})
        total_bytes += on_disk
        total_lines += lf.lineno
    out = {
        "generator": os.path.abspath(__file__),
        "seed": SEED,
        "scale": SCALE,
        "corpus_root": ROOT,
        "scenario": "ACME Shop, Kubernetes cluster prod-eu-1, incident window 2026-07-28 09:00-16:00 UTC. "
                    "A catalog-svc release at 11:00 introduces an un-indexed JSONB lookup; an unauthenticated "
                    "cache flush at 12:59 removes the cache that was hiding it; postgres lock contention at 13:10 "
                    "drains the inventory-svc connection pool; an unauthorized manual scale-down at 13:31 halves "
                    "capacity; pods OOMKill at 13:40 and the mesh returns 503 until ~14:22. A separate manual "
                    "kafka retention change at 13:52 silently drops 2867 order notifications. Three further "
                    "defects (Go panic, Java NPE, discount stacking, stale FX rate) and two red herrings run "
                    "concurrently.",
        "totals": {"files": len(files), "on_disk_bytes": total_bytes, "lines": total_lines},
        "line_numbering": "Proof line numbers are 1-based PHYSICAL line numbers in the file as written. For "
                          "nginx/access.log.1.gz they are line numbers in the DECOMPRESSED stream. Files with a "
                          "truncated tail (nginx/access.log, istio/ingressgateway-access.json.log, "
                          "misc/inventory-svc-partial.log) end without a trailing newline, so `wc -l` reports "
                          "one fewer line than the `lines` field below.",
        "files": files,
        "defects": [],
        "dirtiness": DIRTINESS,
        "correlation_id_map": {
            "logical_request": "ORD-88231 checkout",
            "spellings": {
                "haproxy/haproxy.log": "{%s} capture block" % RID_881,
                "nginx/access.log": "rid=%s" % RID_881,
                "istio/ingressgateway-access.json.log": '"request_id":"%s"' % RID_881,
                "apps/checkout-api/checkout-api.log": "traceId=%s" % RID_881_J,
                "apps/payments-worker/payments.zap.json": '"trace_id":"%s"' % RID_881_J,
                "apps/inventory-svc/uvicorn.log": "correlation_id=%s" % RID_881,
                "inhouse/promo-engine.plog": "RID~%s" % RID_881_S,
                "apps/notify-svc/notify.pino.json": "NONE - no correlation id is propagated; join on order_id",
                "kafka/server.log": "NONE - join on topic/partition/offset (orders.events-3 @ 5512034)",
                "misc/ordersync-prod-2026-07-28": "no id, order id only (%s)" % ORD_881,
            },
        },
    }
    for d in DEFECTS:
        e = dict(d)
        e["proof_locations"] = [{"file": p["file"], "line_start": p["line_start"], "line_end": p["line_end"],
                                 "note": p["note"]} for p in by_defect.get(d["id"], [])]
        e["proof_count"] = len(e["proof_locations"])
        out["defects"].append(e)
    with open(os.path.join(KEYDIR, "answer-key.json"), "w", encoding="utf-8") as f:
        json.dump(out, f, ensure_ascii=False, indent=2)
    return out


def main():
    t0 = time.time()
    if os.path.isdir(ROOT):
        shutil.rmtree(ROOT)
    os.makedirs(ROOT, exist_ok=True)
    steps = [
        ("nginx", gen_nginx), ("envoy/istio", gen_envoy), ("haproxy", gen_haproxy),
        ("java/checkout-api", gen_checkout_java), ("java/catalog-svc", gen_catalog_java),
        ("go/zap", gen_zap), ("docker json-file", gen_docker), ("python/uvicorn", gen_uvicorn),
        ("node/pino", gen_pino), ("postgres", gen_postgres), ("kafka", gen_kafka),
        ("journald", gen_journald), ("syslog/dmesg/auth", gen_syslog), ("k8s events", gen_k8s_events),
        ("k8s pod logs", gen_pod_logs), ("in-house promo", gen_promo), ("russian billing", gen_russian),
        ("ordersync (no ext)", gen_ordersync), ("non-log files", gen_non_logs),
    ]
    for name, fn in steps:
        s = time.time()
        fn()
        sys.stderr.write("  %-22s %6.1fs\n" % (name, time.time() - s))
        sys.stderr.flush()
    key = write_key()
    sys.stderr.write("total %.1fs  files=%d  lines=%d  bytes=%d\n"
                     % (time.time() - t0, key["totals"]["files"], key["totals"]["lines"],
                        key["totals"]["on_disk_bytes"]))


if __name__ == "__main__":
    main()
