"""Holt Speicher-Wünsche aus der App ab und übernimmt sie in meine_werte.json.

Die Seite schickt jeden Klick auf „Speichern“ oder „Entfernen“ als kleine Nachricht an
ntfy.sh (kein Konto nötig). Dieses Skript läuft vor jedem Bot-Lauf, liest die Nachrichten der
letzten 12 Stunden in zeitlicher Reihenfolge und wendet sie an. Mehrfaches Anwenden ergibt
dasselbe Ergebnis, deshalb braucht es keinen Merker, welche Nachricht schon verarbeitet ist.

Jede Nachricht wird streng geprüft: Aktien und ETFs müssen in universum.json stehen,
Krypto muss bei Coinbase in Euro handelbar sein. Höchstens 40 Werte.

  python werkzeuge/wuensche.py
Schreibt meine_werte.json nur bei Änderungen und gibt dann 'geaendert=1' in GITHUB_OUTPUT aus.
"""
import json
import os
import re
import urllib.request
from pathlib import Path

KANAL = "trading-demo-2851473014d96c49"
DATEI = Path("meine_werte.json")
UNIVERSUM = Path("universum.json")
MAX = 40


def holen(url):
    req = urllib.request.Request(url, headers={"User-Agent": "trading-demo-bot"})
    with urllib.request.urlopen(req, timeout=20) as r:
        return r.read().decode("utf-8", "replace")


def nachrichten():
    zeilen = holen(f"https://ntfy.sh/{KANAL}/json?poll=1&since=12h").splitlines()
    out = []
    for z in zeilen:
        try:
            n = json.loads(z)
            if n.get("event") == "message":
                out.append((n["time"], n["id"], json.loads(n["message"])))
        except (ValueError, KeyError, TypeError):
            continue
    return sorted(out, key=lambda x: (x[0], x[1]))


def main():
    uni = {x[0]: x for x in json.loads(UNIVERSUM.read_text())} if UNIVERSUM.exists() else {}
    krypto_ok = {}
    liste = json.loads(DATEI.read_text()) if DATEI.exists() else []
    vorher = json.dumps(liste)
    log = []
    for _, _, e in nachrichten():
        if not isinstance(e, dict):
            continue
        aktion, sym, typ = str(e.get("aktion", "")), str(e.get("symbol", "")).upper().strip(), str(e.get("typ", ""))
        if aktion not in ("hinzufuegen", "entfernen") or typ not in ("aktie", "etf", "krypto"):
            continue
        if typ == "krypto":
            if not re.fullmatch(r"[A-Z0-9]{2,10}", sym):
                continue
            if sym not in krypto_ok:
                try:
                    p = json.loads(holen(f"https://api.exchange.coinbase.com/products/{sym}-EUR"))
                    krypto_ok[sym] = p.get("status") == "online" and not p.get("trading_disabled")
                except Exception:
                    krypto_ok[sym] = False
            if not krypto_ok[sym]:
                continue
            name, boerse = re.sub(r"[^\w .,&()+'/-]", "", str(e.get("name", "")))[:60].strip() or sym, "Coinbase"
        else:
            u = uni.get(sym)
            if not u or u[2] != typ:
                continue
            name, boerse = u[1], u[3]  # Name und Börse aus der eigenen Liste, nicht aus der Nachricht
        drin = any(x["symbol"] == sym for x in liste)
        if aktion == "hinzufuegen" and not drin:
            if len(liste) >= MAX:
                log.append(f"voll, {sym} nicht übernommen")
                continue
            liste.append({"symbol": sym, "name": name, "typ": typ, "boerse": boerse})
            log.append(f"+ {sym}")
        elif aktion == "entfernen" and drin:
            liste = [x for x in liste if x["symbol"] != sym]
            log.append(f"- {sym}")
    geaendert = json.dumps(liste) != vorher
    if geaendert:
        DATEI.write_text(json.dumps(liste, ensure_ascii=False, indent=1) + "\n")
        if os.environ.get("GITHUB_OUTPUT"):
            with open(os.environ["GITHUB_OUTPUT"], "a") as f:
                f.write("geaendert=1\n")
    print("Wünsche:", ", ".join(log) if log else "keine neuen", "| Liste:", ", ".join(x["symbol"] for x in liste) or "leer")


if __name__ == "__main__":
    main()
