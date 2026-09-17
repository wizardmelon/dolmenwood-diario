#!/usr/bin/env python3
"""
Generatore del Diario di Dolmenwood.

Sorgente unica: i file JSON in contenuti/.
Produce:
  - sito/index.html               elenco delle sessioni
  - sito/sessioni/<slug>.html     una pagina per sessione
  - build/stampa.html             versione impaginata per la stampa
  - pdf/diario-dolmenwood.pdf     PDF (richiede Google Chrome, headless)

Per aggiungere una sessione: creare contenuti/sessione-06.json con la stessa
struttura e aggiungerne il nome in ORDINE, qui sotto. Nient'altro.

Uso:  python3 genera.py [--no-pdf]
"""

import html
import json
import re
import shutil
import subprocess
import sys
from pathlib import Path

RADICE = Path(__file__).resolve().parent
CONTENUTI = RADICE / "contenuti"
SITO = RADICE / "sito"
BUILD = RADICE / "build"
PDF = RADICE / "pdf"

# Ordine di presentazione. Aggiungere qui le sessioni future.
ORDINE = [
    "sessioni-01-04.json",
    "sessione-05.json",
]

CHROME = "/Applications/Google Chrome.app/Contents/MacOS/Google Chrome"

# --------------------------------------------------------------------------
# grafica originale (SVG scritti a mano, nessuna immagine di terze parti)
# --------------------------------------------------------------------------

FIOCCO = """<svg class="fiocco" viewBox="0 0 100 100" role="img" aria-label="Fiocco di neve">
  <defs>
    <linearGradient id="gelo" x1="0" y1="0" x2="0" y2="1">
      <stop offset="0%" stop-color="#ffffff"/>
      <stop offset="55%" stop-color="#7fc8e8"/>
      <stop offset="100%" stop-color="#3d7ea0"/>
    </linearGradient>
    <g id="braccio" fill="none" stroke="url(#gelo)" stroke-width="1.7" stroke-linecap="round">
      <path d="M50 50 V5"/>
      <path d="M43 17 L50 11 L57 17"/>
      <path d="M44.5 30 L50 25 L55.5 30"/>
      <path d="M46 41 L50 37.5 L54 41"/>
    </g>
  </defs>
  <use href="#braccio"/>
  <use href="#braccio" transform="rotate(60 50 50)"/>
  <use href="#braccio" transform="rotate(120 50 50)"/>
  <use href="#braccio" transform="rotate(180 50 50)"/>
  <use href="#braccio" transform="rotate(240 50 50)"/>
  <use href="#braccio" transform="rotate(300 50 50)"/>
  <circle cx="50" cy="50" r="2.6" fill="#d8b364"/>
</svg>"""

MARCHIO = """<svg viewBox="0 0 100 100" aria-hidden="true">
  <g fill="none" stroke="currentColor" stroke-width="7" stroke-linecap="round">
    <path d="M50 12 V88"/>
    <path d="M17 31 L83 69"/>
    <path d="M83 31 L17 69"/>
  </g>
</svg>"""

ORNAMENTO = """<svg viewBox="0 0 100 100" aria-hidden="true">
  <g fill="none" stroke="currentColor" stroke-width="8" stroke-linecap="round">
    <path d="M50 14 V86"/><path d="M20 32 L80 68"/><path d="M80 32 L20 68"/>
  </g>
</svg>"""


# --------------------------------------------------------------------------
# utilità
# --------------------------------------------------------------------------

def esc(testo: str) -> str:
    return html.escape(testo, quote=False)


def marcatura(testo: str) -> str:
    """Converte **grassetto** e *corsivo* in HTML, con escape di tutto il resto."""
    testo = esc(testo)
    testo = re.sub(r"\*\*(.+?)\*\*", r"<strong>\1</strong>", testo)
    testo = re.sub(r"(?<!\*)\*([^*]+?)\*(?!\*)", r"<em>\1</em>", testo)
    return testo


