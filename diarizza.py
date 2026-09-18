#!/usr/bin/env python3
"""
Trascrizione con riconoscimento del parlante (diarizzazione).

Pipeline:
  1. Whisper large-v3 (mlx-whisper) con timestamp a livello di parola.
  2. pyannote.audio 3.1 per segmentare l'audio per voce.
  3. Ogni parola viene assegnata al parlante che copre il suo intervallo;
     le parole consecutive dello stesso parlante vengono raggruppate in battute.
  4. (Opzionale) I parlanti anonimi SPEAKER_00, SPEAKER_01... vengono rinominati
     con i nomi veri, o tramite un file di mappatura o tramite enrollment.

Serve un token HuggingFace con accesso ai modelli pyannote (vedi README).
Passarlo con --token, oppure nella variabile d'ambiente HF_TOKEN, oppure
salvato con `huggingface-cli login`.

ENROLLMENT (consigliato per le sessioni future)
-----------------------------------------------
Se esiste una cartella di campioni — un file audio per giocatore, dai 15
secondi in su, con quella sola persona che parla:

    voci/
      Andrea.wav
      Flama.wav
      Vanni.wav
      Marco.wav

lo script calcola l'impronta vocale di ciascuno e assegna automaticamente i
nomi ai parlanti trovati, invece di lasciare SPEAKER_00. Indicala con
--voci voci/.

Uso tipico:
    python3 diarizza.py fonti/audio/Dolmenwood_-_Sessione_5.m4a \\
        --uscita fonti/trascrizione-diarizzata-sessione5 \\
        --parlanti 5
"""

import argparse
import json
import os

# Alcune operazioni di pyannote non hanno un kernel Metal: senza questo
# fallback su CPU la pipeline si ferma con un errore invece di proseguire.
os.environ.setdefault("PYTORCH_ENABLE_MPS_FALLBACK", "1")
import subprocess
import sys
import tempfile
from pathlib import Path

RADICE = Path(__file__).resolve().parent

MODELLO_WHISPER = "mlx-community/whisper-large-v3-mlx"
MODELLO_DIARIZZAZIONE = "pyannote/speaker-diarization-community-1"
MODELLO_SEGMENTAZIONE = "pyannote/segmentation-3.0"
MODELLO_VOCI = "pyannote/wespeaker-voxceleb-resnet34-LM"

# Iperparametri pubblicati nel config.yaml di speaker-diarization-3.1.
# Servono a ricostruire la pipeline a mano quando la libreria tenta di
# reindirizzare a un modello gated diverso (vedi costruisci_pipeline).
PARAMETRI_3_1 = {
    "clustering": {"method": "centroid", "min_cluster_size": 12,
                   "threshold": 0.7045654963945799},
    "segmentation": {"min_duration_off": 0.0},
}
# Lo stesso modello di impronte usato dalla pipeline di diarizzazione: misurato
# sui campioni della sessione 5, separa molto meglio di pyannote/embedding
# (match corretti 0,64-0,74 contro sbagliati sotto 0,48; con l'altro modello i
# due gruppi si sovrapponevano).
MODELLO_IMPRONTE = "pyannote/wespeaker-voxceleb-resnet34-LM"

PROMPT = (
    "Sessione di gioco di ruolo Dolmenwood. Personaggi: Oleggio Brucaboschi, "
    "Slurbel Piedemuffo, Andante Tagliaserpi, Secchio di Brodo, Urca Snobrughiera. "
    "NPC: Sir Cidio, Nevicata al Crepuscolo, Principe Gelido, Morchia, breggle, "
    "goblin, druni, tumulo, anello, torre."
)


# --------------------------------------------------------------------------

def token_hf(esplicito=None) -> str:
    if esplicito:
        return esplicito
    for chiave in ("HF_TOKEN", "HUGGINGFACE_TOKEN", "HUGGING_FACE_HUB_TOKEN"):
        if os.environ.get(chiave):
            return os.environ[chiave]
    salvato = Path.home() / ".cache" / "huggingface" / "token"
    if salvato.exists():
        return salvato.read_text().strip()
    sys.exit(
        "Manca il token HuggingFace.\n"
        "  1. Crea un token (read) su https://huggingface.co/settings/tokens\n"
        f"  2. Accetta i termini su https://huggingface.co/{MODELLO_DIARIZZAZIONE}\n"
        "     (il token deve essere di tipo Read, oppure fine-grained con il\n"
        "     permesso di lettura dei repository gated)\n"
        "  3. Rilancia con --token hf_xxx oppure esporta HF_TOKEN."
    )


