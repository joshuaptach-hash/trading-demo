"""Demo-Trading-Bot: handelt Krypto, Aktien und ETFs mit Spielgeld nach festen Regeln.
Kein Broker, kein echtes Geld.

Läuft per GitHub Actions etwa alle 5 Minuten:
  python bot/bot.py state.json
Holt Kurse (Krypto: Coinbase, rund um die Uhr; Aktien und ETFs: Xetra über Yahoo,
nur zu Börsenzeiten), prüft Stop-Loss, Take-Profit, Liquidation und Haltedauer
anhand der 1-Minuten-Kerzen seit dem letzten Lauf, eröffnet höchstens eine neue
Position und schreibt den Stand zurück.
"""
import json
import sys
import time
import urllib.request
from pathlib import Path

WERTE = {
    # Krypto (Coinbase, EUR, rund um die Uhr)
    "BTC": ("Bitcoin", "krypto"), "ETH": ("Ethereum", "krypto"), "SOL": ("Solana", "krypto"),
    "XRP": ("XRP", "krypto"), "DOGE": ("Dogecoin", "krypto"),
    # Aktien (Xetra, EUR)
    "SAP.DE": ("SAP", "aktie"), "SIE.DE": ("Siemens", "aktie"), "ALV.DE": ("Allianz", "aktie"),
    "RHM.DE": ("Rheinmetall", "aktie"), "APC.DE": ("Apple", "aktie"), "MSF.DE": ("Microsoft", "aktie"),
    "NVD.DE": ("Nvidia", "aktie"), "TL0.DE": ("Tesla", "aktie"),
    # ETFs (Xetra, EUR)
    "EUNL.DE": ("MSCI World", "etf"), "SXR8.DE": ("S&P 500", "etf"), "MWOP.DE": ("MSCI World ESG", "etf"),
    "XAIX.DE": ("AI & Big Data", "etf"), "EXS1.DE": ("DAX", "etf"),
}
# trend = Tagesänderung (Krypto: 24 Std, Börse: seit Vortagesschluss), dip = Gegenbewegung der letzten Stunde
KLASSEN = {
    "krypto": {"name": "Krypto", "hebel": 10, "trend": 1.0, "dip": 0.4, "stop_loss": 0.6, "take_profit": 1.0, "haltedauer_min": 120},
    "aktie": {"name": "Aktien", "hebel": 5, "trend": 0.8, "dip": 0.3, "stop_loss": 0.5, "take_profit": 1.0, "haltedauer_min": 180},
    "etf": {"name": "ETFs", "hebel": 5, "trend": 0.4, "dip": 0.15, "stop_loss": 0.3, "take_profit": 0.6, "haltedauer_min": 180},
}
MEINE = set()  # Symbole aus meine_werte.json
REGELN = {"startkapital": 1000.0, "einsatz": 150.0, "max_positionen": 4, "pause_nach_schliessen_min": 30,
          "gebuehr": 1.0, "schluss_vor_boersenende_min": 10, "klassen": KLASSEN}


def holen(url):
    req = urllib.request.Request(url, headers={"User-Agent": "Mozilla/5.0 (demo-bot)"})
    with urllib.request.urlopen(req, timeout=20) as r:
        return json.loads(r.read())


def meine_werte_laden(pfad):
    """Gespeicherte Werte (Suche auf der Seite) zusätzlich handeln."""
    try:
        liste = json.loads(Path(pfad).read_text())
    except Exception:
        return []
    neu = []
    for e in liste[:40]:
        s, typ = e.get("symbol", ""), e.get("typ", "")
        if typ in KLASSEN and s:
            if s not in WERTE:
                WERTE[s] = (e.get("name") or s, typ)
            MEINE.add(s)
            neu.append(s)
    return neu


def eurusd():
    return float(holen("https://query1.finance.yahoo.com/v8/finance/chart/EURUSD=X?interval=1d&range=5d")["chart"]["result"][0]["meta"]["regularMarketPrice"])


def krypto(s, jetzt):
    api = f"https://api.exchange.coinbase.com/products/{s}-EUR"
    k = sorted(holen(f"{api}/candles?granularity=60"), key=lambda x: x[0])  # [t, low, high, open, close, vol]
    preis = float(k[-1][4])
    ref = float(holen(f"{api}/stats")["open"])
    return {"preis": preis, "ref": ref, "offen": True, "kerzen": [[int(x[0]), float(x[1]), float(x[2]), float(x[4])] for x in k]}