def ancora(titolo: str) -> str:
    base = titolo.lower()
    base = re.sub(r"[àá]", "a", base)
    base = re.sub(r"[èé]", "e", base)
    base = re.sub(r"[ìí]", "i", base)
    base = re.sub(r"[òó]", "o", base)
    base = re.sub(r"[ùú]", "u", base)
    base = re.sub(r"[^a-z0-9]+", "-", base).strip("-")
    return base or "capitolo"


def carica():
    campagna = json.loads((CONTENUTI / "campagna.json").read_text("utf-8"))
    sessioni = [json.loads((CONTENUTI / n).read_text("utf-8")) for n in ORDINE]
    return campagna, sessioni


# --------------------------------------------------------------------------
# frammenti condivisi
# --------------------------------------------------------------------------

def blocco_dialoghi(dialoghi) -> str:
    if not dialoghi:
        return ""
    righe = []
    for d in dialoghi:
        chi = esc(d["chi"])
        classe = " custode" if d["chi"].lower() in ("custode", "master", "dm") else ""
        nota = f' <span class="nota">({esc(d["nota"])})</span>' if d.get("nota") else ""
        righe.append(
            f'<p class="battuta{classe}">'
            f'<span class="chi">{chi}{nota}</span>'
            f'<span class="cosa">{marcatura(d["cosa"])}</span></p>'
        )
    return '<div class="scena">\n' + "\n".join(righe) + "\n</div>"


def blocco_capitoli(sessione, con_ancore=True) -> str:
    parti = []
    for i, cap in enumerate(sessione["capitoli"], 1):
        aid = f' id="{ancora(cap["titolo"])}"' if con_ancore else ""
        corpo = "\n".join(f"<p>{marcatura(p)}</p>" for p in cap["testo"])
        parti.append(
            f'<section class="capitolo"{aid}>\n'
            f'<h2><span class="cifra">Capitolo {i}</span>{esc(cap["titolo"])}</h2>\n'
            f"{corpo}\n{blocco_dialoghi(cap.get('dialoghi'))}\n</section>"
        )
    return "\n".join(parti)


def blocco_perle(sessione) -> str:
    if not sessione.get("perle"):
        return ""
    voci = "\n".join(
        f'<div class="perla"><h3>{esc(p["titolo"])}</h3><p>{marcatura(p["testo"])}</p></div>'
        for p in sessione["perle"]
    )
    return (
        f'<section class="capitolo" id="perle-dal-tavolo">\n'
        f'<h2><span class="cifra">Fuori personaggio</span>Perle dal tavolo</h2>\n'
        f'<div class="perle">\n{voci}\n</div>\n</section>'
    )


def blocco_note(sessione) -> str:
    if not sessione.get("note"):
        return ""
    voci = "\n".join(f"<li>{marcatura(n)}</li>" for n in sessione["note"])
    return f'<section class="note"><h2>Note di ricostruzione</h2><ul>\n{voci}\n</ul></section>'


def blocco_personaggi(campagna) -> str:
    voci = []
    for p in campagna["personaggi"]:
        voci.append(
            '<li class="personaggio">'
            f'<div class="riga"><h3>{esc(p["nome"])}</h3>'
            f'<span class="stirpe">{esc(p["stirpe"])}</span></div>'
            f'<div class="giocatore">giocato da {esc(p["giocatore"])}</div>'
            f'<p>{marcatura(p["nota_stirpe"])}</p>'
            f'<p>{marcatura(p["ruolo"])}</p></li>'
        )
    return '<ul class="griglia-personaggi">\n' + "\n".join(voci) + "\n</ul>"


def pagina(titolo, corpo, prefisso="", descrizione=""):
    return f"""<!DOCTYPE html>
<html lang="it">
<head>
<meta charset="utf-8">
<meta name="viewport" content="width=device-width, initial-scale=1">
<title>{esc(titolo)}</title>
<meta name="description" content="{esc(descrizione)}">
<link rel="stylesheet" href="{prefisso}css/stile.css">
<link rel="icon" href="data:image/svg+xml,<svg xmlns='http://www.w3.org/2000/svg' viewBox='0 0 100 100'><text y='.9em' font-size='90'>&#10052;</text></svg>">
</head>
<body>
<div class="guscio">
{corpo}
</div>
</body>
</html>
"""


