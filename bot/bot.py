"""Krypto-Demo-Bot: handelt mit Spielgeld nach festen Regeln. Kein Broker, kein echtes Geld.

Läuft per GitHub Actions etwa alle 5 Minuten:
  python bot/bot.py state.json
Liest den Stand, holt Kurse von Coinbase (EUR), prüft Stop-Loss, Take-Profit,
Liquidation und Haltedauer anhand der 1-Minuten-Kerzen seit dem letzten Lauf,
eröffnet höchstens eine neue Position und schreibt den Stand zurück.
"""
import json
import sys
import time
import urllib.request
from pathlib import Path

COINS = {"BTC": "Bitcoin", "ETH": "Ethereum", "SOL": "Solana", "XRP": "XRP", "DOGE": "Dogecoin"}
REGELN = {
    "startkapital": 1000.0,
    "einsatz": 150.0,          # € pro Position
    "hebel": 10,               # 1 % Kurs = 10 % auf den Einsatz
    "stop_loss": 0.6,          # % vom Einstiegskurs (= -6 % auf den Einsatz)
    "take_profit": 1.0,        # % vom Einstiegskurs (= +10 % auf den Einsatz)
    "max_positionen": 3,
    "max_haltedauer_min": 120,
    "pause_nach_schliessen_min": 30,
    "trend_24h": 1.0,          # Long nur wenn 24 Std mehr als +1 %, Short wenn weniger als -1 %
    "ruecksetzer_1h": 0.4,     # ... und die letzte Stunde mindestens 0,4 % dagegen lief
    "gebuehr": 1.0,
}
API = "https://api.exchange.coinbase.com/products"


def holen(url):
    req = urllib.request.Request(url, headers={"User-Agent": "krypto-demo-bot"})
    with urllib.request.urlopen(req, timeout=20) as r:
        return json.loads(r.read())


def marktdaten():
    m = {}
    for s in COINS:
        kerzen = holen(f"{API}/{s}-EUR/candles?granularity=60")  # neueste zuerst: [t, low, high, open, close, vol]
        kerzen = sorted(kerzen, key=lambda k: k[0])
        stats = holen(f"{API}/{s}-EUR/stats")
        preis = float(kerzen[-1][4])
        vor60 = [k for k in kerzen if k[0] <= kerzen[-1][0] - 3600]
        m[s] = {
            "preis": preis,
            "h24": (preis / float(stats["open"]) - 1) * 100,
            "h1": (preis / float(vor60[-1][4]) - 1) * 100 if vor60 else 0.0,
            "kerzen": [[int(k[0]), float(k[1]), float(k[2]), float(k[4])] for k in kerzen],
        }
    return m


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


def schliessen(st, p, preis, grund, jetzt, betrag=None):
    w = wert(p, preis) if betrag is None else betrag
    geb = 0.0 if betrag == 0 else REGELN["gebuehr"]
    st["bargeld"] += w - geb
    st["positionen"].remove(p)
    st["trades"].append({"ts": jetzt, "s": p["s"], "aktion": "Geschlossen", "short": p["short"], "hebel": p["hebel"],
                         "kurs": preis, "einsatz": p["einsatz"], "betrag": round(w, 2), "ergebnis": round(w - p["einsatz"] - geb - REGELN["gebuehr"], 2),
                         "gebuehr": geb, "grund": grund})
    st["zuletzt_geschlossen"][p["s"]] = jetzt
    return w - p["einsatz"] - geb - REGELN["gebuehr"]


