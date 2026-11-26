#!/usr/bin/env python3
"""Local mock of the Banxico SIE, INEGI BIE and FRED endpoints, for offline pipeline tests.

The values are SYNTHETIC (smooth made-up paths in plausible ranges). They exist only to exercise
parsing, transformations, fail-safe logic and the front end. Never commit data produced from them.

Usage:  python tests/mock_server.py 8765 [--break KEY ...]
  --break fix        -> Banxico FIX returns HTTP 500 (tests retries + keep-previous)
  --break inpc       -> INEGI INPC returns an empty observation list (tests "suspicious")
  --break DGS10      -> FRED DGS10 returns a truncated file (fewer rows)
  --break SF61745-title -> Banxico target metadata returns a wrong title (tests ID guard)
"""
import json
import math
import sys
from datetime import date, timedelta
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from urllib.parse import parse_qs, urlparse

START = date(2014, 1, 1)
END = date.today() - timedelta(days=1)
BROKEN = set(sys.argv[sys.argv.index("--break") + 1:]) if "--break" in sys.argv else set()


def days(business=False):
    d = START
    while d <= END:
        if not business or d.weekday() < 5:
            yield d
        d += timedelta(days=1)


def months(lag_months=0):
    last = END.year * 12 + END.month - 1 - lag_months
    k = START.year * 12 + START.month - 1
    while k <= last:
        yield date(k // 12, k % 12 + 1, 1)
        k += 1


def t(d):  # years since start
    return (d - START).days / 365.25


def step(v, s=0.25):
    return round(v / s) * s


BANXICO = {
    "SF43718": ("Tipo de cambio Pesos por dólar E.U.A. Tipo de cambio para solventar obligaciones denominadas en moneda extranjera Fecha de determinación (FIX)",
                lambda: [(d, 17.5 + 2.2 * math.sin(t(d) * 1.3) + 0.15 * math.sin(t(d) * 40)) for d in days(True)]),
    "SF61745": ("Tasa objetivo",
                lambda: [(d, step(6.5 + 3.5 * math.sin(t(d) * 0.9 - 1))) for d in days()]),
    "SF44071": ("Valores gubernamentales Resultados de la subasta semanal Bonos a tasa fija a 10 años Tasa de rendimiento",
                lambda: [(d, round(8 + 1.5 * math.sin(t(d) * 0.8), 2)) for i, d in enumerate(days(True)) if d.weekday() == 1 and i % 4 == 0]),
    "SR1504": ("Índice de tipo de cambio real bilateral con EUA",
               lambda: [(d, 95 + 10 * math.sin(t(d) * 1.1)) for d in months(1)]),
}


def inegi_series(iid):
    if iid == "736181":  # quarterly GDP SA
        out, y, q = [], 2014, 1
        while date(y, 3 * q - 2, 1) <= END - timedelta(days=60):
            d = date(y, 3 * q - 2, 1)
            v = 2.2e7 * (1.006 ** (4 * t(d))) * (0.82 if d == date(2020, 4, 1) else 1)
            out.append((f"{y}/{q:02d}", v))
            q += 1
            if q == 5:
                y, q = y + 1, 1
        return out
    base = {"737219": lambda d: 100 * 1.005 ** (12 * t(d)) / 1.06 ** 5,
            "910392": lambda d: 85 * 1.0035 ** (12 * t(d)),
            "910393": lambda d: 84 * 1.0033 ** (12 * t(d)),
            "444884": lambda d: 3.4 + 0.6 * math.sin(t(d))}[iid]
    out = []
    for d in months(1):
        v = base(d)
        val = "N/E" if (iid == "444884" and date(2020, 4, 1) <= d <= date(2020, 6, 1)) else f"{v:.6f}"
        out.append((f"{d.year}/{d.month:02d}", val))
    return out


def fred_series(sid):
    if sid == "DFF":
        return [(d, round(step(2.5 + 2 * math.sin(t(d) * 0.9)) - 0.17, 2)) for d in days()]
    if sid == "DFEDTARL":
        return [(d, step(2.5 + 2 * math.sin(t(d) * 0.9)) - 0.25) for d in days()]
    if sid == "DFEDTARU":
        return [(d, step(2.5 + 2 * math.sin(t(d) * 0.9))) for d in days()]
    if sid == "CPIAUCSL":
        return [(d, round(234 * 1.0022 ** (12 * t(d)), 3)) for d in months(1)]
    if sid == "DGS10":
        rows = [(d, "." if d.month == 12 and d.day == 25 else round(2.5 + 1.5 * math.sin(t(d) * 0.7), 2)) for d in days(True)]
        return rows[:100] if "DGS10" in BROKEN else rows
    raise KeyError(sid)


class H(BaseHTTPRequestHandler):
    def log_message(self, *a):
        pass

    def send(self, code, body, ctype="application/json"):
        b = body.encode()
        self.send_response(code)
        self.send_header("Content-Type", ctype)
        self.send_header("Content-Length", str(len(b)))
        self.end_headers()
        self.wfile.write(b)

    def do_GET(self):
        u = urlparse(self.path)
        p = [x for x in u.path.split("/") if x]
        try:
            if p[:1] == ["banxico"]:
                if self.headers.get("Bmx-Token") != "test-banxico-token":
                    return self.send(401, '{"error":"token"}')
                sid = p[1]
                title, gen = BANXICO[sid]
                if len(p) == 2:
                    if f"{sid}-title" in BROKEN:
                        title = "Some other series"
                    return self.send(200, json.dumps({"bmx": {"series": [{"idSerie": sid, "titulo": title}]}}))
                if sid == "SF43718" and "fix" in BROKEN:
                    return self.send(500, "boom")
                datos = [{"fecha": d.strftime("%d/%m/%Y"), "dato": f"{v:,.4f}"} for d, v in gen()]
                if sid == "SF61745":
                    datos.append({"fecha": END.strftime("%d/%m/%Y"), "dato": "N/E"})
                return self.send(200, json.dumps({"bmx": {"series": [{"idSerie": sid, "titulo": title, "datos": datos}]}}))
            if p[:1] == ["inegi"]:
                iid, token = p[1], p[7]
                if token != "test-inegi-token":
                    return self.send(400, "bad token")
                obs = [] if (iid == "910392" and "inpc" in BROKEN) else [
                    {"TIME_PERIOD": tp, "OBS_VALUE": str(v)} for tp, v in reversed(inegi_series(iid))]
                return self.send(200, json.dumps({"Series": [{"INDICADOR": iid, "OBSERVATIONS": obs}]}))
            if p[:1] == ["fred"]:
                sid = parse_qs(u.query)["id"][0]
                lines = [f"observation_date,{sid}"] + [f"{d.isoformat()},{v}" for d, v in fred_series(sid)]
                return self.send(200, "\n".join(lines) + "\n", "text/csv")
        except (KeyError, IndexError) as e:
            return self.send(404, f"not found {e}")
        self.send(404, "not found")


if __name__ == "__main__":
    port = int(sys.argv[1]) if len(sys.argv) > 1 and sys.argv[1].isdigit() else 8765
    ThreadingHTTPServer(("127.0.0.1", port), H).serve_forever()