def barra(prefisso=""):
    return f"""<nav class="barra">
  <a class="marchio" href="{prefisso}index.html">{MARCHIO}<span>Dolmenwood</span></a>
  <span class="spazio"></span>
  <a href="{prefisso}index.html#sessioni">Sessioni</a>
  <a href="{prefisso}index.html#compagnia">Compagnia</a>
  <a href="{prefisso}index.html#glossario">Glossario</a>
</nav>"""


def piede(campagna):
    return f"""<footer class="piede">
  <p>{esc(campagna["titolo"])} — diario di gioco amatoriale.</p>
  <p>Ambientazione e regolamento: {esc(campagna["sistema"])}. Questo diario racconta
  fatti accaduti al nostro tavolo con parole nostre; non riproduce testo, mappe o
  illustrazioni dei manuali. Tutte le immagini di questo sito sono SVG originali.</p>
</footer>"""


# --------------------------------------------------------------------------
# sito
# --------------------------------------------------------------------------

def genera_indice(campagna, sessioni):
    schede = []
    for s in sessioni:
        etichetta = f"Sessione {s['numero']}" if str(s["numero"]) != "1-4" else "Sessioni 1-4"
        schede.append(
            f'<li><a class="scheda" href="sessioni/{s["slug"]}.html">'
            f'<span class="numero">{esc(str(s["numero"]))}</span>'
            f'<h2>{esc(s["titolo"])}</h2>'
            f'<p class="sottotitolo">{esc(s["sottotitolo"])}</p>'
            f'<div class="meta"><span>{esc(etichetta)}</span>'
            f'<span>{esc(s["avventura"])}</span><span>{esc(s["durata"])}</span></div>'
            f"</a></li>"
        )
    schede.append(
        '<li><div class="scheda prossima"><span class="numero">6</span>'
        "<h2>La torre</h2>"
        '<p class="sottotitolo">Da giocare — il finale di Winter\'s Daughter</p>'
        '<div class="meta"><span>prossimamente</span></div></div></li>'
    )

    glossario = "\n".join(
        f'<dt>{esc(v["voce"])}</dt><dd>{marcatura(v["desc"])}</dd>'
        for v in campagna["glossario"]
    )

    corpo = f"""{barra()}
<main class="contenuto largo">
  <header class="testata">
    {FIOCCO}
    <p class="occhiello">{esc(campagna["sistema"])}</p>
    <h1>{esc(campagna["titolo"])}</h1>
    <p class="intro">{marcatura(campagna["introduzione"])}</p>
  </header>

  <div class="filetto">{ORNAMENTO}</div>

  <section id="sessioni">
    <h2 class="indice-titolo" style="font-family:var(--sans);font-size:.72rem;letter-spacing:.2em;text-transform:uppercase;color:var(--ghiaccio-cupo);margin:0 0 1.25rem">Le sessioni</h2>
    <ul class="elenco-sessioni">
      {"".join(schede)}
    </ul>
  </section>

  <div class="filetto">{ORNAMENTO}</div>

  <section id="compagnia">
    <h2 class="capitolo-titolo" style="font-size:1.6rem;margin:0 0 1.25rem">La compagnia</h2>
    {blocco_personaggi(campagna)}
  </section>

  <div class="filetto">{ORNAMENTO}</div>

  <section id="glossario">
    <h2 style="font-size:1.6rem;margin:0 0 1.25rem">Chi è chi, cos'è cosa</h2>
    <dl class="glossario">{glossario}</dl>
  </section>
</main>
{piede(campagna)}"""

    (SITO / "index.html").write_text(
        pagina(campagna["titolo"], corpo, "", campagna["introduzione"]), "utf-8"
    )