def in_wav(sorgente: Path, destinazione: Path) -> Path:
    """Converte in WAV mono 16 kHz: formato atteso sia da Whisper sia da pyannote."""
    if sorgente.suffix.lower() == ".wav":
        return sorgente
    subprocess.run(
        ["ffmpeg", "-y", "-loglevel", "error", "-i", str(sorgente),
         "-ar", "16000", "-ac", "1", "-c:a", "pcm_s16le", str(destinazione)],
        check=True,
    )
    return destinazione


# --------------------------------------------------------------------------

def _carica(costruttore, nome, token):
    """pyannote 4 usa `token=`, la 3.x usava `use_auth_token=`."""
    try:
        return costruttore(nome, token=token)
    except TypeError:
        return costruttore(nome, use_auth_token=token)


def trascrivi(wav: Path, cache: Path):
    if cache.exists():
        print(f"  · trascrizione già presente: {cache.name}")
        return json.loads(cache.read_text("utf-8"))

    import mlx_whisper
    print("  · Whisper large-v3 con timestamp per parola (alcuni minuti)…")
    risultato = mlx_whisper.transcribe(
        str(wav), path_or_hf_repo=MODELLO_WHISPER, language="it",
        verbose=False, word_timestamps=True, initial_prompt=PROMPT,
    )
    cache.write_text(json.dumps(risultato, ensure_ascii=False), "utf-8")
    return risultato



def costruisci_pipeline(token: str):
    """Carica la pipeline di diarizzazione 3.1.

    pyannote.audio 4 reindirizza il nome `speaker-diarization-3.1` al modello
    `speaker-diarization-community-1`, che è gated separatamente. Se quel
    reindirizzamento fallisce, la pipeline viene ricostruita a mano dai suoi
    componenti (segmentazione + impronte vocali) con gli iperparametri
    pubblicati nel config.yaml della 3.1: stesso risultato, senza dipendere
    da un modello a cui il token non ha accesso. `legacy=True` disattiva il
    clustering PLDA della 4.x, che scaricherebbe pesi da quel modello.
    """
    from pyannote.audio import Pipeline
    from pyannote.audio.pipelines import SpeakerDiarization

    try:
        pipeline = _carica(Pipeline.from_pretrained, MODELLO_DIARIZZAZIONE, token)
        if pipeline is not None:
            return pipeline
        print("    (nome non risolto, ricostruisco la pipeline dai componenti)")
    except Exception as errore:
        print(f"    ({type(errore).__name__}: ricostruisco la pipeline dai componenti)")

    try:
        pipeline = SpeakerDiarization(
            legacy=True,
            segmentation=MODELLO_SEGMENTAZIONE,
            embedding=MODELLO_VOCI,
            clustering="AgglomerativeClustering",
            embedding_exclude_overlap=True,
            segmentation_batch_size=32,
            embedding_batch_size=32,
            token=token,
        )
    except TypeError:
        pipeline = SpeakerDiarization(
            legacy=True,
            segmentation=MODELLO_SEGMENTAZIONE,
            embedding=MODELLO_VOCI,
            clustering="AgglomerativeClustering",
            embedding_exclude_overlap=True,
            segmentation_batch_size=32,
            embedding_batch_size=32,
            use_auth_token=token,
        )
    pipeline.instantiate(PARAMETRI_3_1)
    return pipeline


