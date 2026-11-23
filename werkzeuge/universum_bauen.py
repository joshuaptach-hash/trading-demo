"""Baut universum.json, die Suchliste der Seite, aus öffentlichen Börsenlisten.

  python werkzeuge/universum_bauen.py ORDNER_MIT_LISTEN universum.json

Erwartet im Ordner:
  nasdaqlisted.txt, otherlisted.txt  (nasdaqtrader.com/dynamic/SymDir/, NASDAQ und NYSE/NYSE Arca/Cboe)
  instr.csv                          (Xetra: alle handelbaren Instrumente, xetra.com)
Ausgabe: Liste von [Yahoo-Symbol, Name, Typ (aktie|etf), Börse, ISIN]
"""
import csv
import json
import re
import sys
from pathlib import Path

RAUS = re.compile(r"\b(warrants?|units?|rights?|preferred|depositary shares? representing|notes due|debentures|subordinated|"
                  r"when issued|trust preferred|% )", re.I)
BOERSE = {"Q": "NASDAQ", "N": "NYSE", "A": "NYSE American", "P": "NYSE Arca", "Z": "Cboe", "V": "IEX"}


def schoen_us(n):
    n = re.sub(r"\s*-\s*(Class [A-Z] )?(Common Stock|Ordinary Shares|American Depositary Shares).*$", lambda m: f" {m.group(1) or ''}".rstrip(), n)
    n = re.sub(r"\s+(Common Stock|Ordinary Shares?|Class [A-Z] Common Stock|Common Shares)$", "", n)
    return n.strip(" -")


XETRA_REST = re.compile(r"\s+(O\.N\.|INH\.?|NA|NAM\.-?AKT\.?|INH\.AKT\.?|VZO|ST|DL|EO|SF|LS|REG\.?\s*SHS?\.?|SHS|ADR)(?=[\s,.\-\d]|$).*$")


def schoen_xetra(n):
    n = XETRA_REST.sub("", n).strip()
    # GROSSBUCHSTABEN lesbarer machen, Kürzel wie AG, SE, ETF, MSCI bleiben
    return " ".join(w if len(w) <= 4 or any(c.isdigit() or c in ".+()&-" for c in w) else w.capitalize() for w in n.split())


def main():
    q, ziel = Path(sys.argv[1]), Path(sys.argv[2])
    out, gesehen = [], set()

    def neu(sym, name, typ, boerse, isin=""):
        if sym in gesehen or not name:
            return
        gesehen.add(sym)
        out.append([sym, name, typ, boerse, isin])

    for f, sym_i, ex_i, etf_i, test_i in (("nasdaqlisted.txt", 0, None, 6, 3), ("otherlisted.txt", 0, 2, 4, 6)):
        for r in csv.reader(open(q / f, encoding="utf-8", errors="replace"), delimiter="|"):
            if len(r) < 7 or r[0] in ("Symbol", "ACT Symbol") or r[0].startswith("File Creation"):
                continue
            if r[test_i] == "Y" or RAUS.search(r[1]) or not re.fullmatch(r"[A-Z]{1,5}(\.[A-Z])?", r[sym_i]):
                continue
            etf = r[etf_i] == "Y"
            neu(r[sym_i].replace(".", "-"), r[1].strip() if etf else schoen_us(r[1]), "etf" if etf else "aktie",
                "NASDAQ" if ex_i is None else BOERSE.get(r[ex_i], "USA"))

    rows = list(csv.reader(open(q / "instr.csv", encoding="utf-8-sig", errors="replace"), delimiter=";"))
    ix = {k.strip(): i for i, k in enumerate(rows[2])}
    for r in rows[3:]:
        if len(r) < 20 or r[ix["Instrument Status"]] != "Active" or not r[ix["Mnemonic"]]:
            continue
        typ = "aktie" if r[ix["Instrument Type"]] == "CS" else "etf"
        neu(r[ix["Mnemonic"]] + ".DE", schoen_xetra(r[ix["Instrument"]]), typ, "Xetra", r[ix["ISIN"]])

    ziel.write_text(json.dumps(out, ensure_ascii=False, separators=(",", ":")))
    print(len(out), "Werte,", sum(1 for x in out if x[2] == "aktie"), "Aktien,", sum(1 for x in out if x[2] == "etf"), "ETFs/ETCs")


if __name__ == "__main__":
    main()
