"""Übernimmt einen Eintrag aus einem GitHub-Issue in meine_werte.json.

Wird nur vom Workflow meine-werte.yml aufgerufen, und nur für Issues des Repo-Besitzers.
Titel und Text kommen als Umgebungsvariablen (nie in die Shell eingesetzt) und werden streng geprüft.
Ausgabe: eine Zeile Text für den Kommentar am Issue.
"""
import json
import os
import re
import sys
from pathlib import Path

DATEI = Path("meine_werte.json")
MAX = 40
TYPEN = {"aktie", "etf", "krypto"}


def fehler(text):
    print(text)
    sys.exit(0)


def main():
    text = os.environ.get("ISSUE_BODY", "")
    m = re.search(r"\{[^{}]{0,600}\}", text)
    if not m:
        fehler("Kein gültiger Eintrag gefunden, nichts geändert.")
    try:
        e = json.loads(m.group(0))
    except ValueError:
        fehler("Eintrag ist kein gültiges JSON, nichts geändert.")
    aktion, sym, typ = str(e.get("aktion", "")), str(e.get("symbol", "")).upper(), str(e.get("typ", ""))
    name = re.sub(r"[^\w .,&()+'/-]", "", str(e.get("name", "")), flags=re.UNICODE)[:80].strip() or sym
    boerse = re.sub(r"[^\w .-]", "", str(e.get("boerse", "")))[:20]
    if aktion not in ("hinzufuegen", "entfernen"):
        fehler("Unbekannte Aktion, nichts geändert.")
    if typ not in TYPEN:
        fehler("Unbekannter Typ, nichts geändert.")
    muster = r"[A-Z0-9]{2,10}" if typ == "krypto" else r"[A-Z0-9][A-Z0-9.\-]{0,14}"
    if not re.fullmatch(muster, sym):
        fehler("Ungültiges Kürzel, nichts geändert.")

    liste = json.loads(DATEI.read_text()) if DATEI.exists() else []
    liste = [x for x in liste if x["symbol"] != sym]
    if aktion == "hinzufuegen":
        if len(liste) >= MAX:
            fehler(f"Die Liste ist voll ({MAX} Werte). Bitte erst einen entfernen.")
        liste.append({"symbol": sym, "name": name, "typ": typ, "boerse": boerse})
        msg = f"{name} ({sym}) ist gespeichert. Der Bot handelt den Wert ab dem nächsten Lauf mit."
    else:
        msg = f"{sym} ist entfernt. Offene Positionen schließt der Bot weiter nach seinen Regeln."
    DATEI.write_text(json.dumps(liste, ensure_ascii=False, indent=1) + "\n")
    print(msg)


if __name__ == "__main__":
    main()
