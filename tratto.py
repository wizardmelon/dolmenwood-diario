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


def fondi(centro, master, offset):
    righe = []
    for b in centro:
        if b["parlante"] not in CUSTODE and b["parlante"] != "non attribuito":
            righe.append((b["inizio"], b["parlante"], b["testo"], "C"))
    for b in master:
        if b["parlante"] in CUSTODE:
            righe.append((b["inizio"] + offset, "Custode", b["testo"], "M"))
    # dove il master non sente nulla del Custode, si recupera quanto dice il centro
    coperto = [(t, t + 3) for t, p, _, o in righe if o == "M"]
    for b in centro:
        if b["parlante"] in CUSTODE or b["parlante"] == "non attribuito":
            if not any(a <= b["inizio"] <= z for a, z in coperto):
                righe.append((b["inizio"], b["parlante"], b["testo"], "C"))
    righe.sort(key=lambda r: r[0])
    return righe


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
    args = ap.parse_args()

    righe = fondi(json.loads(args.centro.read_text("utf-8")),
                  json.loads(args.master.read_text("utf-8")), args.offset)

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
