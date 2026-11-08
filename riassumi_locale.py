#!/usr/bin/env python3
"""
Appunti di sessione con un LLM locale (Ollama): nessun token a pagamento.

Legge la trascrizione diarizzata, la spezza in finestre di pochi minuti e per
ciascuna chiede al modello locale di estrarre eventi, dialoghi in gioco,
battute fuori gioco, nomi e dubbi, in JSON. Il risultato (appunti .json e
.md compatto) è la base su cui si scrive il diario: chi scrive legge qualche
migliaio di token di appunti invece di decine di migliaia di trascrizione.

Come contesto il modello riceve:
  - contenuti/contesto-llm.md      chi gioca chi, nomi noti, avvertenze;
  - (il diario scritto al tavolo NON va dato alle finestre: il modello lo ricopia ovunque)
  - una seconda registrazione       (opzionale, --master): per esempio il
    microfono vicino al master, sincronizzata con --offset.

Uso:
    python3 riassumi_locale.py fonti/trascrizione-diarizzata-sessione6-centro.json \\
        --master fonti/trascrizione-diarizzata-sessione6-master.json --offset 131.9 \\
        --uscita build/s6-appunti
"""

import argparse
import json
import re
import sys
import time
import urllib.request
from pathlib import Path

RADICE = Path(__file__).resolve().parent
OLLAMA = "http://localhost:11434/api/chat"
MODELLO = "qwen3:30b-a3b-instruct-2507-q4_K_M"

SCHEMA = {
    "type": "object",
    "properties": {
        "riassunto": {"type": "string"},
        "eventi": {"type": "array", "items": {"type": "object", "properties": {
            "ora": {"type": "string"}, "cosa": {"type": "string"}}, "required": ["ora", "cosa"]}},
        "dialoghi": {"type": "array", "items": {"type": "object", "properties": {
            "ora": {"type": "string"}, "chi": {"type": "string"}, "cosa": {"type": "string"}},
            "required": ["ora", "chi", "cosa"]}},
        "fuori_gioco": {"type": "array", "items": {"type": "object", "properties": {
            "ora": {"type": "string"}, "chi": {"type": "string"}, "cosa": {"type": "string"}},
            "required": ["ora", "chi", "cosa"]}},
        "bottino_xp": {"type": "array", "items": {"type": "string"}},
        "nomi": {"type": "array", "items": {"type": "string"}},
        "dubbi": {"type": "array", "items": {"type": "string"}},
    },
    "required": ["riassunto", "eventi", "dialoghi", "fuori_gioco", "bottino_xp", "nomi", "dubbi"],
}

SCHEMA_SINTESI = {
    "type": "object",
    "properties": {
        "capitoli": {"type": "array", "items": {"type": "object", "properties": {
            "titolo": {"type": "string"}, "da": {"type": "string"}, "a": {"type": "string"},
            "sintesi": {"type": "string"},
            "scene_chiave": {"type": "array", "items": {"type": "object", "properties": {
                "ora": {"type": "string"}, "cosa": {"type": "string"}}, "required": ["ora", "cosa"]}}},
            "required": ["titolo", "da", "a", "sintesi", "scene_chiave"]}},
        "bottino_xp": {"type": "array", "items": {"type": "string"}},
        "perle": {"type": "array", "items": {"type": "object", "properties": {
            "ora": {"type": "string"}, "chi": {"type": "string"}, "cosa": {"type": "string"}},
            "required": ["ora", "chi", "cosa"]}},
        "nomi": {"type": "array", "items": {"type": "string"}},
        "dubbi": {"type": "array", "items": {"type": "string"}},
    },
    "required": ["capitoli", "bottino_xp", "perle", "nomi", "dubbi"],
}

