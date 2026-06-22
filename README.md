# Bot Trovaunposto → notifiche Telegram

Controlla automaticamente **trovaunposto.it** ogni 5 minuti e ti manda una
**notifica su Telegram** quando compare un nuovo biglietto treno che rispetta i
tuoi criteri (tratta, giorno, fascia oraria, prezzo massimo).

Gira gratis sul cloud di **GitHub Actions**: funziona 24 ore su 24 **anche con il
tuo PC spento**. Le ricerche (tratta, giorno, orario, prezzo) si impostano
comodamente con i **comandi del bot su Telegram** — vedi la *Parte 5*.

---

## Come funziona (in breve)

1. Ogni 5 minuti GitHub esegue lo script `trovaunposto_bot.py`.
2. Lo script apre le ricerche che hai scritto in `config.json`, legge i biglietti
   in pagina e tiene solo quelli che rispettano i tuoi filtri.
3. Confronta con i biglietti già visti (file `state/seen.json`): se ne trova di
   **nuovi**, ti manda un messaggio Telegram con prezzo, orario e **link diretto**
   al biglietto.

> Nota: al **primo avvio** il bot registra i biglietti già presenti **senza**
> avvisarti (per non riempirti di messaggi). Da lì in poi ti avvisa solo sui nuovi.
> Se vuoi essere avvisato anche di quelli già presenti, metti
> `"notify_on_first_run": true` in `config.json`.

---

## Cosa ti serve (gratis)

