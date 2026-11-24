"""Nachrichten lesen und bewerten.

Quellen (kostenlos, ohne Konto):
  Welt und Wirtschaft: BBC World, BBC Business, Tagesschau, CNBC Top News
  Krypto: CoinDesk
  Je beobachtetem Wert: Yahoo Finance (Meldungen zum Kürzel)
Bewertung: Claude (Anthropic-API, Schlüssel als GitHub-Secret ANTHROPIC_API_KEY, Modell über
KI_MODELL einstellbar) liest alle neuen Schlagzeilen auf einmal und gibt je betroffenem Wert einen Wert von -1 (sehr negativ)
bis +1 (sehr positiv), eine Sicherheit und einen kurzen Grund zurück. Ohne KI greift eine
einfache Wortliste. Ergebnis landet in state["news"].
"""
import email.utils
import json
import os
import re
import time
import urllib.error
import urllib.parse
import urllib.request
import xml.etree.ElementTree as ET

FEEDS = {
    "BBC World": "https://feeds.bbci.co.uk/news/world/rss.xml",
    "BBC Business": "https://feeds.bbci.co.uk/news/business/rss.xml",
    "Tagesschau": "https://www.tagesschau.de/xml/rss2/",
    "CNBC": "https://www.cnbc.com/id/100003114/device/rss/rss.html",
    "CoinDesk": "https://www.coindesk.com/arc/outboundfeeds/rss/",
}
MAX_ALTER_H = 6          # ältere Meldungen ignorieren
KI_ABSTAND_MIN = 15      # höchstens alle 15 Minuten die KI fragen (Freikontingent)
KI_MODELL = os.environ.get("KI_MODELL") or "claude-opus-5-5"

POS = r"\b(beat|beats|surge|surges|soar|soars|jump|jumps|rall(y|ies)|record|upgrade|upgraded|raises? guidance|strong demand|approval|approved|buyback|profit rises|rekord|steigt|gewinnsprung|übertrifft|hochgestuft)\b"
NEG = r"\b(falls?|drops?|slides?|sinks?|declines?|down \d|miss|misses|plunge|plunges|slump|slumps|tumble|tumbles|crash|downgrade|downgraded|cuts? guidance|lawsuit|probe|investigation|recall|fraud|hack|hacked|bankrupt|layoffs?|sanction|ban|warns?|sinkt|einbruch|verlust|herabgestuft|klage|ermittlung)\b"


def holen(url, daten=None, kopf=None, zeit=20):
    req = urllib.request.Request(url, data=daten, headers={"User-Agent": "Mozilla/5.0 (trading-demo)", **(kopf or {})})
    with urllib.request.urlopen(req, timeout=zeit) as r:
        return r.read()


def schlagzeilen(werte, jetzt_s):
    """Liste von Meldungen: {id, titel, quelle, ts, bezug (Symbol oder '')}."""
    out, gesehen = [], set()

    def neu(titel, quelle, ts, bezug="", link=""):
        titel = re.sub(r"\s+", " ", titel or "").strip()
        key = re.sub(r"\W", "", titel.lower())[:80]
        if not titel or key in gesehen or jetzt_s - ts > MAX_ALTER_H * 3600:
            return
        gesehen.add(key)
        out.append({"id": key[:40], "titel": titel[:200], "quelle": quelle, "ts": int(ts), "bezug": bezug, "link": link if link.startswith("https://") else ""})

    for name, url in FEEDS.items():
        try:
            root = ET.fromstring(holen(url))
            for it in root.findall(".//item")[:30]:
                d = it.findtext("pubDate")
                ts = email.utils.parsedate_to_datetime(d).timestamp() if d else jetzt_s
                neu(it.findtext("title"), name, ts, "", it.findtext("link") or "")
        except Exception:
            continue
    for s, (name, klasse) in werte.items():
        q = s.replace(".DE", "") if klasse != "krypto" else f"{s}-USD"
        if s.endswith(".DE"):
            q = name  # deutsche Kürzel liefern bei Yahoo kaum News, der Name schon
        try:
            d = json.loads(holen(f"https://query1.finance.yahoo.com/v1/finance/search?q={urllib.request.quote(q)}&newsCount=4&quotesCount=0"))
            for n in d.get("news", []):
                neu(n.get("title"), n.get("publisher", "Yahoo"), n.get("providerPublishTime", jetzt_s), s, n.get("link", ""))
        except Exception:
            continue
    return sorted(out, key=lambda x: -x["ts"])


SCHEMA = {
    "type": "object",
    "properties": {
        "stimmung": {"type": "number"},
        "stimmung_grund": {"type": "string"},
        "werte": {"type": "array", "items": {
            "type": "object",
            "properties": {
                "symbol": {"type": "string"},
                "score": {"type": "number"},
                "sicherheit": {"type": "number"},
                "grund": {"type": "string"},
                "meldungen": {"type": "array", "items": {"type": "integer"}},
            },
            "required": ["symbol", "score", "sicherheit", "grund", "meldungen"],
            "additionalProperties": False,
        }},
    },
    "required": ["stimmung", "stimmung_grund", "werte"],
    "additionalProperties": False,
}