ISTRUZIONI_SINTESI = """Hai gli appunti, finestra per finestra, di una sessione di gioco di ruolo. Uniscili in un indice della sessione.

Regole:
- NON inventare: usa solo ciò che c'è negli appunti. Se due finestre si contraddicono, tieni il fatto confermato dal diario e segnalalo nei dubbi.
- "capitoli": da 8 a 12, in ordine, che coprono TUTTA la sessione senza buchi. Ogni capitolo ha un titolo evocativo in italiano (non una descrizione), l'intervallo da/a (mm:ss), una sintesi di 4-8 frasi e le scene chiave con l'orario.
- Una finestra di sole regole, dadi o pausa non è un capitolo: assorbila in quello vicino.
- "perle": le battute fuori gioco più divertenti, senza doppioni, al massimo 15, con orario e giocatore.
- "bottino_xp": oggetti, ricompense, punti esperienza, livelli, senza doppioni.
- "nomi": nomi propri sentiti, grafia più plausibile, senza doppioni.
- "dubbi": al massimo 8, solo su trama e attribuzioni."""

ISTRUZIONI = """Prepara gli appunti per il diario di una sessione di gioco di ruolo a partire dalla trascrizione qui sotto.

Regole:
- NON inventare. Il CONTESTO e il DIARIO descrivono il passato o i fatti principali: usali per capire i nomi, ma NON attribuire alla finestra eventi, luoghi o personaggi che la trascrizione di questa finestra non mostra.
- "dubbi": al massimo 3, solo se riguardano la trama o chi ha fatto cosa. Niente dubbi su parole storpiate dalla trascrizione.
- "riassunto": 3-6 frasi su cosa succede IN GIOCO in questa finestra.
- "eventi": i fatti di trama in ordine, ciascuno con l'ora (mm:ss) in cui accade.
- "dialoghi": le battute IN GIOCO più significative, riscritte pulite ma fedeli al senso; "chi" è il personaggio o PNG che parla (Custode se è la narrazione del master). Al massimo 8.
- "fuori_gioco": solo le battute dei GIOCATORI fuori personaggio davvero memorabili (gaffe, giochi di parole, risate). "chi" è il giocatore. Al massimo 5. Niente discussioni di regole. Se non c'è nulla di davvero divertente, lista vuota.
- "bottino_xp": oggetti ottenuti, ricompense, punti esperienza, livelli.
- "nomi": nomi propri di persone, luoghi, mostri, oggetti sentiti, con la grafia che ti sembra più plausibile.
- Distingui i personaggi dai giocatori. L'etichetta del parlante è una stima: sbaglia spesso; fidati del contenuto.
- Se la finestra è quasi tutta fuori gioco (pausa, regole, chiacchiere), dillo in una frase e lascia le liste vuote."""


def ripulisci(testo: str) -> str:
    """Toglie le ripetizioni fantasma tipiche di Whisper (stessa parola ripetuta)."""
    testo = re.sub(r"\b(\w+)(?:[\s,.…]+\1\b){3,}", r"\1", testo, flags=re.I)
    return re.sub(r"\s+", " ", testo).strip()


def mmss(s: float) -> str:
    s = max(0, int(s))
    return f"{s // 60:02d}:{s % 60:02d}"


def righe(battute, da, a, spostamento=0.0, minimo_parole=2):
    """Battute con inizio in [da, a) sulla linea del tempo principale."""
    out = []
    for b in battute:
        t = b["inizio"] + spostamento
        if not (da <= t < a):
            continue
        testo = ripulisci(b["testo"])
        if len(testo.split()) < minimo_parole:
            continue
        out.append(f"[{mmss(t)}] {b['parlante']}: {testo}")
    return "\n".join(out)


def chiedi(modello, prompt, tentativi=2, schema=None):
    corpo = {
        "model": modello, "stream": False, "format": schema or SCHEMA,
        "options": {"temperature": 0.2, "num_ctx": 32768},
        "messages": [{"role": "user", "content": prompt}],
    }
    for n in range(tentativi):
        richiesta = urllib.request.Request(
            OLLAMA, data=json.dumps(corpo).encode(), headers={"Content-Type": "application/json"})
        with urllib.request.urlopen(richiesta, timeout=1800) as r:
            risposta = json.loads(r.read())
        try:
            return json.loads(risposta["message"]["content"])
        except (json.JSONDecodeError, KeyError):
            print(f"    risposta non valida, riprovo ({n + 1}/{tentativi})", flush=True)
    return None


