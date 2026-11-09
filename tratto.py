#!/usr/bin/env python3
"""
Fonde due registrazioni diarizzate e legge un tratto, o cerca una parola.

Il microfono al centro del tavolo sente tutti ma il Custode lontano; quello
vicino al master sente benissimo lui e poco gli altri. La trascrizione fusa
prende i giocatori dal primo e il Custode dal secondo, sulla stessa linea del
tempo (quella del microfono centrale).

    python3 tratto.py fonti/…-centro.json fonti/…-master.json --offset 131.9 63:00 66:00
    python3 tratto.py … --offset 131.9 --cerca "undici|bambina|11 anni"
    python3 tratto.py … --offset 131.9 --scrivi fonti/trascrizione-fusa-sessione6.txt
"""

import argparse
import json
import re
import sys
from pathlib import Path

CUSTODE = ("Custode",)


def mmss(s):
    s = int(max(s, 0))
    return f"{s // 60:02d}:{s % 60:02d}"


def pulisci(t):
    t = re.sub(r"\b(\w+)(?:[\s,.…]+\1\b){3,}", r"\1", t, flags=re.I)
    return re.sub(r"\s+", " ", t).strip()


ALLUCINAZIONE = re.compile(r"(?:\b\d{1,3}\b[\s,.]*){6,}")


def allucinazione(testo):
    """Sequenze di numeri: Whisper che gira a vuoto su rumore o dadi (sessione 6: tre minuti)."""
    return bool(ALLUCINAZIONE.search(testo))


def fondi(centro, master, offset):
    """Giocatori dal microfono centrale, Custode da quello vicino a lui.

    Dal master si prendono anche le righe senza etichetta, marcate «Custode?»: la
    diarizzazione mette lì il secondo registro del Custode (spiegazioni, PNG), e
    scartarle faceva perdere battute decisive. Le righe che sono solo numeri
    (allucinazioni di Whisper) si scartano da entrambe le fonti.
    """
    righe = []
    for b in centro:
        if allucinazione(b["testo"]):
            continue
        if b["parlante"] not in CUSTODE and b["parlante"] != "non attribuito":
            righe.append((b["inizio"], b["parlante"], b["testo"], "C"))
    for b in master:
        if allucinazione(b["testo"]):
            continue
        t = b["inizio"] + offset
        if b["parlante"] in CUSTODE:
            righe.append((t, "Custode", b["testo"], "M"))
        elif b["parlante"] in ("non attribuito", "SCONOSCIUTO"):
            righe.append((t, "Custode?", b["testo"], "M"))
    # dove il master non copre, si recupera quanto dice il centro come Custode
    coperto = [(t, t + 3) for t, p, _, o in righe if o == "M"]
    for b in centro:
        if allucinazione(b["testo"]):
            continue
        if b["parlante"] in CUSTODE or b["parlante"] == "non attribuito":
            if not any(a <= b["inizio"] <= z for a, z in coperto):
                righe.append((b["inizio"], b["parlante"], b["testo"], "C"))
    righe.sort(key=lambda r: r[0])
    return righe


def buchi(battute, offset=0.0, soglia=15):
    """Minuti in cui una trascrizione è per lo più sequenze di numeri."""
    per_minuto = {}
    for b in battute:
        if allucinazione(b["testo"]):
            m = int((b["inizio"] + offset) // 60)
            per_minuto[m] = per_minuto.get(m, 0) + len(b["testo"].split())
    return sorted(m for m, n in per_minuto.items() if n >= soglia)


def stampa(righe, da, a, minimo=2):
    for t, p, testo, _ in righe:
        if da <= t < a:
            testo = pulisci(testo)
            if len(testo.split()) >= minimo:
                print(f"[{mmss(t)}] {p}: {testo}")


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("centro", type=Path)
    ap.add_argument("master", type=Path)
    ap.add_argument("--offset", type=float, required=True, help="t_master = t_centro - offset")
    ap.add_argument("tratto", nargs="?", help="da-a in mm:ss, es. 63:00 66:00 (due argomenti)")
    ap.add_argument("fine", nargs="?")
    ap.add_argument("--cerca", help="espressione regolare da cercare")
    ap.add_argument("--scrivi", type=Path, help="scrive tutta la trascrizione fusa")
    ap.add_argument("--minimo", type=int, default=2, help="parole minime per riga")
    ap.add_argument("--buchi", action="store_true",
                    help="elenca i minuti in cui una registrazione è inutilizzabile (allucinazioni di numeri)")
    args = ap.parse_args()

    centro = json.loads(args.centro.read_text("utf-8"))
    master = json.loads(args.master.read_text("utf-8"))
    if args.buchi:
        for nome, battute, sh in (("centro", centro, 0.0), ("master", master, args.offset)):
            m = buchi(battute, sh)
            print(f"{nome}: " + (", ".join(f"{x:02d}'" for x in m) if m else "nessun buco"))
    righe = fondi(centro, master, args.offset)

    if args.scrivi:
        with open(args.scrivi, "w", encoding="utf-8") as f:
            for t, p, testo, o in righe:
                testo = pulisci(testo)
                if len(testo.split()) >= args.minimo:
                    f.write(f"[{mmss(t)}] {p}: {testo}\n")
        print(f"{args.scrivi} ({len(righe)} righe)")
    if args.cerca:
        rx = re.compile(args.cerca, re.I)
        for t, p, testo, _ in righe:
            if rx.search(testo):
                print(f"[{mmss(t)}] {p}: {pulisci(testo)[:200]}")
    if args.tratto:
        def sec(x):
            m, s = x.split(":")
            return int(m) * 60 + int(s)
        stampa(righe, sec(args.tratto), sec(args.fine), args.minimo)


if __name__ == "__main__":
    sys.exit(main())