def diarizza(wav: Path, token: str, parlanti=None, minimo=None, massimo=None):
    import torch
    from pyannote.audio import Pipeline

    print("  · pyannote.audio (il primo avvio scarica i modelli)…")
    pipeline = costruisci_pipeline(token)

    # Su Apple Silicon la GPU fa una differenza enorme: misurato su M1 Ultra,
    # 0,08x il tempo reale contro 1,4x su CPU, cioè circa diciassette volte più
    # veloce. Se lo spostamento fallisce si prosegue su CPU senza interrompere.
    if torch.backends.mps.is_available():
        try:
            pipeline.to(torch.device("mps"))
            print("    (GPU Apple Silicon)")
        except Exception as errore:
            print(f"    (MPS non utilizzabile, si prosegue su CPU: {type(errore).__name__})")
    elif torch.cuda.is_available():
        pipeline.to(torch.device("cuda"))
        print("    (GPU CUDA)")

    parametri = {}
    if parlanti:
        parametri["num_speakers"] = parlanti
    else:
        if minimo:
            parametri["min_speakers"] = minimo
        if massimo:
            parametri["max_speakers"] = massimo

    risultato = pipeline(str(wav), **parametri)

    # pyannote 4 restituisce un DiarizeOutput; la 3.x un Annotation diretto.
    # Di DiarizeOutput interessa `exclusive_speaker_diarization`, che esclude le
    # sovrapposizioni: serve proprio ad allineare una trascrizione, dove ogni
    # parola deve finire a un solo parlante.
    annotazione = getattr(risultato, "exclusive_speaker_diarization", None)
    if annotazione is None:
        annotazione = getattr(risultato, "speaker_diarization", risultato)

    turni = [
        {"inizio": seg.start, "fine": seg.end, "parlante": etichetta}
        for seg, _, etichetta in annotazione.itertracks(yield_label=True)
    ]

    # la 4.x calcola già un'impronta per parlante: si riusa per l'enrollment
    impronte = {}
    vettori = getattr(risultato, "speaker_embeddings", None)
    if vettori is not None:
        for etichetta, vettore in zip(annotazione.labels(), vettori):
            impronte[etichetta] = vettore

    print(f"    trovati {len(set(t['parlante'] for t in turni))} parlanti, {len(turni)} turni")
    return turni, impronte


# --------------------------------------------------------------------------

def parlante_di(inizio, fine, turni):
    """Il parlante che si sovrappone di più all'intervallo della parola."""
    migliore, punteggio = None, 0.0
    for t in turni:
        sovrapposizione = min(fine, t["fine"]) - max(inizio, t["inizio"])
        if sovrapposizione > punteggio:
            migliore, punteggio = t["parlante"], sovrapposizione
    return migliore


def unisci(trascrizione, turni, stacco=1.2):
    """Assegna un parlante a ogni parola e raggruppa in battute."""
    parole = []
    for seg in trascrizione["segments"]:
        for w in (seg.get("words") or []):
            testo = w.get("word", "").strip()
            if testo:
                parole.append((w["start"], w["end"], testo))
    if not parole:
        sys.exit("La trascrizione non contiene timestamp per parola.")

    battute = []
    for inizio, fine, testo in parole:
        chi = parlante_di(inizio, fine, turni) or "SCONOSCIUTO"
        if battute and battute[-1]["parlante"] == chi and inizio - battute[-1]["fine"] <= stacco:
            battute[-1]["parole"].append(testo)
            battute[-1]["fine"] = fine
        else:
            battute.append({"parlante": chi, "inizio": inizio, "fine": fine, "parole": [testo]})

    for b in battute:
        b["testo"] = " ".join(b.pop("parole"))
    return [b for b in battute if b["testo"]]


# --------------------------------------------------------------------------

def impronte_note(cartella: Path, token: str):
    """Calcola un'impronta vocale per ogni campione in cartella/<Nome>.<ext>."""
    import torch
    from pyannote.audio import Model, Inference

    modello = _carica(Model.from_pretrained, MODELLO_IMPRONTE, token)
    inferenza = Inference(modello, window="whole")

    note = {}
    for campione in sorted(cartella.iterdir()):
        # i vocali di WhatsApp arrivano in .opus, quelli di iPhone in .m4a
        if campione.suffix.lower() not in (".wav", ".m4a", ".mp3", ".flac", ".aac",
                                           ".opus", ".ogg", ".webm", ".amr", ".mp4"):
            continue
        with tempfile.TemporaryDirectory() as tmp:
            wav = in_wav(campione, Path(tmp) / "c.wav")
            note[campione.stem] = torch.tensor(inferenza(str(wav))).flatten()
        print(f"    impronta di {campione.stem}")
    return note