def sintetizza(appunti, modello, diario=""):
    """Secondo passaggio: fonde gli appunti a finestre in un indice di capitoli."""
    blocchi = []
    for f in appunti:
        b = [f"### {f['da']}-{f['a']}", f["riassunto"]]
        b += [f"- [{e['ora']}] {e['cosa']}" for e in f.get("eventi", [])]
        b += [f"- (fuori gioco) [{v['ora']}] {v['chi']}: {v['cosa']}" for v in f.get("fuori_gioco", [])]
        if f.get("bottino_xp"):
            b.append("- (bottino/xp) " + "; ".join(f["bottino_xp"]))
        blocchi.append("\n".join(b))
    prompt = ISTRUZIONI_SINTESI
    if diario:
        prompt += "\n\n=== DIARIO SCRITTO AL TAVOLO (fatti sicuri) ===\n" + diario
    prompt += "\n\n=== APPUNTI PER FINESTRE ===\n" + "\n\n".join(blocchi) + "\n\nRispondi solo con il JSON."
    return chiedi(modello, prompt, schema=SCHEMA_SINTESI)


def scrivi_sintesi(sint, base: Path):
    base.with_suffix(".json").write_text(json.dumps(sint, ensure_ascii=False, indent=1), "utf-8")
    md = ["# Indice automatico della sessione", ""]
    for i, c in enumerate(sint["capitoli"], 1):
        md += [f"## {i}. {c['titolo']}  ({c['da']}–{c['a']})", c["sintesi"]]
        md += [f"- [{x['ora']}] {x['cosa']}" for x in c.get("scene_chiave", [])]
        md.append("")
    md.append("## Bottino e XP\n" + "\n".join(f"- {x}" for x in sint["bottino_xp"]))
    md.append("\n## Perle candidate\n" + "\n".join(f"- [{x['ora']}] {x['chi']}: {x['cosa']}" for x in sint["perle"]))
    md.append("\n## Nomi\n" + ", ".join(sint["nomi"]))
    md.append("\n## Dubbi\n" + "\n".join(f"- {x}" for x in sint["dubbi"]))
    base.with_suffix(".md").write_text("\n".join(md), "utf-8")


