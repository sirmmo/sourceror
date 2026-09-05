# Sourceror

🇬🇧 *[English version of this document](README.md) — it is the more complete one,
and covers the container image, the HTTP API and the test suite.*

Aiuto per gli editor di Wikipedia: **controlla le fonti già citate** in una voce
e **cerca fonti** per le affermazioni che non ne hanno.

Non usa euristiche inventate: la valutazione di affidabilità viene dalle liste
che la comunità di Wikipedia mantiene davvero, e i controlli bibliografici da
Crossref e OpenAlex.

## Cosa fa

### 1. Verifica le citazioni esistenti

Ogni `<ref>` della voce viene estratta, classificata e valutata:

| Controllo | Fonte del dato |
|---|---|
| Fonte deprecata, inaffidabile, senza consenso, blacklistata | [WP:RSP](https://en.wikipedia.org/wiki/Wikipedia:Reliable_sources/Perennial_sources) — 493 fonti discusse dalla comunità |
| Editore predatorio, rivista dirottata, DOI resolver falso, preprint | [User:Headbomb/unreliable](https://en.wikipedia.org/wiki/User:Headbomb/unreliable) (CITEWATCH), per dominio **e** per prefisso DOI |
| **Articolo ritrattato** | Crossref (`update-to`) + OpenAlex (`is_retracted`) |
| DOI inesistente o inventato | Crossref + OpenAlex |
| Peer review, DOAJ, open access, citazioni ricevute | OpenAlex |
| Link morto, soft-404, redirect sospetto | richiesta HTTP diretta |
| Copia archiviata disponibile | Wayback Machine |
| Auto-citazione del soggetto della voce | Wikidata `P856` (sito ufficiale) |
| Riferimento circolare a Wikipedia | tabella interna |
| Piattaforma autopubblicata, contenuto generato dagli utenti, social, comunicato stampa | tabella interna |
| Citazione incompleta (manca titolo, data, autore, editore) | parsing del template |
| URL nudo, ref vuota, nota senza opera identificabile | parsing del wikitext |

Ogni citazione riceve un punteggio 0–100 e un giudizio
(**solida / accettabile / debole / inaffidabile**). La severità del problema
peggiore fa da tetto al punteggio: una citazione perfettamente formattata a una
fonte deprecata resta inaffidabile.

Il parser distingue casi che altrimenti darebbero falsi allarmi:

- `{{DBI}}`, `{{Treccani}}`, `{{Cite ODNB}}`… → citazioni valide, non "testo libero";
- note esplicative (commento dell'autore) → non valutate come fonti;
- citazioni brevi (`Lorenzetti, p. 42`) → verificate contro la Bibliografia;
- fonti cartacee scritte in prosa → segnalate come problema di *formato*, non di
  verificabilità (le fonti offline sono legittime, [WP:SOURCEACCESS](https://en.wikipedia.org/wiki/Wikipedia:Verifiability#Access_to_sources)).

### 2. Trova fonti mancanti

Individua i punti scoperti — template `{{senza fonte}}` / `{{citation needed}}`
e paragrafi lunghi senza note — e cerca fonti su **OpenAlex**, **Crossref** e
**OpenLibrary**, ordinate per pertinenza e qualità, con il template di citazione
già compilato e pronto da incollare (`{{cita pubblicazione}}` su it.wiki,
`{{cite journal}}` su en.wiki).

### 3. Strumenti

- **URL → citazione**: usa **Citoid**, il server Zotero di Wikimedia, per
  estrarre i metadati e generare il template compilato.
- **Controlla un dominio**: questa testata è utilizzabile come fonte?

## Avvio

Immagine già pubblicata, senza compilare nulla:

```bash
docker run -d --name sourceror -p 127.0.0.1:5010:5000 \
  -v sourceror-data:/app/data \
  ghcr.io/sirmmo/sourceror:latest
```

Oppure dai sorgenti:

```bash
docker compose up -d --build
# poi apri http://127.0.0.1:5010
```

Cambiare porta: `PORT=8080 docker compose up -d`.

> ⚠️ **Non esporre Sourceror in rete senza autenticazione davanti.** Non ne ha,
> e `POST /api/llm` permette a chiunque di cambiare `llm_base_url`: una
> successiva `POST /api/llm/test` invierebbe l'header
> `Authorization: Bearer <chiave>` all'endpoint scelto dall'attaccante. Per
> questo compose ascolta solo su `127.0.0.1`; usa `BIND=0.0.0.0` solo dietro un
> reverse proxy autenticato.
Lingua predefinita: `SOURCEROR_LANG=en docker compose up -d`.

Al primo avvio scarica le liste di affidabilità da Wikipedia (qualche secondo) e
le tiene in cache 24 ore nel volume `sourceror-data`.

## LLM (opzionale)

**Sourceror funziona senza LLM.** Se configurato, viene usato solo nei due punti
in cui le API non bastano:

1. **riformulare** un'affermazione in query di ricerca accademiche sensate;
2. **giudicare la pertinenza**: distinguere un lavoro *sul* soggetto da uno che
   ne usa solo il nome. Il filtro lessicale non può sapere che *"Cancer Stem
   Cells: the Loch Ness Monster of tumour biology"* non parla del lago di Ness.

Si configura da **Strumenti → LLM** nell'interfaccia, oppure da variabili
d'ambiente. Endpoint compatibile OpenAI: OpenRouter (predefinito), OpenAI,
LiteLLM, vLLM o llama.cpp in locale.

```bash
LLM_BASE_URL=https://openrouter.ai/api/v1   # o OPENAI_BASE_URL
LLM_API_KEY=sk-or-v1-…                      # o OPENROUTER_API_KEY / OPENAI_API_KEY
LLM_MODEL=openai/gpt-4o-mini                # o OPENROUTER_MODEL / OPENAI_MODEL
```

Le impostazioni salvate dall'interfaccia finiscono in `data/config.json`
(modo `0600`, contiene la chiave) e hanno la precedenza sull'ambiente.
Ogni chiamata degrada silenziosamente al comportamento senza LLM in caso di errore.

> **Scelta del modello — conta molto per la velocità.** I modelli *reasoning*
> spendono il budget di token in ragionamento nascosto *prima* di rispondere:
> con `max_tokens` basso restituiscono una risposta vuota, e ogni chiamata
> costa decine di secondi. Misurato su questa macchina con
> `deepseek-v4-flash`: giudizio di pertinenza su 12 candidati **127 s** in una
> sola richiesta, **28 s** suddividendo in lotti paralleli da 4 (è quello che
> fa Sourceror); generazione di query **~50 s** per affermazione.
> Un modello non-reasoning (es. `openai/gpt-4o-mini`) è molto più rapido a
> parità di risultato su questi compiti.
>
> Effetto end-to-end su *Loch Ness Monster* (187 note): **31,9 s** senza LLM,
> **74,6 s** con LLM — che in cambio elimina dai suggerimenti i lavori che usano
> il nome solo come metafora e fa emergere le fonti storiche reali.
>
> Il budget predefinito è 3000 token e in caso di troncamento la richiesta
> viene ripetuta con budget raddoppiato. La casella **usa LLM** nella barra in
> alto disattiva l'arricchimento per una singola analisi.

## API

| Endpoint | |
|---|---|
| `POST /api/analyze` | `{title, lang, check_links, discover_sources}` → `{job}` |
| `GET /api/job/<id>` | stato, avanzamento e risultato |
| `GET /api/search?q=&lang=` | autocompletamento dei titoli |
| `POST /api/cite` | `{url, lang}` → template compilato via Citoid |
| `POST /api/discover` | `{claim, title, lang}` → fonti candidate |
| `GET /api/domain?d=` | giudizio di affidabilità di un dominio |
| `GET\|POST /api/llm` | leggi/scrivi la configurazione LLM |
| `POST /api/llm/test` | prova la connessione |
| `GET /api/kb`, `POST /api/kb/refresh` | stato e ricarica delle liste |

Il titolo accetta anche un URL di Wikipedia, da cui estrae la voce.

## Limiti — leggere prima di fidarsi

- **Sourceror non legge le fonti.** Verifica *chi* pubblica e *se* il link
  funziona, non se il testo citato sostiene davvero l'affermazione. Il
  "citation drift" resta invisibile.
- **WP:RSP è la consuetudine di en.wikipedia**, non una regola di it.wikipedia, e
  vale *nel contesto*: una fonte "generalmente inaffidabile" può essere corretta
  per un'affermazione su sé stessa. Il tool segnala, non decide.
- **"Non in elenco" non significa affidabile**: la maggior parte delle testate,
  incluse quelle italiane, non è in nessuna delle due liste.
- **academia.edu e ResearchGate non sono interrogabili**: bloccano le richieste
  automatiche (HTTP 403) e non hanno API pubbliche. Sono invece trattati come
  *segnale*: una citazione che punta lì è un repository autopubblicato, e
  Sourceror cerca la versione peer-reviewed dello stesso lavoro su OpenAlex e
  propone di citare quella.
- Il controllo dei link genera una richiesta HTTP per ogni URL della voce: su
  una voce con 200 note richiede circa mezzo minuto. Disattivabile.
- Un HTTP 403 in fase di verifica non è un difetto della fonte: molti siti
  bloccano i bot. Viene segnalato come "da controllare a mano".

## Struttura

```
app.py                  route Flask, job in background
sourceror/
  wikitext.py           parser di wikitext: ref, template, sezioni, {{senza fonte}}
  reliability.py        WP:RSP + CITEWATCH + tabelle interne, con cache su disco
  apis.py               MediaWiki, Wikidata, Citoid, Crossref, OpenAlex, OpenLibrary, Wayback
  audit.py              motore di valutazione: flag, punteggio, giudizio
  discover.py           ricerca fonti, filtro di pertinenza, ranking
  cite.py               generazione template nella lingua del wiki
  analyze.py            orchestrazione
  llm.py, config.py     livello LLM opzionale
templates/index.html    interfaccia (vanilla JS, nessuna build)
```