FX = {}


def boerse(s, jetzt):
    d = holen(f"https://query1.finance.yahoo.com/v8/finance/chart/{s}?interval=1m&range=1d")["chart"]["result"][0]
    m, q = d["meta"], d["indicators"]["quote"][0]
    w = m.get("currency", "EUR")
    if w == "EUR":
        f = 1.0
    elif w == "USD":
        if "USD" not in FX:
            FX["USD"] = eurusd()
        f = 1 / FX["USD"]
    else:
        raise ValueError(f"Währung {w} wird nicht unterstützt")
    kerzen = [[int(t), float(lo) * f, float(hi) * f, float(c) * f] for t, lo, hi, c in zip(d.get("timestamp", []), q["low"], q["high"], q["close"])
              if c is not None and lo is not None and hi is not None]
    m["regularMarketPrice"] = float(m["regularMarketPrice"]) * f
    m["chartPreviousClose"] = float(m["chartPreviousClose"]) * f
    per = m["currentTradingPeriod"]["regular"]
    jetzt_s = jetzt // 1000
    return {"preis": float(m["regularMarketPrice"]), "ref": float(m["chartPreviousClose"]), "kerzen": kerzen,
            "offen": per["start"] <= jetzt_s < per["end"], "schluss": per["end"], "stand": int(m.get("regularMarketTime", jetzt_s))}


def marktdaten(jetzt):
    m, fehler = {}, []
    for s, (name, klasse) in WERTE.items():
        try:
            d = krypto(s, jetzt) if klasse == "krypto" else boerse(s, jetzt)
        except Exception as e:
            fehler.append(f"{s}: {e}")
            continue
        k = d["kerzen"]
        vor60 = [x for x in k if x[0] <= k[-1][0] - 3600] if k else []
        d["tag"] = (d["preis"] / d["ref"] - 1) * 100
        d["h1"] = (d["preis"] / vor60[-1][3] - 1) * 100 if vor60 else 0.0
        d["klasse"] = klasse
        m[s] = d
    return m, fehler


def eur(v):
    return f"{v:,.2f} €".replace(",", "X").replace(".", ",").replace("X", ".")


def kurs(v):
    return (f"{v:,.2f}" if v >= 1 else f"{v:.5f}").replace(",", "X").replace(".", ",").replace("X", ".") + " €"


def pct(v):
    return f"{v:+.1f} %".replace(".", ",")


def wert(p, preis):
    d = p["menge"] * preis - p["einsatz"] * p["hebel"]
    return max(0.0, p["einsatz"] + (-d if p["short"] else d))


def liq_preis(p):
    exp = p["einsatz"] * p["hebel"]
    return ((exp + p["einsatz"]) if p["short"] else (exp - p["einsatz"])) / p["menge"]


def name(s):
    return WERTE[s][0] if s in WERTE else s


def schliessen(st, p, preis, grund, ts, betrag=None):
    w = wert(p, preis) if betrag is None else betrag
    geb = 0.0 if betrag == 0 else REGELN["gebuehr"]
    st["bargeld"] += w - geb
    st["positionen"].remove(p)
    ergebnis = w - p["einsatz"] - geb - REGELN["gebuehr"]
    st["trades"].append({"ts": ts, "s": p["s"], "aktion": "Geschlossen", "short": p["short"], "hebel": p["hebel"], "kurs": preis,
                         "einsatz": p["einsatz"], "betrag": round(w, 2), "ergebnis": round(ergebnis, 2), "gebuehr": geb, "grund": grund})
    st["zuletzt_geschlossen"][p["s"]] = ts
    return ergebnis