def main():
    ap = argparse.ArgumentParser(description="Appunti di sessione con un LLM locale.")
    ap.add_argument("principale", type=Path, nargs="?", help="JSON diarizzato della registrazione principale")
    ap.add_argument("--sintesi-da", type=Path, help="salta le finestre: fonde un file di appunti .json in un indice")
    ap.add_argument("--master", type=Path, help="JSON diarizzato di una seconda registrazione")
    ap.add_argument("--offset", type=float, default=0.0,
                    help="secondi: t_master = t_principale - offset (positivo se il master parte dopo)")
    ap.add_argument("--diario", type=Path, help="solo per --sintesi-da; sconsigliato (vedi sopra)")
    ap.add_argument("--uscita", type=Path, required=True, help="percorso base di uscita, senza estensione")
    ap.add_argument("--finestra", type=float, default=6.0, help="minuti per finestra")
    ap.add_argument("--modello", default=MODELLO)
    ap.add_argument("--solo", help="da:a in minuti, per provare su un tratto (es. 15:25)")
    args = ap.parse_args()

    if args.sintesi_da:
        appunti = json.loads(args.sintesi_da.read_text("utf-8"))
        diario = args.diario.read_text("utf-8") if args.diario else ""
        print(f"Sintesi di {len(appunti)} finestre…", flush=True)
        sint = sintetizza(appunti, args.modello, diario)
        if not sint:
            sys.exit("Il modello non ha prodotto un JSON valido.")
        scrivi_sintesi(sint, args.uscita)
        print(f"{len(sint['capitoli'])} capitoli → {args.uscita.with_suffix('.md')}")
        return
    if not args.principale:
        ap.error("serve il JSON della trascrizione, oppure --sintesi-da")
    principale = json.loads(args.principale.read_text("utf-8"))
    master = json.loads(args.master.read_text("utf-8")) if args.master else []
    contesto = (RADICE / "contenuti" / "contesto-llm.md").read_text("utf-8")
    diario = args.diario.read_text("utf-8") if args.diario else ""

    fine = max(b["fine"] for b in principale)
    passo = args.finestra * 60
    inizio_s = 0.0
    if args.solo:
        a, b = args.solo.split(":")
        inizio_s, fine = float(a) * 60, float(b) * 60

    appunti, t = [], inizio_s
    totale = int((fine - inizio_s) // passo) + 1
    n = 0
    while t < fine:
        n += 1
        t1 = min(t + passo, fine + 1)
        trasc_a = righe(principale, t, t1)
        if len(trasc_a.split()) < 15:
            t = t1
            continue
        # il master ha t_master = t_principale - offset: per riportarlo sulla linea principale si somma offset
        trasc_b = righe(master, t, t1, spostamento=args.offset, minimo_parole=3) if master else ""

        prompt = [ISTRUZIONI, "\n=== CONTESTO ===\n" + contesto]
        # NON si passa il diario alle finestre: provato, il modello ne ricopia la trama in
        # ogni finestra, anche dove nell'audio succede altro. Serve solo a chi scrive, per
        # il controllo incrociato; --diario vale solo per la sintesi finale.
        prompt.append(f"\n=== FINESTRA {mmss(t)}-{mmss(t1)} ===")
        prompt.append("\n--- TRASCRIZIONE A (microfono al centro del tavolo: si sentono tutti) ---\n" + trasc_a)
        if trasc_b:
            prompt.append("\n--- TRASCRIZIONE B (microfono vicino al master: la voce del Custode è più chiara; "
                          "stessi minuti) ---\n" + trasc_b)
        prompt.append("\nRispondi solo con il JSON.")

        inizio_chiamata = time.time()
        print(f"[{n}/{totale}] {mmss(t)}-{mmss(t1)}  ({len(trasc_a.split())} parole A, "
              f"{len(trasc_b.split())} parole B)…", end=" ", flush=True)
        r = chiedi(args.modello, "\n".join(prompt))
        print(f"{time.time() - inizio_chiamata:.0f}s", flush=True)
        if r:
            r["da"], r["a"] = mmss(t), mmss(t1)
            appunti.append(r)
        t = t1

    args.uscita.parent.mkdir(parents=True, exist_ok=True)
    args.uscita.with_suffix(".json").write_text(json.dumps(appunti, ensure_ascii=False, indent=1), "utf-8")

    md = ["# Appunti automatici", "", f"Modello locale: `{args.modello}`. Da verificare: ogni voce è una stima.", ""]
    for f in appunti:
        md.append(f"## {f['da']}–{f['a']}")
        md.append(f["riassunto"])
        for chiave, titolo in (("eventi", "Eventi"), ("dialoghi", "Dialoghi in gioco"),
                               ("fuori_gioco", "Fuori gioco")):
            if f.get(chiave):
                md.append(f"**{titolo}**")
                for v in f[chiave]:
                    md.append(f"- [{v['ora']}] " + (f"{v['chi']}: " if "chi" in v else "") + (v.get("cosa") or ""))
        if f.get("bottino_xp"):
            md.append("**Bottino/XP** " + "; ".join(f["bottino_xp"]))
        if f.get("nomi"):
            md.append("**Nomi** " + ", ".join(f["nomi"]))
        if f.get("dubbi"):
            md.append("**Dubbi** " + " | ".join(f["dubbi"]))
        md.append("")
    args.uscita.with_suffix(".md").write_text("\n".join(md), "utf-8")
    print(f"\n{len(appunti)} finestre → {args.uscita.with_suffix('.md')}")


if __name__ == "__main__":
    sys.exit(main())