def assegna_nomi(wav: Path, battute, note, token: str, soglia=0.45):
    """Rinomina SPEAKER_XX confrontando le voci con i campioni registrati.

    Si assegna solo in caso di **preferenza reciproca**: il campione deve essere
    il migliore per quel gruppo di voci *e* quel gruppo deve essere il migliore
    per quel campione. Serve a evitare due errori visti sul campo:

    - un parlante senza campione (per esempio il Custode) veniva battezzato con
      il nome di chi gli somigliava di più, pur senza somigliargli davvero;
    - due campioni della stessa persona (voce normale e voce di scena) si
      facevano concorrenza, e il parlante restava senza nome.
    """
    import torch
    from pyannote.audio import Model, Inference
    from pyannote.core import Segment

    modello = _carica(Model.from_pretrained, MODELLO_IMPRONTE, token)
    inferenza = Inference(modello, window="whole")

    per_parlante = {}
    for b in battute:
        per_parlante.setdefault(b["parlante"], []).append(b)

    # impronta media di ogni parlante, dalle sue battute più lunghe
    medie = {}
    for parlante, elenco in per_parlante.items():
        elenco.sort(key=lambda b: b["fine"] - b["inizio"], reverse=True)
        vettori = []
        for b in elenco[:12]:
            if b["fine"] - b["inizio"] < 2:
                continue
            try:
                v = inferenza.crop(str(wav), Segment(b["inizio"], b["fine"]))
                vettori.append(torch.tensor(v).flatten())
            except Exception:
                continue
        if vettori:
            medie[parlante] = torch.stack(vettori).mean(0)

    cos = lambda a, b: torch.nn.functional.cosine_similarity(a, b, dim=0).item()
    punteggi = {(p, n): cos(v, w) for p, v in medie.items() for n, w in note.items()}

    mappa = {}
    for parlante in medie:
        candidati = sorted(note, key=lambda n: -punteggi[(parlante, n)])
        nome = candidati[0]
        valore = punteggi[(parlante, nome)]

        # il campione preferisce a sua volta questo parlante?
        preferito = max(medie, key=lambda p: punteggi[(p, nome)])

        if valore < soglia:
            print(f"    {parlante} → nessun nome (migliore {nome} {valore:.2f}, sotto {soglia})")
        elif preferito != parlante:
            print(f"    {parlante} → nessun nome ({nome} {valore:.2f}, ma «{nome}» "
                  f"somiglia di più a {preferito})")
        else:
            mappa[parlante] = nome
            print(f"    {parlante} → {nome} ({valore:.2f})")
    return mappa


# --------------------------------------------------------------------------

def scrivi(battute, base: Path, mappa=None):
    mappa = mappa or {}
    base.parent.mkdir(parents=True, exist_ok=True)

    righe = []
    for b in battute:
        m, s = divmod(int(b["inizio"]), 60)
        chi = mappa.get(b["parlante"], b["parlante"])
        righe.append(f"[{m:02d}:{s:02d}] {chi}: {b['testo']}")
    (base.with_suffix(".txt")).write_text("\n".join(righe) + "\n", "utf-8")

    dati = [
        {**b, "parlante": mappa.get(b["parlante"], b["parlante"]), "etichetta": b["parlante"]}
        for b in battute
    ]
    (base.with_suffix(".json")).write_text(json.dumps(dati, ensure_ascii=False, indent=1), "utf-8")

    print(f"  · {base.with_suffix('.txt')}")
    print(f"  · {base.with_suffix('.json')}")



def scrivi_scheda(battute, base: Path, quante=8):
    """Elenca le battute più lunghe di ogni parlante, con l'orario nella
    registrazione: serve a riconoscere a orecchio chi è ciascun SPEAKER_XX."""
    per_parlante = {}
    for b in battute:
        per_parlante.setdefault(b["parlante"], []).append(b)

    righe = ["# Chi è chi", "",
             "Per ogni voce trovata, le battute più lunghe con il minuto in cui si",
             "trovano nella registrazione. Ascoltarne una basta per riconoscere la",
             "persona; poi si scrive la corrispondenza in un file, per esempio",
             "`nomi.json`, e si rilancia con `--nomi nomi.json`:", "",
             '```json',
             '{ "SPEAKER_00": "Secchio di Brodo", "SPEAKER_04": "Custode" }',
             '```', ""]
    ordine = sorted(per_parlante, key=lambda k: -sum(x["fine"] - x["inizio"] for x in per_parlante[k]))
    for parlante in ordine:
        elenco = sorted(per_parlante[parlante], key=lambda b: len(b["testo"]), reverse=True)
        minuti = sum(x["fine"] - x["inizio"] for x in per_parlante[parlante]) / 60
        righe.append(f"## {parlante} — {minuti:.1f} minuti, {len(per_parlante[parlante])} battute")
        righe.append("")
        for b in elenco[:quante]:
            m, sec = divmod(int(b["inizio"]), 60)
            righe.append(f"- **{m:02d}:{sec:02d}** {b['testo'][:190]}")
        righe.append("")

    percorso = base.with_name(base.name + "-chi-e-chi.md")
    percorso.write_text("\n".join(righe), "utf-8")
    print(f"  · {percorso}")