def genera_sessione(campagna, sessioni, i):
    s = sessioni[i]
    prec = sessioni[i - 1] if i > 0 else None
    succ = sessioni[i + 1] if i + 1 < len(sessioni) else None
    etichetta = f"Sessione {s['numero']}" if str(s["numero"]) != "1-4" else "Sessioni 1-4"

    voci_indice = [
        f'<li><a href="#{ancora(c["titolo"])}">{esc(c["titolo"])}</a></li>'
        for c in s["capitoli"]
    ]
    if s.get("perle"):
        voci_indice.append('<li><a href="#perle-dal-tavolo">Perle dal tavolo</a></li>')

    nav_prec = (
        f'<a href="{prec["slug"]}.html">&larr; {esc(prec["titolo"])}</a>'
        if prec else '<span class="vuoto">&larr; inizio</span>'
    )
    nav_succ = (
        f'<a href="{succ["slug"]}.html">{esc(succ["titolo"])} &rarr;</a>'
        if succ else '<span class="vuoto">da giocare &rarr;</span>'
    )

    corpo = f"""{barra("../")}
<main class="contenuto">
  <header class="testata-sessione">
    <p class="etichetta">{esc(etichetta)} &middot; {esc(s["avventura"])}</p>
    <h1>{esc(s["titolo"])}</h1>
    <p class="sottotitolo">{esc(s["sottotitolo"])}</p>
    <div class="dettagli"><span>{esc(s["durata"])}</span></div>
    <p class="avviso">{marcatura(s["occhiello"])}</p>
  </header>

  <nav class="indice"><h2>In questa sessione</h2><ol>{"".join(voci_indice)}</ol></nav>

  {blocco_capitoli(s)}
  {blocco_perle(s)}
  {blocco_note(s)}

  <nav class="nav-sessioni">{nav_prec}{nav_succ}</nav>
</main>
{piede(campagna)}"""

    (SITO / "sessioni" / f"{s['slug']}.html").write_text(
        pagina(f"{s['titolo']} — {campagna['titolo']}", corpo, "../", s["sottotitolo"]),
        "utf-8",
    )


# --------------------------------------------------------------------------
# PDF
# --------------------------------------------------------------------------