def pruefen(st, m, jetzt):
    """Stop-Loss, Take-Profit, Liquidation und Haltedauer. Kerzen seit dem letzten Lauf, in zeitlicher Reihenfolge."""
    meld = []
    seit = st.get("geprueft_bis", 0)
    for p in list(st["positionen"]):
        ab = max(seit, p["ts"] // 1000)
        for t, low, high, close in m[p["s"]]["kerzen"]:
            if t < ab:
                continue
            schlecht, gut = (high, low) if p["short"] else (low, high)
            ueber = (lambda a, b: a >= b) if p["short"] else (lambda a, b: a <= b)
            # Der Kurs erreicht zuerst die nähere Grenze: Stop-Loss liegt näher als die Liquidation.
            sl_naeher = p.get("sl") and (p["sl"] < p["liq"] if p["short"] else p["sl"] > p["liq"])
            if sl_naeher and ueber(schlecht, p["sl"]):
                e = schliessen(st, p, p["sl"], f"Stop-Loss bei {kurs(p['sl'])}", t * 1000)
                meld.append(f"Stop-Loss bei {COINS[p['s']]}: {eur(e)} inklusive Gebühren.")
                break
            if ueber(schlecht, p["liq"]):
                schliessen(st, p, p["liq"], "liquidiert, Einsatz verloren", t * 1000, betrag=0.0)
                meld.append(f"{COINS[p['s']]} wurde liquidiert, der Einsatz von {eur(p['einsatz'])} ist weg.")
                break
            if (gut <= p["tp"]) if p["short"] else (gut >= p["tp"]):
                e = schliessen(st, p, p["tp"], f"Take-Profit bei {kurs(p['tp'])}", t * 1000)
                meld.append(f"Take-Profit bei {COINS[p['s']]}: +{eur(e)} inklusive Gebühren.")
                break
        else:
            if jetzt - p["ts"] > REGELN["max_haltedauer_min"] * 60000:
                e = schliessen(st, p, m[p["s"]]["preis"], "Haltedauer abgelaufen", jetzt)
                meld.append(f"{COINS[p['s']]} nach {REGELN['max_haltedauer_min']} Minuten geschlossen: {eur(e)}.")
    st["geprueft_bis"] = max(k[0] for s in m for k in m[s]["kerzen"]) + 60
    return meld


def eroeffnen(st, m, jetzt):
    R = REGELN
    offen = {p["s"] for p in st["positionen"]}
    if len(offen) >= R["max_positionen"]:
        return None, "Maximal drei Positionen offen, ich warte auf Stop-Loss, Take-Profit oder Ablauf."
    if st["bargeld"] < R["einsatz"] + R["gebuehr"]:
        return None, "Zu wenig Spielgeld für eine neue Position."
    kandidaten = []
    for s, d in m.items():
        if s in offen or jetzt - st["zuletzt_geschlossen"].get(s, 0) < R["pause_nach_schliessen_min"] * 60000:
            continue
        if d["h24"] > R["trend_24h"] and d["h1"] < -R["ruecksetzer_1h"]:
            kandidaten.append((abs(d["h1"]) + d["h24"] / 4, s, False))
        elif d["h24"] < -R["trend_24h"] and d["h1"] > R["ruecksetzer_1h"]:
            kandidaten.append((abs(d["h1"]) + abs(d["h24"]) / 4, s, True))
    if not kandidaten:
        return None, "Kein Coin erfüllt gerade die Regel. Ich warte auf einen Rücksetzer im Trend."
    _, s, short = max(kandidaten)
    d, preis, h = m[s], m[s]["preis"], R["hebel"]
    p = {"s": s, "short": short, "hebel": h, "einsatz": R["einsatz"], "kurs": preis, "menge": R["einsatz"] * h / preis, "ts": jetzt,
         "sl": preis * (1 + R["stop_loss"] / 100 if short else 1 - R["stop_loss"] / 100),
         "tp": preis * (1 - R["take_profit"] / 100 if short else 1 + R["take_profit"] / 100)}
    p["liq"] = liq_preis(p)
    st["bargeld"] -= R["einsatz"] + R["gebuehr"]
    st["positionen"].append(p)
    grund = f"24 Std {pct(d['h24'])}, letzte Stunde {pct(d['h1'])}"
    st["trades"].append({"ts": jetzt, "s": s, "aktion": "Eröffnet", "short": short, "hebel": h, "kurs": preis, "einsatz": R["einsatz"],
                         "betrag": R["einsatz"], "gebuehr": R["gebuehr"], "grund": grund})
    richtung = "Short (Wette auf fallende Kurse)" if short else "Long (Wette auf steigende Kurse)"
    return p, (f"{richtung} {COINS[s]} {h}x eröffnet, {eur(R['einsatz'])} Einsatz zu {kurs(preis)}. Grund: {grund}. "
               f"Stop-Loss {kurs(p['sl'])}, Take-Profit {kurs(p['tp'])}.")


def main():
    pfad = Path(sys.argv[1] if len(sys.argv) > 1 else "state.json")
    jetzt = int(time.time() * 1000)
    st = json.loads(pfad.read_text()) if pfad.exists() and pfad.stat().st_size > 2 else None
    if not st:
        st = {"start": REGELN["startkapital"], "bargeld": REGELN["startkapital"], "positionen": [], "trades": [], "verlauf": [],
              "zuletzt_geschlossen": {}, "beginn": jetzt, "laeufe": 0}
    m = marktdaten()
    meld = pruefen(st, m, jetzt)
    _, info = eroeffnen(st, m, jetzt)
    gesamt = st["bargeld"] + sum(wert(p, m[p["s"]]["preis"]) for p in st["positionen"])
    st["verlauf"].append([jetzt, round(gesamt, 2)])
    st["verlauf"] = st["verlauf"][-2500:]
    st["trades"] = st["trades"][-300:]
    st["laeufe"] = st.get("laeufe", 0) + 1
    st["aktualisiert"] = jetzt
    st["notiz"] = " ".join(meld + [info])
    st["regeln"] = REGELN
    st["namen"] = COINS
    st["markt"] = {s: {"preis": d["preis"], "h1": round(d["h1"], 3), "h24": round(d["h24"], 3)} for s, d in m.items()}
    pfad.write_text(json.dumps(st, ensure_ascii=False, separators=(",", ":")))
    print(time.strftime("%Y-%m-%d %H:%M:%S"), f"Gesamt {gesamt:.2f} €, Bargeld {st['bargeld']:.2f} €, offen {len(st['positionen'])}")
    print(st["notiz"])


if __name__ == "__main__":
    main()
