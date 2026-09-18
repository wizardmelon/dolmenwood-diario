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
MODELLO_IMPRONTE = "pyannote/embedding"

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

    if torch.backends.mps.is_available():
        # su Apple Silicon alcune operazioni di pyannote non hanno kernel MPS:
        # la CPU dei Mac Apple Silicon è comunque rapida su questo carico.
        print("    (MPS disponibile; si usa la CPU, più affidabile per pyannote)")

    parametri = {}
    if parlanti:
        parametri["num_speakers"] = parlanti
    else:
        if minimo:
            parametri["min_speakers"] = minimo
        if massimo:
            parametri["max_speakers"] = massimo

    annotazione = pipeline(str(wav), **parametri)
    turni = [
        {"inizio": seg.start, "fine": seg.end, "parlante": etichetta}
        for seg, _, etichetta in annotazione.itertracks(yield_label=True)
    ]
    print(f"    trovati {len(set(t['parlante'] for t in turni))} parlanti, {len(turni)} turni")
    return turni


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
        if campione.suffix.lower() not in (".wav", ".m4a", ".mp3", ".flac", ".aac"):
            continue
        with tempfile.TemporaryDirectory() as tmp:
            wav = in_wav(campione, Path(tmp) / "c.wav")
            note[campione.stem] = torch.tensor(inferenza(str(wav))).flatten()
        print(f"    impronta di {campione.stem}")
    return note


def assegna_nomi(wav: Path, battute, note, token: str, soglia=0.25):
    """Rinomina SPEAKER_XX con il nome più vicino, se abbastanza simile."""
    import torch
    from pyannote.audio import Model, Inference
    from pyannote.core import Segment

    modello = _carica(Model.from_pretrained, MODELLO_IMPRONTE, token)
    inferenza = Inference(modello, window="whole")

    # per ogni parlante, le battute più lunghe: il campione più pulito che abbiamo
    per_parlante = {}
    for b in battute:
        per_parlante.setdefault(b["parlante"], []).append(b)

    mappa = {}
    for parlante, elenco in per_parlante.items():
        elenco.sort(key=lambda b: b["fine"] - b["inizio"], reverse=True)
        vettori = []
        for b in elenco[:8]:
            if b["fine"] - b["inizio"] < 1.5:
                continue
            try:
                v = inferenza.crop(str(wav), Segment(b["inizio"], b["fine"]))
                vettori.append(torch.tensor(v).flatten())
            except Exception:
                continue
        if not vettori:
            continue
        medio = torch.stack(vettori).mean(0)
        punteggi = {
            nome: torch.nn.functional.cosine_similarity(medio, vet, dim=0).item()
            for nome, vet in note.items()
        }
        nome, punteggio = max(punteggi.items(), key=lambda kv: kv[1])
        if punteggio >= soglia:
            mappa[parlante] = nome
            print(f"    {parlante} → {nome} (somiglianza {punteggio:.2f})")
        else:
            print(f"    {parlante} → nessuna corrispondenza (migliore {nome} {punteggio:.2f})")
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
    ap.add_argument("--cache", type=Path, help="dove tenere la trascrizione grezza (JSON)")
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
        turni = diarizza(wav, token, args.parlanti, args.min_parlanti, args.max_parlanti)

        print("Unione:")
        battute = unisci(trascrizione, turni)
        print(f"  · {len(battute)} battute")

        mappa = None
        if args.voci:
            if not args.voci.is_dir():
                sys.exit(f"Cartella campioni non trovata: {args.voci}")
            print("Enrollment:")
            note = impronte_note(args.voci, token)
            if note:
                mappa = assegna_nomi(wav, battute, note, token)

        print("Uscita:")
        scrivi(battute, args.uscita, mappa)
        riepilogo(battute, mappa)


if __name__ == "__main__":
    main()