def pruefen(st, m, jetzt):
    """Stop-Loss, Take-Profit, Liquidation, Haltedauer und Börsenschluss, Kerze für Kerze seit dem letzten Lauf."""
    meld = []
    geprueft = st.setdefault("geprueft_bis", {})
    for p in list(st["positionen"]):
        d = m.get(p["s"])
        if not d:
            continue
        ab = max(geprueft.get(p["s"], 0), p["ts"] // 1000)
        fertig = False
        for t, low, high, close in d["kerzen"]:
            if t < ab:
                continue
            schlecht, gut = (high, low) if p["short"] else (low, high)
            ueber = (lambda a, b: a >= b) if p["short"] else (lambda a, b: a <= b)
            sl_naeher = p["sl"] < p["liq"] if p["short"] else p["sl"] > p["liq"]
            if sl_naeher and ueber(schlecht, p["sl"]):
                e = schliessen(st, p, p["sl"], f"Stop-Loss bei {kurs(p['sl'])}", t * 1000)
                meld.append(f"Stop-Loss bei {name(p['s'])}: {eur(e)} inklusive Gebühren.")
                fertig = True; break
            if ueber(schlecht, p["liq"]):
                schliessen(st, p, p["liq"], "liquidiert, Einsatz verloren", t * 1000, betrag=0.0)
                meld.append(f"{name(p['s'])} wurde liquidiert, der Einsatz von {eur(p['einsatz'])} ist weg.")
                fertig = True; break
            if (gut <= p["tp"]) if p["short"] else (gut >= p["tp"]):
                e = schliessen(st, p, p["tp"], f"Take-Profit bei {kurs(p['tp'])}", t * 1000)
                meld.append(f"Take-Profit bei {name(p['s'])}: +{eur(e)} inklusive Gebühren.")
                fertig = True; break
        if fertig:
            continue
        k = KLASSEN[d["klasse"]]
        if jetzt - p["ts"] > k["haltedauer_min"] * 60000:
            e = schliessen(st, p, d["preis"], "Haltedauer abgelaufen", jetzt)
            meld.append(f"{name(p['s'])} nach {k['haltedauer_min']} Minuten geschlossen: {eur(e)}.")
        elif d["klasse"] != "krypto" and (not d["offen"] or jetzt // 1000 > d["schluss"] - REGELN["schluss_vor_boersenende_min"] * 60):
            e = schliessen(st, p, d["preis"], "vor Börsenschluss geschlossen", jetzt)
            meld.append(f"{name(p['s'])} vor Börsenschluss geschlossen: {eur(e)}.")
    for s, d in m.items():
        if d["kerzen"]:
            geprueft[s] = d["kerzen"][-1][0] + 60
    return meld


def eroeffnen(st, m, jetzt):
    R = REGELN
    offen = {p["s"] for p in st["positionen"]}
    if len(offen) >= R["max_positionen"]:
        return f"{R['max_positionen']} Positionen sind offen, ich warte auf Stop-Loss, Take-Profit oder Ablauf."
    if st["bargeld"] < R["einsatz"] + R["gebuehr"]:
        return "Zu wenig Spielgeld für eine neue Position."
    kandidaten = []
    for s, d in m.items():
        k = KLASSEN[d["klasse"]]
        if s in offen or jetzt - st["zuletzt_geschlossen"].get(s, 0) < R["pause_nach_schliessen_min"] * 60000:
            continue
        if d["klasse"] != "krypto" and (not d["offen"] or jetzt // 1000 > d["schluss"] - 30 * 60):
            continue
        # Stärke relativ zur Schwelle der Klasse, damit Krypto Aktien nicht immer verdrängt
        if d["tag"] > k["trend"] and d["h1"] < -k["dip"]:
            kandidaten.append((abs(d["h1"]) / k["dip"] + d["tag"] / k["trend"] / 4, s, False))
        elif d["tag"] < -k["trend"] and d["h1"] > k["dip"]:
            kandidaten.append((d["h1"] / k["dip"] + abs(d["tag"]) / k["trend"] / 4, s, True))
    if not kandidaten:
        offene_boerse = any(d["offen"] for d in m.values() if d["klasse"] != "krypto")
        return ("Kein Wert erfüllt gerade die Regel. Ich warte auf einen Rücksetzer im Trend."
                + ("" if offene_boerse else " Die Börse ist geschlossen, Aktien und ETFs handle ich erst wieder ab 9 Uhr."))
    _, s, short = max(kandidaten)
    d = m[s]
    k = KLASSEN[d["klasse"]]
    preis, h = d["preis"], k["hebel"]
    p = {"s": s, "name": name(s), "klasse": d["klasse"], "meine": s in MEINE, "short": short, "hebel": h, "einsatz": R["einsatz"], "kurs": preis, "menge": R["einsatz"] * h / preis, "ts": jetzt,
         "sl": preis * (1 + k["stop_loss"] / 100 if short else 1 - k["stop_loss"] / 100),
         "tp": preis * (1 - k["take_profit"] / 100 if short else 1 + k["take_profit"] / 100)}
    p["liq"] = liq_preis(p)
    st["bargeld"] -= R["einsatz"] + R["gebuehr"]
    st["positionen"].append(p)
    zeitraum = "24 Std" if d["klasse"] == "krypto" else "heute"
    grund = f"{zeitraum} {pct(d['tag'])}, letzte Stunde {pct(d['h1'])}"
    st["trades"].append({"ts": jetzt, "s": s, "aktion": "Eröffnet", "short": short, "hebel": h, "kurs": preis, "einsatz": R["einsatz"],
                         "betrag": R["einsatz"], "gebuehr": R["gebuehr"], "grund": grund})
    richtung = "Short (Wette auf fallende Kurse)" if short else "Long (Wette auf steigende Kurse)"
    return (f"{richtung} {name(s)} {h}x eröffnet, {eur(R['einsatz'])} Einsatz zu {kurs(preis)}. Grund: {grund}. "
            f"Stop-Loss {kurs(p['sl'])}, Take-Profit {kurs(p['tp'])}.")


def main():
    pfad = Path(sys.argv[1] if len(sys.argv) > 1 else "state.json")
    jetzt = int(time.time() * 1000)
    st = json.loads(pfad.read_text()) if pfad.exists() and pfad.stat().st_size > 2 else None
    if not st:
        st = {"start": REGELN["startkapital"], "bargeld": REGELN["startkapital"], "positionen": [], "trades": [], "verlauf": [],
              "zuletzt_geschlossen": {}, "beginn": jetzt, "laeufe": 0}
    if not isinstance(st.get("geprueft_bis"), dict):
        st["geprueft_bis"] = {}
    meine_werte_laden(sys.argv[2] if len(sys.argv) > 2 else "meine_werte.json")
    for p in st["positionen"]:  # offene Positionen weiter verfolgen, auch wenn der Wert entfernt wurde
        if p["s"] not in WERTE:
            WERTE[p["s"]] = (p.get("name", p["s"]), p.get("klasse", "aktie"))
    m, fehler = marktdaten(jetzt)
    meld = pruefen(st, m, jetzt)
    info = eroeffnen(st, m, jetzt)
    gesamt = st["bargeld"] + sum(wert(p, m[p["s"]]["preis"]) if p["s"] in m else p["einsatz"] for p in st["positionen"])
    st["verlauf"].append([jetzt, round(gesamt, 2)])
    st["verlauf"] = st["verlauf"][-3000:]
    st["trades"] = st["trades"][-300:]
    st["laeufe"] = st.get("laeufe", 0) + 1
    st["aktualisiert"] = jetzt
    st["notiz"] = " ".join(meld + [info])
    st["regeln"] = REGELN
    st["werte"] = {s: {"name": n, "klasse": k, "meine": s in MEINE} for s, (n, k) in WERTE.items()}
    st["eurusd"] = FX.get("USD")
    alt = st.get("markt", {})
    st["markt"] = {**alt, **{s: {"preis": d["preis"], "tag": round(d["tag"], 3), "h1": round(d["h1"], 3), "offen": d["offen"],
                                 "stand": d.get("stand", jetzt // 1000), "kurve": [round(x[3], 6) for x in d["kerzen"] if x[0] >= d["kerzen"][-1][0] - 3600][-60:]}
                             for s, d in m.items() if d["klasse"] != "krypto"}}
    for s, d in m.items():
        if d["klasse"] == "krypto":
            st["markt"][s] = {"preis": d["preis"], "tag": round(d["tag"], 3), "h1": round(d["h1"], 3), "offen": True}
    st["fehler"] = fehler
    pfad.write_text(json.dumps(st, ensure_ascii=False, separators=(",", ":")))
    print(time.strftime("%Y-%m-%d %H:%M:%S"), f"Gesamt {gesamt:.2f} €, Bargeld {st['bargeld']:.2f} €, offen {len(st['positionen'])}")
    print(st["notiz"])
    for f in fehler:
        print("FEHLER", f)


if __name__ == "__main__":
    main()