- Un account **Telegram** (l'app sul telefono).
- Un account **GitHub** → https://github.com/signup

Tempo richiesto: ~15 minuti, una volta sola.

---

## PARTE 1 — Crea il bot Telegram

1. Su Telegram cerca **@BotFather** e premi **Avvia / Start**.
2. Scrivi `/newbot` e segui le istruzioni (dai un nome e uno username che finisca
   per `bot`, es. `trovaposti_bot`).
3. BotFather ti darà un **TOKEN**, una stringa tipo
   `123456789:AAH...xYz`. **Copialo e tienilo da parte** (è la tua password: non
   condividerlo).
4. **Importante:** apri il tuo nuovo bot (BotFather ti dà il link `t.me/...`) e
   premi **Avvia / Start**. Senza questo passaggio il bot non può scriverti.

### Trova il tuo CHAT ID

Il bot deve sapere a chi scrivere. Il modo più semplice:

- Su Telegram cerca **@userinfobot**, premi **Start**: ti risponde con il tuo
  **Id** numerico (es. `5012345678`). Quello è il tuo `CHAT ID`.

In alternativa: scrivi un messaggio qualsiasi al tuo bot, poi apri nel browser
`https://api.telegram.org/bot<IL_TUO_TOKEN>/getUpdates` e cerca `"chat":{"id":...}`.

Alla fine devi avere due valori:
- **TELEGRAM_BOT_TOKEN** = il token di BotFather
- **TELEGRAM_CHAT_ID** = il tuo id numerico

---

## PARTE 2 — Metti i file su GitHub

Questi sono i file da caricare (sono già in questa cartella):

```
trovaunposto_bot.py
config.json
requirements.txt
.gitignore
state/.gitkeep
.github/workflows/check.yml
README.md
```

### Modo consigliato: GitHub Desktop (no comandi)

1. Scarica **GitHub Desktop**: https://desktop.github.com/ e accedi col tuo account.
2. `File → Add local repository…` e scegli questa cartella
   (`...\Automazione trovaunposto\Trovaunposto`). Se chiede di creare un
   repository, conferma (`create a repository`).
3. Premi **Publish repository**. **Togli la spunta da "Keep this code private"**
   (vedi nota sotto sul perché conviene **pubblico**) e pubblica.

### Modo alternativo: solo sito web

1. Su GitHub: **New repository** → dai un nome (es. `trovaunposto-bot`) →
   scegli **Public** → **Create**.
2. **Add file → Upload files** e trascina tutti i file della cartella.
3. Il file dentro `.github/workflows/` a volte non si carica col trascinamento.
   In quel caso: **Add file → Create new file**, come nome scrivi
   `.github/workflows/check.yml` (le barre creano le cartelle), incolla dentro il
   contenuto del file `check.yml` che trovi qui, e salva.

> ### Pubblico o privato?
> Conviene **pubblico**: GitHub Actions è **gratis e illimitato** sui repository
> pubblici, mentre quelli privati hanno solo 2000 minuti/mese (non bastano per un
> controllo ogni 5 minuti). Il tuo **token Telegram resta comunque segreto**:
> va nei *Secrets* (passo dopo), che **non** sono visibili nel codice pubblico.
> Nel repository finiscono solo lo script e le tratte che cerchi — niente di
> sensibile.

---

## PARTE 3 — Inserisci i Secret (token e chat id)

Nel repository su GitHub:

1. **Settings** (impostazioni del repository) → menu a sinistra
   **Secrets and variables → Actions**.
2. **New repository secret** e crea questi due:
   - Nome: `TELEGRAM_BOT_TOKEN` — Valore: il token di BotFather
   - Nome: `TELEGRAM_CHAT_ID` — Valore: il tuo id numerico
3. Salva.

---

## PARTE 4 — Attiva e prova

1. Vai sulla scheda **Actions** del repository. Se appare un avviso, clicca
   **"I understand my workflows, enable them"**.
2. Apri il workflow **"Controllo biglietti Trovaunposto"** → **Run workflow**
   (esecuzione manuale) per fare subito una prova.
3. Apri il log dell'esecuzione: dovresti vedere quante ricerche ha fatto e quanti
   biglietti ha trovato. Da quel momento, ogni 5 minuti circa, riceverai un
   messaggio Telegram quando appare un biglietto nuovo che rispetta i criteri.

Per un test del solo Telegram (senza cercare biglietti) puoi anche eseguire lo
script localmente con `python trovaunposto_bot.py --test` (vedi più sotto).

---

## PARTE 5 — Imposta le ricerche da Telegram (comandi)

Una volta attivo, puoi gestire tutto **dalla chat del bot**, senza toccare i file.
Scrivi al bot uno di questi comandi:

| Comando | Cosa fa |
|---|---|
| `/aiuto` | Mostra l'elenco dei comandi |
| `/lista` | Elenca le ricerche attive (con numero) |
| `/aggiungi PARTENZA > ARRIVO [data] [oraInizio-oraFine] [maxPREZZO]` | Aggiunge una ricerca |
| `/rimuovi N` | Rimuove la ricerca numero N (vedi `/lista`) |
| `/pausa` | Sospende le notifiche |
| `/riprendi` | Riattiva le notifiche |
| `/stato` | Mostra stato e numero di ricerche |

Esempi (copiali e modificali):

- `/aggiungi Milano > Roma 2026-07-25 17:00-21:00 max60`
- `/aggiungi Napoli > Milano 25/12/2026`
- `/aggiungi Torino > Roma 08:00-12:00`

Regole: la **partenza** va prima di `>`, l'**arrivo** subito dopo; poi, in qualsiasi
ordine e tutti facoltativi, la **data** (`AAAA-MM-GG` oppure `GG/MM/AAAA`), la
**fascia oraria** (`HH:MM-HH:MM`) e il **prezzo massimo** (`maxNN`). Le città sono
cercate su *tutte le stazioni* (es. "Milano" = qualsiasi stazione di Milano).

> ⏱ I comandi vengono letti al controllo successivo (entro ~5 minuti) e il bot ti
> risponde in chat quando li ha elaborati. Quando aggiungi una ricerca, registra i
> biglietti già presenti senza avvisarti e poi ti notifica solo i nuovi.
>
> 🔒 Il bot esegue i comandi **solo** se arrivano dalla tua chat (quella del
> `TELEGRAM_CHAT_ID`): i messaggi di altri vengono ignorati.

---

## PARTE 6 — (Alternativa) Configura le tratte da file (`config.json`)

Puoi anche modificare a mano il file `config.json` invece di usare i comandi.
È utile per stazioni particolari: il modo **più semplice e sicuro** è copiare
l'indirizzo dal sito:

1. Vai su https://trovaunposto.it/, sezione **Treni → Acquista**.
2. Scegli **partenza** e **arrivo** dai menù (così i nomi delle stazioni sono
   quelli giusti) ed eventualmente la data, poi avvia la ricerca.
3. **Copia l'indirizzo** dalla barra del browser e incollalo nel campo
   `search_url`.

Esempio di voce:

```json
{
  "name": "Milano → Roma sera",
  "search_url": "https://trovaunposto.it/trains/searchTrainTicket?departure=MILANO%28TUTTE+LE+STAZIONI%29&departure_id=MILANO%28TUTTE+LE+STAZIONI%29&arrival=ROMA%28TUTTE+LE+STAZIONI%29&arrival_id=ROMA%28TUTTE+LE+STAZIONI%29&date=2026-07-25",
  "match_departure_contains": "MILANO",
  "match_arrival_contains": "ROMA",
  "match_date": "2026-07-25",
  "time_from": "17:00",
  "time_to": "21:00",
  "max_price": 60
}
```

### Cosa significano i campi

| Campo | Obbligatorio | A cosa serve |
|---|---|---|
| `name` | sì | Etichetta che compare nella notifica. |
| `search_url` | sì | L'indirizzo della ricerca copiato dal sito. |
| `match_departure_contains` | consigliato | Filtra la **stazione di partenza**. Es. `"MILANO"` accetta qualsiasi stazione di Milano; `"Milano Centrale"` solo quella. |
| `match_arrival_contains` | consigliato | Come sopra per l'**arrivo**. Es. `"ROMA"`. |
| `match_date` | opzionale | Avvisa solo per quel **giorno** (formato `AAAA-MM-GG`). Lascia `""` per qualsiasi giorno mostrato. |
| `time_from` / `time_to` | opzionale | **Fascia oraria** di partenza (es. `"17:00"`–`"21:00"`). Vuoto = qualsiasi ora. |
| `max_price` | opzionale | **Prezzo massimo** in €. `null` = nessun limite. |

> **Perché servono `match_departure_contains` e `match_arrival_contains`?**
> La ricerca del sito mostra anche i giorni vicini e i biglietti **andata/ritorno**:
> uno stesso annuncio può comparire perché *una delle due tratte* tocca quelle
> città. Questi due campi (più `match_date`) servono al bot per tenere solo la
> direzione e il giorno che ti interessano davvero. Tienili sempre impostati.

Puoi aggiungere quante ricerche vuoi: basta ripetere il blocco `{ ... }`
separandolo con una virgola dentro `searches`.

Dopo ogni modifica a `config.json`, **salva e ricarica su GitHub** (con GitHub
Desktop: *Commit* + *Push*; da web: modifica il file e *Commit changes*).

---

## Frequenza e limiti (da sapere)

- Il controllo è impostato ogni **5 minuti** (`*/5 * * * *`, orario UTC). GitHub
  esegue i cron "alla meglio": a volte può ritardare di qualche minuto. In pratica
  riceverai gli avvisi entro **circa 5–15 minuti** dalla pubblicazione.
- Su repository **pubblico** i minuti sono gratis e illimitati.
- GitHub **sospende** i cron se il repository resta **60 giorni senza attività**.
  Ogni volta che il bot trova un biglietto fa un piccolo salvataggio (attività),
  quindi di norma non succede; se dovesse capitare, riapri Actions e riattiva.
- È un controllo periodico, non in tempo reale: se un biglietto viene pubblicato
  **e venduto** nello stesso intervallo, potrebbe sfuggire. Per ridurre il rischio
  puoi tenere la frequenza a 5 minuti (già impostata).

---

## (Opzionale) Eseguirlo sul tuo PC Windows

Se preferisci provarlo o farlo girare in locale:

```powershell
# 1) installa Python da python.org, poi nella cartella del progetto:
pip install -r requirements.txt

# 2) imposta i due valori (in questa finestra):
setx TELEGRAM_BOT_TOKEN "123456789:AAH...xYz"
setx TELEGRAM_CHAT_ID  "5012345678"
#    chiudi e riapri il terminale dopo setx

# 3) prova:
python trovaunposto_bot.py --test       # messaggio di prova su Telegram
python trovaunposto_bot.py --dry-run     # mostra cosa troverebbe, senza inviare
python trovaunposto_bot.py               # giro reale
```

Per farlo partire da solo: **Utilità di pianificazione di Windows** → nuova
attività che esegue `python trovaunposto_bot.py` ogni 5 minuti (il PC deve essere
acceso).

---

## Risoluzione problemi

- **Non ricevo messaggi di prova.** Hai premuto **Start** sul *tuo* bot? Il
  `CHAT ID` è il numero giusto? Il token è incollato senza spazi?
- **L'Action fallisce con errore di rete o 403.** Il sito potrebbe rifiutare le
  richieste automatiche in quel momento: di solito basta riprovare. Se persiste,
  abbassa la frequenza (es. ogni 15 minuti) modificando il `cron` in
  `.github/workflows/check.yml`.
- **Ricevo biglietti della direzione/giorno sbagliati.** Imposta
  `match_departure_contains`, `match_arrival_contains` e `match_date`.
- **Non ricevo nulla pur essendoci biglietti.** Ricorda che al primo avvio il bot
  *registra senza avvisare*. Per un controllo immediato usa `--dry-run` in locale,
  oppure metti temporaneamente `"notify_on_first_run": true`.

---

## Nota

Il bot legge solo pagine pubbliche del sito, a un ritmo moderato, per uso
personale. Usa una frequenza ragionevole e rispetta i Termini del sito.
L'acquisto va sempre completato a mano da te sul sito (il bot non compra né paga).
