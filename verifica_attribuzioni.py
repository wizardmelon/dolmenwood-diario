#!/usr/bin/env python3
"""
Controlla le attribuzioni dei dialoghi del diario contro la trascrizione
diarizzata, prima di pubblicare.

Per ogni battuta di un personaggio nel JSON della sessione cerca nella
trascrizione la battuta che contiene più parole significative e confronta chi
l'ha detta (secondo la voce) con il giocatore del personaggio. Segnala solo le
discordanze, con un grado di affidabilità: una battuta lunga e ben coperta pesa
molto più di un frammento di mezzo secondo.

Non decide nulla: indica dove guardare. Le voci di scena del Custode (Sir Cidio,
i goblin) vengono spesso attribuite a un giocatore: lo script le segnala a parte.

Uso:
    python3 verifica_attribuzioni.py contenuti/sessione-06.json \\
        fonti/trascrizione-diarizzata-sessione6-centro.json
"""

import argparse
import json
import re
import sys
from pathlib import Path

RADICE = Path(__file__).resolve().parent


def norma(t: str) -> str:
    t = t.lower().replace("'", " ").replace("’", " ")
    return re.sub(r"[^a-zàèéìòù0-9 ]", " ", t)


def giocatori_per_personaggio():
    campagna = json.loads((RADICE / "contenuti" / "campagna.json").read_text("utf-8"))
    mappa = {}
    for p in campagna["personaggi"]:
        mappa[p["nome"]] = p["giocatore"].split()[0]
    return mappa


def main():
    ap = argparse.ArgumentParser(description="Verifica le attribuzioni dei dialoghi.")
    ap.add_argument("sessione", type=Path, help="contenuti/sessione-NN.json")
    ap.add_argument("trascrizione", type=Path, nargs="+",
                    help="uno o più JSON diarizzati (per esempio centro e master): una battuta "
                         "concorda se concorda in almeno uno")
    ap.add_argument("--tutto", action="store_true", help="mostra anche le discordanze poco affidabili")
    args = ap.parse_args()

    sessione = json.loads(args.sessione.read_text("utf-8"))
    indici = [[(norma(b["testo"]), b) for b in json.loads(f.read_text("utf-8"))]
              for f in args.trascrizione]
    attesi = giocatori_per_personaggio()

    controllate = ok = 0
    alte, basse, custode = [], [], []
    for cap in sessione["capitoli"]:
        for d in cap.get("dialoghi", []):
            chiavi = [w for w in norma(d["cosa"]).split() if len(w) > 4]
            if len(chiavi) < 3:
                continue
            # per ogni trascrizione, la battuta che contiene più parole chiave
            candidati = []
            for indice in indici:
                migliore, n = None, 0
                for testo, b in indice:
                    k = sum(1 for c in chiavi if c in testo)
                    if k > n:
                        migliore, n = b, k
                if migliore and n / len(chiavi) >= 0.5:
                    candidati.append((n, migliore))
            if not candidati:
                continue
            controllate += 1
            atteso = attesi.get(d["chi"], "Custode")
            if any(atteso in b["parlante"] or "non attribuito" in b["parlante"]
                   or b["parlante"] == "SCONOSCIUTO" for _, b in candidati):
                ok += 1
                continue
            n, migliore = max(candidati, key=lambda x: x[0])
            copertura = n / len(chiavi)
            trovato = migliore["parlante"]
            durata = migliore["fine"] - migliore["inizio"]
            voce = (f"[{int(migliore['inizio']) // 60:02d}:{int(migliore['inizio']) % 60:02d}] "
                    f"«{d['cosa'][:60]}…»\n      diario: {d['chi']} ({atteso})  |  voce: {trovato}  "
                    f"|  battuta {durata:.1f}s, parole {copertura:.0%}")
            if atteso == "Custode":
                custode.append(voce)
            elif durata >= 3 and copertura >= 0.7 and n >= 4:
                alte.append(voce)
            else:
                basse.append(voce)

    print(f"Dialoghi controllati: {controllate} — concordano: {ok}\n")
    for titolo, lista in (("DISCORDANZE AFFIDABILI (da correggere o verificare)", alte),
                          ("VOCI DI SCENA DEL CUSTODE attribuite a un giocatore (attese)", custode)):
        print(f"== {titolo}: {len(lista)}")
        for v in lista:
            print("  " + v)
        print()
    print(f"== Discordanze su frammenti brevi, poco affidabili: {len(basse)}"
          + ("" if args.tutto else "  (--tutto per vederle)"))
    if args.tutto:
        for v in basse:
            print("  " + v)


if __name__ == "__main__":
    sys.exit(main())