def riepilogo(battute, mappa=None):
    mappa = mappa or {}
    durate, conteggi = {}, {}
    for b in battute:
        chi = mappa.get(b["parlante"], b["parlante"])
        durate[chi] = durate.get(chi, 0) + (b["fine"] - b["inizio"])
        conteggi[chi] = conteggi.get(chi, 0) + 1
    totale = sum(durate.values()) or 1
    print("\nTempo di parola:")
    for chi, d in sorted(durate.items(), key=lambda kv: -kv[1]):
        print(f"  {chi:<16} {d/60:5.1f} min  {d/totale*100:4.1f}%  ({conteggi[chi]} battute)")


# --------------------------------------------------------------------------

def main():
    ap = argparse.ArgumentParser(description="Trascrizione con riconoscimento del parlante.")
    ap.add_argument("audio", type=Path, help="file audio della sessione")
    ap.add_argument("--uscita", type=Path, required=True, help="percorso base dei file di uscita (senza estensione)")
    ap.add_argument("--token", help="token HuggingFace (altrimenti HF_TOKEN)")
    ap.add_argument("--parlanti", type=int, help="numero esatto di persone al tavolo, se noto")
    ap.add_argument("--min-parlanti", type=int)
    ap.add_argument("--max-parlanti", type=int)
    ap.add_argument("--voci", type=Path, help="cartella con un campione audio per giocatore (enrollment)")
    ap.add_argument("--nomi", type=Path, help="file JSON {\"SPEAKER_00\": \"Vanni\", ...} per rinominare a mano")
    ap.add_argument("--cache", type=Path, help="dove tenere la trascrizione grezza (JSON)")
    ap.add_argument("--ricalcola", action="store_true",
                    help="rifà la diarizzazione invece di riusare quella già salvata")
    args = ap.parse_args()

    if not args.audio.exists():
        sys.exit(f"File non trovato: {args.audio}")

    token = token_hf(args.token)
    cache = args.cache or args.uscita.with_name(args.uscita.name + "-grezza.json")

    with tempfile.TemporaryDirectory() as tmp:
        print("Preparazione audio:")
        wav = in_wav(args.audio, Path(tmp) / "sessione.wav")
        print(f"  · {wav.name}")

        print("Trascrizione:")
        trascrizione = trascrivi(wav, cache)

        print("Diarizzazione:")
        turni_json = args.uscita.with_name(args.uscita.name + "-turni.json")
        if turni_json.exists() and not args.ricalcola:
            # la diarizzazione è la parte lenta: riusarla permette di riprovare
            # l'assegnazione dei nomi in pochi secondi anziché in una decina di minuti
            turni = json.loads(turni_json.read_text("utf-8"))
            print(f"  · riuso {turni_json.name} ({len(turni)} turni) — --ricalcola per rifarla")
        else:
            turni, _ = diarizza(wav, token, args.parlanti, args.min_parlanti, args.max_parlanti)
            turni_json.parent.mkdir(parents=True, exist_ok=True)
            turni_json.write_text(json.dumps(turni, ensure_ascii=False), "utf-8")

        print("Unione:")
        battute = unisci(trascrizione, turni)
        print(f"  · {len(battute)} battute")

        mappa = {}
        if args.voci:
            if not args.voci.is_dir():
                sys.exit(f"Cartella campioni non trovata: {args.voci}")
            print("Enrollment:")
            note = impronte_note(args.voci, token)
            if note:
                mappa = assegna_nomi(wav, battute, note, token)
        if args.nomi:
            # i nomi scritti a mano colmano i buchi lasciati dall'enrollment,
            # senza scavalcare quello che è stato riconosciuto dalla voce
            manuali = json.loads(args.nomi.read_text("utf-8"))
            print("Nomi a mano:")
            for etichetta, nome in manuali.items():
                if etichetta in mappa:
                    print(f"  · {etichetta} già riconosciuto come {mappa[etichetta]}, ignoro «{nome}»")
                else:
                    mappa[etichetta] = nome
                    print(f"  · {etichetta} → {nome}")
        mappa = mappa or None

        print("Uscita:")
        scrivi(battute, args.uscita, mappa)
        if not mappa:
            scrivi_scheda(battute, args.uscita)
        riepilogo(battute, mappa)


if __name__ == "__main__":
    main()