def ki_bewerten(meldungen, werte):
    """Claude bewertet die Schlagzeilen. Braucht ANTHROPIC_API_KEY (GitHub-Secret), sonst None."""
    if not os.environ.get("ANTHROPIC_API_KEY") or not meldungen:
        return None
    try:
        import anthropic
    except ImportError:
        print("KI: Paket anthropic fehlt")
        return None
    liste = "\n".join(f"{i}. [{m['quelle']}{', zu ' + m['bezug'] if m['bezug'] else ''}] {m['titel']}" for i, m in enumerate(meldungen[:60]))
    werte_txt = ", ".join(f"{s} ({n})" for s, (n, _) in werte.items())
    prompt = (
        "Du bewertest Schlagzeilen für einen Demo-Trading-Bot, der mit Spielgeld handelt. Schätze ein, wie die Meldungen "
        "den Kurs der folgenden Werte in den nächsten Stunden beeinflussen.\n"
        f"Werte: {werte_txt}\n\nSchlagzeilen:\n{liste}\n\n"
        "Nenne nur Werte, die von mindestens einer Schlagzeile direkt oder klar indirekt betroffen sind (Weltnachrichten wie "
        "Zinsen, Kriege oder Zölle wirken auf Indizes, ETFs und Krypto). Bekannte, schon eingepreiste Nachrichten gewichtest "
        "du schwächer. score von -1 (sehr negativ) bis 1 (sehr positiv), sicherheit von 0 bis 1, grund ein kurzer Satz auf "
        "Deutsch, meldungen die Nummern der Schlagzeilen. stimmung ist die allgemeine Marktstimmung von -1 bis 1."
    )
    try:
        client = anthropic.Anthropic(max_retries=2, timeout=90.0)
        r = client.messages.create(
            model=KI_MODELL,
            max_tokens=4000,
            output_config={"effort": "low", "format": {"type": "json_schema", "schema": SCHEMA}},
            messages=[{"role": "user", "content": prompt}],
        )
    except anthropic.AuthenticationError:
        print("KI: API-Schlüssel ungültig")
        return None
    except anthropic.RateLimitError:
        print("KI: Limit erreicht, später erneut")
        return None
    except anthropic.APIStatusError as e:
        print("KI-Fehler", e.status_code, str(e.message)[:200])
        return None
    except anthropic.APIConnectionError:
        print("KI: keine Verbindung")
        return None
    if r.stop_reason != "end_turn":
        print("KI: unvollständige Antwort,", r.stop_reason)
        return None
    text = next((b.text for b in r.content if b.type == "text"), "")
    try:
        return json.loads(text)
    except ValueError:
        print("KI: Antwort kein JSON")
        return None


def wortliste(meldungen, werte):
    werte_out = {}
    for m in meldungen:
        if not m["bezug"]:
            continue
        # Verb am Satzanfang zählt, "after huge rally" usw. nicht: nur die ersten 8 Wörter
        kopf = " ".join(m["titel"].split()[:8])
        p, n = len(re.findall(POS, kopf, re.I)), len(re.findall(NEG, kopf, re.I))
        if p == n:
            continue
        w = werte_out.setdefault(m["bezug"], {"symbol": m["bezug"], "score": 0.0, "sicherheit": 0.4, "grund": "", "meldungen": []})
        w["score"] = max(-1, min(1, w["score"] + (0.35 if p > n else -0.35)))
        w["grund"] = f"Schlagzeile: {m['titel'][:90]}"
    return {"stimmung": 0, "stimmung_grund": "Ohne KI bewertet (Wortliste).", "werte": list(werte_out.values())}


def aktualisieren(st, werte, jetzt):
    """Holt Meldungen, bewertet neue höchstens alle 15 Minuten, schreibt st['news']."""
    jetzt_s = jetzt // 1000
    alt = st.get("news", {})
    meldungen = schlagzeilen(werte, jetzt_s)
    bekannt = set(alt.get("gelesen", []))
    neue = [m for m in meldungen if m["id"] not in bekannt]
    news = {**alt, "meldungen": meldungen[:40], "stand": jetzt}
    ki_zuletzt_ok = alt.get("quelle", "").startswith("KI")
    faellig = jetzt - alt.get("bewertet", 0) >= KI_ABSTAND_MIN * 60000 or not ki_zuletzt_ok
    if (neue or not ki_zuletzt_ok) and faellig:
        erg = ki_bewerten(meldungen[:60], werte)
        quelle = f"KI ({KI_MODELL})" if erg else "Wortliste"
        erg = erg or wortliste(meldungen, werte)
        sig = {}
        for w in erg.get("werte", []):
            s = str(w.get("symbol", "")).upper()
            if s not in werte:
                continue
            try:
                sc, si = max(-1.0, min(1.0, float(w.get("score", 0)))), max(0.0, min(1.0, float(w.get("sicherheit", 0))))
            except (TypeError, ValueError):
                continue
            idx = [i for i in w.get("meldungen", []) if isinstance(i, int) and 0 <= i < len(meldungen)]
            sig[s] = {"score": round(sc, 2), "sicherheit": round(si, 2), "grund": str(w.get("grund", ""))[:200], "ts": jetzt,
                      "titel": [meldungen[i]["titel"] for i in idx[:3]]}
        # Signale älter als 3 Stunden verfallen, neue überschreiben
        bisher = {s: v for s, v in alt.get("signale", {}).items() if jetzt - v["ts"] < 3 * 3600000}
        news.update({"signale": {**bisher, **sig}, "bewertet": jetzt, "quelle": quelle,
                     "stimmung": erg.get("stimmung", 0), "stimmung_grund": str(erg.get("stimmung_grund", ""))[:200],
                     "gelesen": list({m["id"] for m in meldungen} | bekannt)[-400:]})
    else:
        news["signale"] = {s: v for s, v in alt.get("signale", {}).items() if jetzt - v["ts"] < 3 * 3600000}
    st["news"] = news
    return news["signale"]
