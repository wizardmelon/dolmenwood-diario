# Diario di Dolmenwood

Diario di campagna della compagnia di Morchia — campagna amatoriale di
**Dolmenwood RPG / Old-School Essentials** (Necrotic Gnome), avventura
*Winter's Daughter*.

Il progetto genera, da una sorgente unica, sia un **sito statico** pubblicabile
su GitHub Pages sia un **PDF impaginato**.

## Struttura

```
contenuti/         sorgente unica: un JSON per sessione + campagna.json
genera.py          generatore: legge contenuti/ e produce sito/, build/, pdf/
sito/              sito statico (HTML/CSS vanilla, nessun build step)
  index.html         elenco delle sessioni
  sessioni/          una pagina per sessione
  css/stile.css      tema scuro invernale/fatato
pdf/               diario-dolmenwood.pdf
fonti/             materiale grezzo da cui è ricostruito il diario
  audio/             registrazioni (escluse da git, vedi .gitignore)
build/             HTML intermedio per la stampa (rigenerabile)
```

## Rigenerare tutto

```bash
python3 genera.py
```

Nessuna dipendenza Python esterna. Il PDF viene prodotto con Google Chrome in
modalità headless; se Chrome non è installato il sito viene generato lo stesso e
il PDF viene saltato. Per generare solo il sito:

```bash
python3 genera.py --no-pdf
```

## Aggiungere una sessione

1. Creare `contenuti/sessione-06.json` copiando la struttura di
   `contenuti/sessione-05.json`:

   ```jsonc
   {
     "numero": 6,
     "slug": "sessione-06",
     "titolo": "...",
     "sottotitolo": "...",
     "avventura": "Winter's Daughter",
     "durata": "...",
     "occhiello": "...",
     "capitoli": [
       { "titolo": "...", "testo": ["paragrafo", "..."],
         "dialoghi": [ { "chi": "Nome", "nota": "opzionale", "cosa": "battuta" } ] }
     ],
     "perle": [ { "titolo": "...", "testo": "..." } ],
     "note":  [ "..." ]
   }
   ```

   Nel campo `testo` e nelle battute si possono usare `**grassetto**` e
   `*corsivo*`.

2. Aggiungere il nome del file alla lista `ORDINE` in `genera.py`.
3. Rilanciare `python3 genera.py`.

L'indice, la navigazione avanti/indietro fra le sessioni e il PDF si aggiornano
da soli.

## Pubblicare su GitHub Pages

Nelle impostazioni del repository, *Pages* → *Deploy from a branch* → branch
`main`, cartella `/sito`. Il file `sito/.nojekyll` evita che GitHub tenti di
processare le pagine con Jekyll.

## Come è stato ricostruito il diario

- **Sessioni 1-4**: nessuna registrazione. Riscrittura del diario manoscritto
  tenuto al tavolo (`fonti/diario-sessioni-1-4.txt`), riordinato ma senza
  aggiungere eventi.
- **Sessione 5**: prima sessione registrata. L'audio è stato trascritto in
  locale con **Whisper large-v3** (`mlx-whisper` su Apple Silicon, lingua
  italiana). La trascrizione risultante
  (`fonti/trascrizione-whisper-sessione5.txt`) è la fonte primaria; la
  trascrizione automatica dell'iPhone
  (`fonti/trascrizione-iphone-grezza-sessione5.txt`) è stata usata solo come
  riscontro incrociato su nomi ed eventi.
- I nomi propri, le stirpi e i termini di regolamento sono stati verificati
  contro il materiale pubblico di Necrotic Gnome (reference document online) e
  le descrizioni pubbliche di *Winter's Daughter*. I dialoghi sono ripuliti e
  fedeli al senso, non trascrizioni parola per parola.
- Dove le fonti restano ambigue anche incrociandole, il testo lo dichiara
  esplicitamente con `[da verificare con il gruppo]`.

## Note su diritti e materiali

Dolmenwood e *Winter's Daughter* sono opere di Necrotic Gnome. Questo repository
contiene **solo** materiale originale: il resoconto di cosa è successo al nostro
tavolo, scritto con parole nostre. Non riproduce testo, mappe, statistiche o
illustrazioni dei manuali.

Tutta la grafica del sito e del PDF è generata: SVG scritti a mano e effetti
CSS. Nessuna immagine di terze parti.