CSS_STAMPA = """
@page { size: A4; margin: 20mm 18mm 18mm; }
@page :first { margin: 0; }
* { box-sizing: border-box; }
html { -webkit-print-color-adjust: exact; print-color-adjust: exact; }
body {
  margin: 0;
  font-family: "Iowan Old Style", Palatino, "Book Antiqua", Georgia, serif;
  font-size: 10.6pt; line-height: 1.62; color: #16203a;
  background: #fbfcfe;
}
h1, h2, h3 { font-weight: 600; }
strong { color: #0b1020; }

/* copertina */
.copertina {
  height: 297mm; width: 210mm;
  display: flex; flex-direction: column; align-items: center; justify-content: center;
  text-align: center; page-break-after: always;
  background: #0b1020;
  background-image:
    radial-gradient(ellipse 70% 45% at 50% 8%, rgba(61,126,160,.45), transparent 70%),
    radial-gradient(ellipse 60% 40% at 50% 100%, rgba(79,122,90,.25), transparent 70%);
  color: #e8eef7;
}
.copertina svg { width: 120px; height: 120px; margin-bottom: 14mm; }
.copertina .occhiello { font-size: 8.5pt; letter-spacing: .28em; text-transform: uppercase; color: #d8b364; margin: 0 0 8mm; }
.copertina h1 { font-size: 34pt; line-height: 1.08; margin: 0 0 6mm; color: #fff; letter-spacing: -.01em; }
.copertina .sottotitolo { font-size: 13pt; font-style: italic; color: #7fc8e8; margin: 0 0 14mm; }
.copertina .intro { max-width: 120mm; font-size: 10pt; color: #aebcd2; font-style: italic; line-height: 1.7; }
.copertina .piedecop { position: absolute; bottom: 18mm; font-size: 8pt; letter-spacing: .18em; text-transform: uppercase; color: #5f708e; }

/* struttura */
.parte { page-break-before: always; }
.occhiello-sessione { font-size: 8pt; letter-spacing: .24em; text-transform: uppercase; color: #8a6d34; margin: 0 0 3mm; }
h1.titolo-sessione { font-size: 22pt; line-height: 1.15; margin: 0 0 3mm; color: #16203a; }
.sottotitolo-sessione { font-size: 11pt; font-style: italic; color: #4d5c7a; margin: 0 0 4mm; }
.avviso {
  border: .4pt dashed #b0bed4; border-radius: 3pt; padding: 3mm 4mm;
  font-size: 8.6pt; line-height: 1.55; color: #4d5c7a; margin: 0 0 9mm;
  font-family: -apple-system, "Segoe UI", Helvetica, sans-serif;
}
.regola { border: 0; border-top: .6pt solid #c9a961; width: 26mm; margin: 0 0 8mm; }

.capitolo { page-break-inside: auto; margin-bottom: 8mm; }
.capitolo h2 { font-size: 14.5pt; margin: 0 0 1.5mm; page-break-after: avoid; color: #16203a; }
.capitolo .cifra { display: block; font-size: 7.5pt; letter-spacing: .22em; text-transform: uppercase; color: #8a6d34; margin-bottom: 1.5mm; font-family: -apple-system, "Segoe UI", Helvetica, sans-serif; }
.capitolo h2::after { content: ""; display: block; width: 18mm; border-top: .5pt solid #c9a961; margin-top: 2.5mm; }
.capitolo p { margin: 0 0 3.2mm; text-align: justify; hyphens: auto; }

.scena {
  margin: 4mm 0 5mm; padding: 3.5mm 4.5mm;
  border-left: 1.2pt solid #3d7ea0; background: #eef4f9;
  page-break-inside: avoid;
}
.battuta { margin: 0 0 2.6mm; }
.battuta:last-child { margin-bottom: 0; }
.battuta .chi {
  display: block; font-size: 7.4pt; font-weight: 700; letter-spacing: .13em;
  text-transform: uppercase; color: #2d6f92;
  font-family: -apple-system, "Segoe UI", Helvetica, sans-serif;
}
.battuta .chi .nota { font-weight: 400; text-transform: none; letter-spacing: 0; font-style: italic; color: #6b7a96; }
.battuta .cosa { display: block; }
.battuta.custode .chi { color: #8a6d34; }
.battuta.custode .cosa { font-style: italic; color: #4d5c7a; }

.perle { margin-top: 2mm; }
.perla { border: .4pt solid #e0cb9a; background: #fdf8ee; border-radius: 3pt; padding: 3.5mm 4.5mm; margin-bottom: 3.5mm; page-break-inside: avoid; }
.perla h3 { font-size: 10.5pt; color: #8a6320; margin: 0 0 1.5mm; }
.perla p { margin: 0; font-size: 9.6pt; color: #3c4a66; }

.note { border-top: .5pt solid #c8d2e2; margin-top: 8mm; padding-top: 4mm; }
.note h2 { font-size: 8pt; letter-spacing: .2em; text-transform: uppercase; color: #6b7a96; margin: 0 0 3mm; font-family: -apple-system, "Segoe UI", Helvetica, sans-serif; }
.note ul { margin: 0; padding-left: 5mm; }
.note li { font-size: 9pt; color: #4d5c7a; margin-bottom: 2mm; }

/* fronte: compagnia */
.personaggio { page-break-inside: avoid; margin-bottom: 5mm; padding-left: 4mm; border-left: 1pt solid #c9a961; }
.personaggio h3 { margin: 0; font-size: 11.5pt; }
.personaggio .meta { font-size: 8pt; letter-spacing: .1em; text-transform: uppercase; color: #8a6320; margin: .8mm 0 1.6mm; font-family: -apple-system, "Segoe UI", Helvetica, sans-serif; }
.personaggio p { margin: 0 0 1.6mm; font-size: 9.6pt; color: #3c4a66; }

dl.glossario { margin: 0; }
dl.glossario dt { font-weight: 600; color: #8a6320; margin-top: 3.5mm; }
dl.glossario dd { margin: .6mm 0 0; font-size: 9.6pt; color: #3c4a66; }

h2.sezione { font-size: 17pt; margin: 0 0 5mm; }
"""


def genera_stampa(campagna, sessioni):
    parti = [
        f"""<div class="copertina">
  {FIOCCO}
  <p class="occhiello">{esc(campagna["sistema"])}</p>
  <h1>{esc(campagna["titolo"])}</h1>
  <p class="sottotitolo">{esc(campagna["sottotitolo"])}</p>
  <p class="intro">{marcatura(campagna["introduzione"])}</p>
  <p class="piedecop">Avventura: {esc(campagna["avventura_corrente"])}</p>
</div>"""
    ]

    # la compagnia
    voci = []
    for p in campagna["personaggi"]:
        voci.append(
            f'<div class="personaggio"><h3>{esc(p["nome"])}</h3>'
            f'<div class="meta">{esc(p["stirpe"])} &middot; giocato da {esc(p["giocatore"])}</div>'
            f'<p>{marcatura(p["nota_stirpe"])}</p><p>{marcatura(p["ruolo"])}</p></div>'
        )
    glossario = "".join(
        f'<dt>{esc(v["voce"])}</dt><dd>{marcatura(v["desc"])}</dd>'
        for v in campagna["glossario"]
    )
    parti.append(
        f'<div class="parte"><h2 class="sezione">La compagnia</h2>{"".join(voci)}'
        f'<h2 class="sezione" style="margin-top:10mm">Chi è chi, cos\'è cosa</h2>'
        f'<dl class="glossario">{glossario}</dl></div>'
    )

    for s in sessioni:
        etichetta = f"Sessione {s['numero']}" if str(s["numero"]) != "1-4" else "Sessioni 1-4"
        parti.append(
            f'<div class="parte">'
            f'<p class="occhiello-sessione">{esc(etichetta)} &middot; {esc(s["avventura"])} &middot; {esc(s["durata"])}</p>'
            f'<h1 class="titolo-sessione">{esc(s["titolo"])}</h1>'
            f'<p class="sottotitolo-sessione">{esc(s["sottotitolo"])}</p>'
            f'<hr class="regola">'
            f'<p class="avviso">{marcatura(s["occhiello"])}</p>'
            f"{blocco_capitoli(s, con_ancore=False)}"
            f"{blocco_perle(s)}"
            f"{blocco_note(s)}"
            f"</div>"
        )

    doc = f"""<!DOCTYPE html>
<html lang="it"><head><meta charset="utf-8">
<title>{esc(campagna["titolo"])}</title>
<style>{CSS_STAMPA}</style></head>
<body>{"".join(parti)}</body></html>
"""
    BUILD.mkdir(exist_ok=True)
    percorso = BUILD / "stampa.html"
    percorso.write_text(doc, "utf-8")
    return percorso


def genera_pdf(sorgente: Path) -> bool:
    if not Path(CHROME).exists():
        print("  ! Google Chrome non trovato: salto la generazione del PDF.")
        return False
    PDF.mkdir(exist_ok=True)
    uscita = PDF / "diario-dolmenwood.pdf"
    comando = [
        CHROME, "--headless", "--disable-gpu", "--no-pdf-header-footer",
        "--virtual-time-budget=6000",
        f"--print-to-pdf={uscita}", sorgente.as_uri(),
    ]
    esito = subprocess.run(comando, capture_output=True, text=True)
    if uscita.exists() and uscita.stat().st_size > 0:
        print(f"  · {uscita.relative_to(RADICE)} ({uscita.stat().st_size // 1024} KB)")
        return True
    print("  ! Chrome non ha prodotto il PDF:", esito.stderr[-400:])
    return False


# --------------------------------------------------------------------------

def main():
    campagna, sessioni = carica()
    (SITO / "sessioni").mkdir(parents=True, exist_ok=True)

    print("Sito:")
    genera_indice(campagna, sessioni)
    print("  · sito/index.html")
    for i, s in enumerate(sessioni):
        genera_sessione(campagna, sessioni, i)
        print(f"  · sito/sessioni/{s['slug']}.html")

    print("Stampa:")
    sorgente = genera_stampa(campagna, sessioni)
    print(f"  · {sorgente.relative_to(RADICE)}")

    if "--no-pdf" not in sys.argv:
        genera_pdf(sorgente)

    print("Fatto.")


if __name__ == "__main__":
    main()
