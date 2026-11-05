# 🏖️ Azdora

[![CI](https://github.com/amazzotti/azdora-bot/actions/workflows/ci.yml/badge.svg)](https://github.com/amazzotti/azdora-bot/actions/workflows/ci.yml)
![Python 3.12](https://img.shields.io/badge/python-3.12-blue)
![Costo: 0 €](https://img.shields.io/badge/costo-0%20%E2%82%AC-brightgreen)
[![Licenza MIT](https://img.shields.io/badge/licenza-MIT-lightgrey)](LICENSE)

**Bot Telegram personale per chi vive tra Riccione e Rimini.**
In Romagna l'*azdora* è chi manda avanti la casa: sa che tempo fa, si ricorda le scadenze e dice a tutti cosa c'è da fare. Questo bot fa lo stesso, su Telegram.

> 🇬🇧 English summary at the [bottom of the page](#-english-summary).

---

## ✨ Cosa sa fare

| Comando | Cosa risponde |
|---|---|
| `/oggi` | Riepilogo di oggi per Riccione e Rimini: meteo, vento, mare, alba e tramonto, consigli pratici |
| `/domani` | Lo stesso, per domani |
| `/meteo [località]` | Condizioni attuali e previsioni dei prossimi 3 giorni, per **qualsiasi comune** (`/meteo cesenatico`) |
| `/vento [località]` | Vento adesso, ogni 3 ore e giorno per giorno, con i nomi dei venti dell'Adriatico (**garbino**, bora, scirocco, maestrale…), nodi e scala Beaufort |
| `/mare [località]` | Altezza, direzione e periodo delle onde, stato del mare (scala Douglas), temperatura dell'acqua. Nell'entroterra ti dice che il mare è lontano |
| `/qui` | Mostra il pulsante **📍 Manda la mia posizione** |
| 📍 *posizione* | Se mandi la tua posizione (anche con 📎 → Posizione), ti risponde con meteo, vento, mare e consigli **per il punto in cui sei** |
| `/id` | L'id della chat, da usare nella configurazione |
| `/aiuto` | L'elenco dei comandi |

Senza località i comandi usano Riccione.

Inoltre, **ogni mattina alle 7** l'Azdora manda il riepilogo della giornata. Ecco un esempio vero:

```text
☀️ Buongiorno Ale!
Oggi è giovedì 5 novembre.

📍 Riccione
🌦️ Rovesci deboli · 10°/15° · pioggia 28% (0.2 mm)
💨 Adesso: Maestrale da NO ↘ 10 km/h (5 nodi), raffiche 21 km/h · forza 2, brezza leggera
🌬️ In giornata: Maestrale da NO ↘, fino a 10 km/h, raffiche 21 km/h
🌊 Mare: onde fino a 0.4 m da NE (poco mosso) · acqua 17°C

📍 Rimini
...

🌅 Alba 06:50 · 🌇 Tramonto 16:54 · UV max 2

Consigli
🌂 Possibile qualche goccia (28%): ombrello in macchina.
```

I consigli vengono da regole semplici e leggibili (soglie in [`azdora/riepilogo.py`](azdora/riepilogo.py)). Il bot avvisa di ombrello, temporali, nebbia in A14, raffiche forti, gelo, caldo, UV alto, mare mosso e… *giornata da spiaggia*.

## 🧭 Come funziona

```mermaid
flowchart LR
    U([📱 Telegram]) -- "webhook: ogni messaggio" --> W["/api/webhook<br/>(Vercel)"]
    C([⏰ cron-job.org<br/>ogni giorno alle 7]) -- "Authorization: Bearer" --> R["/api/cron?job=mattino<br/>(Vercel)"]
    W --> B{{"Azdora<br/>azdora/bot.py"}}
    R --> B
    B -- "previsioni + mare" --> M[(Open-Meteo<br/>gratis, senza chiave)]
    B -- "nome ↔ coordinate" --> L[(Open-Meteo Geocoding<br/>+ Nominatim OSM)]
    B -- "sendMessage" --> U
```

1. **Scrivi un comando** al bot. Telegram lo inoltra subito al **webhook** su Vercel. La funzione [`api/webhook.py`](api/webhook.py) controlla l'header segreto che Telegram rimanda a ogni chiamata e passa il messaggio al bot.
2. **Il bot** ([`azdora/bot.py`](azdora/bot.py)) controlla che la chat sia autorizzata, capisce il comando e scarica i dati da **Open-Meteo**: previsioni dall'API *forecast*, onde e temperatura dell'acqua dall'API *marine*.
3. **I numeri diventano parole**: i gradi diventano punti cardinali e nomi dei venti ([`vento.py`](azdora/meteo/vento.py)), le altezze d'onda diventano lo stato del mare ([`mare.py`](azdora/meteo/mare.py)) e i codici WMO diventano emoji ([`codici_wmo.py`](azdora/meteo/codici_wmo.py)). Il messaggio è composto in HTML di Telegram ([`messaggi.py`](azdora/messaggi.py)).
4. **Posizione e località.** Se mandi la posizione, il bot cerca il nome del comune su Nominatim di OpenStreetMap ("Misano Adriatico (RN)") e calcola il meteo per quel punto esatto. Per `/meteo cesenatico` usa invece la ricerca per nome di Open-Meteo. Il mare compare solo se il punto marino di Open-Meteo ha dati ed è entro 30 km ([`luoghi.py`](azdora/luoghi.py)).
5. **Alle 7**, [cron-job.org](https://cron-job.org) chiama [`api/cron.py`](api/cron.py) con un secondo segreto e il bot manda il riepilogo alla tua chat.

### Scelte di progetto

- **Niente framework per bot.** La Bot API viene chiamata direttamente con `httpx` ([`telegram.py`](azdora/telegram.py)): le dipendenze sono 2 e il codice gira uguale su Vercel, su GitHub Actions e in locale.
- **Logica separata dall'infrastruttura.** `bot.py` non sa nulla di HTTP. Riceve dall'esterno *chi spedisce i messaggi* e *da dove arriva il meteo*, quindi nei test si usano dei finti e nel simulatore la console.
- **Costo zero:**

  | Pezzo | Servizio | Piano |
  |---|---|---|
  | Comandi (webhook) | Vercel | Hobby, gratuito |
  | Orari | cron-job.org | gratuito |
  | Meteo e mare | Open-Meteo | gratuito per uso non commerciale, senza chiave |
  | CI | GitHub Actions | gratuito |

## 🔒 Privacy e sicurezza

- **Nessun segreto nel codice.** Token e chiavi vivono solo nelle variabili d'ambiente di Vercel e nei GitHub Secrets. Il file `.env` è escluso da git.
- **Webhook protetto**: le richieste senza l'header `X-Telegram-Bot-Api-Secret-Token` giusto ricevono `401`. Il confronto è a tempo costante.
- **Cron protetto**: serve `Authorization: Bearer <CRON_SECRET>`.
- **Il bot risponde solo a due chat**: la tua chat privata e, in futuro, il gruppo di coppia. Agli sconosciuti dice solo il loro `/id`. Se un estraneo lo aggiunge a un gruppo, il bot esce da solo.
- **Posizione**: il bot non la salva da nessuna parte. Le coordinate servono solo a chiedere meteo e nome del luogo a Open-Meteo e Nominatim, nel momento in cui la mandi.
- **Nei log non finisce il token**: gli errori di rete vengono riformulati senza l'URL, che lo contiene.

## 🚀 Messa in funzione

Bastano una ventina di minuti, una volta sola.

### 1. Crea il bot su Telegram
1. Scrivi a [@BotFather](https://t.me/BotFather) e manda `/newbot`.
2. Come nome scegli `Azdora`, come username ad esempio `azdora_riccione_bot`.
3. Copia il **token**. ⚠️ È una password: non va mai nel codice. Se ti scappa, rigeneralo con `/revoke`.

### 2. Genera due segreti casuali
```bash
py -c "import secrets; print(secrets.token_urlsafe(32))"
```
Lancialo due volte: un segreto serve per `TELEGRAM_WEBHOOK_SECRET`, l'altro per `CRON_SECRET`.

### 3. Pubblica su Vercel
1. Su [vercel.com](https://vercel.com) entra con GitHub (piano **Hobby**) e scegli **Add New → Project → `azdora-bot`**.
2. Lascia **Framework Preset: Other**.
3. In **Environment Variables** aggiungi:

   | Variabile | Valore |
   |---|---|
   | `TELEGRAM_BOT_TOKEN` | il token di BotFather |
   | `TELEGRAM_BOT_USERNAME` | lo username del bot, senza `@` |
   | `TELEGRAM_WEBHOOK_SECRET` | il primo segreto |
   | `CRON_SECRET` | il secondo segreto |
   | `AZDORA_NOME` | come vuoi essere salutato, es. `Ale` |

4. Clicca **Deploy**. Per controllare, apri `https://<progetto>.vercel.app/api/webhook`: deve rispondere `{"ok": true, ...}`.

### 4. Collega Telegram a Vercel
1. Su GitHub vai in **Settings → Secrets and variables → Actions** e aggiungi i secret `TELEGRAM_BOT_TOKEN` e `TELEGRAM_WEBHOOK_SECRET`.
2. In **Actions → Configura bot → Run workflow** inserisci l'indirizzo Vercel.
3. Scrivi `/id` al bot e lui ti risponde con il tuo id.
4. Aggiungi `TELEGRAM_CHAT_ID` con quel numero su Vercel; poi in **Deployments** scegli **Redeploy**, perché le variabili nuove valgono solo dal deploy successivo.
5. Aggiungi lo stesso `TELEGRAM_CHAT_ID` anche nei GitHub Secrets.
6. Prova `/oggi`. 🎉

### 5. Riepilogo delle 7 con cron-job.org
1. Crea un account gratuito su [cron-job.org](https://cron-job.org) e imposta il fuso **Europe/Rome**.
2. Scegli **Create cronjob**:
   - **URL**: `https://<progetto>.vercel.app/api/cron?job=mattino`
   - **Orario**: ogni giorno alle 07:00
   - **Advanced → Headers**: `Authorization` = `Bearer <CRON_SECRET>`
3. Lancia **Test run** e il riepilogo ti arriva su Telegram.

Per provare il riepilogo senza cron-job.org c'è anche il workflow **Actions → Prova riepilogo**.

## 🛠️ Sviluppo in locale

```bash
py -3.12 -m venv .venv
.venv\Scripts\activate          # su Linux/macOS: source .venv/bin/activate
pip install -e ".[dev]"
```

```bash
pytest --cov       # test, con copertura
ruff check .       # lint
ruff format .      # formattazione
```

### Simulatore
Puoi provare il bot dal terminale **senza Telegram e senza token**, con il meteo vero:

```bash
python -m azdora.simula /oggi "/vento rimini" "/meteo cesenatico" /mare
python -m azdora.simula posizione:43.978,12.697   # come se mandassi la posizione
python -m azdora.simula mattino              # il messaggio delle 7
python -m azdora.simula --offline /domani    # con i dati salvati, senza internet
```

### Struttura

```text
azdora-bot/
├─ api/                    # funzioni serverless di Vercel
│  ├─ webhook.py           #   POST /api/webhook  ← Telegram
│  └─ cron.py              #   GET  /api/cron     ← cron-job.org
├─ azdora/
│  ├─ bot.py               # comandi, permessi, riepiloghi
│  ├─ messaggi.py          # testi in HTML di Telegram
│  ├─ riepilogo.py         # riepilogo giornaliero e consigli
│  ├─ telegram.py          # client minimale della Bot API
│  ├─ web.py               # logica degli endpoint (segreti, JSON, errori)
│  ├─ vercel.py            # adattatore HTTP per Vercel
│  ├─ config.py            # località e variabili d'ambiente
│  ├─ luoghi.py            # nome ↔ coordinate, distanze
│  ├─ app.py               # monta i pezzi reali
│  ├─ simula.py            # simulatore da terminale
│  ├─ configura.py         # imposta webhook e menu comandi
│  ├─ lavori.py            # lavori da riga di comando (es. mattino)
│  └─ meteo/
│     ├─ openmeteo.py      # client e modello dati di Open-Meteo
│     ├─ vento.py          # rosa dei venti, nodi, Beaufort
│     ├─ mare.py           # scala Douglas
│     └─ codici_wmo.py     # codici meteo → testo ed emoji
├─ tests/                  # pytest + respx, con risposte vere di Open-Meteo in fixtures/
└─ .github/workflows/      # CI, "Configura bot", "Prova riepilogo"
```

## 🗺️ Roadmap

- [x] **Tappa 1: meteo, vento e mare.** Comandi, riepilogo delle 7, test, CI.
- [x] **Meteo dove sei.** Posizione condivisa, `/qui`, qualsiasi comune per nome.
- [ ] **Tappa 2: notizie e allerte intorno a me.**
  - Il bot ricorda l'ultima posizione che gli hai mandato, anche la posizione in tempo reale di Telegram.
  - Con quella posizione costruisce il riepilogo delle 7, ti manda il link appena esce una notizia nuova sul comune in cui ti trovi (feed RSS dei giornali locali e Google News) e ti avvisa delle allerte meteo della tua zona (Allerta Meteo Emilia-Romagna, Meteoalarm fuori regione).
  - Serve un piccolo database gratuito per la posizione e le notizie già mandate.
- [ ] **Tappa 3: scadenze.** Bollo, assicurazione, revisione, bollette, con promemoria a 30, 7 e 1 giorno e il pulsante "✅ Pagato".
- [ ] **Tappa 4: spese mensili.** `/spesa 45 benzina`, riepilogo per categoria e resoconto il 1° del mese.
- [ ] **Tappa 5: chat di coppia.** Lista condivisa delle cose da fare, idee per il weekend: giri della domenica, sagre ed eventi in Romagna, con il meteo.
- [ ] **Extra**: carburante più economico in zona (open data MIMIT), UV e pollini, treni Riccione–Bologna, raccolta differenziata.

## 🙏 Fonti dei dati

- Meteo, mare e ricerca delle località: [Open-Meteo.com](https://open-meteo.com/), licenza [CC BY 4.0](https://creativecommons.org/licenses/by/4.0/).
- Nome del luogo dalla posizione: [Nominatim](https://nominatim.org/), dati © [OpenStreetMap](https://www.openstreetmap.org/copyright) contributors, licenza ODbL.
- I dati del mare si riferiscono a un punto al largo: sono indicativi per la costa.

---

## 🇬🇧 English summary

**Azdora** is a personal Telegram bot for people living in Riccione and Rimini, on Italy's Adriatic coast. *Azdora* is the Romagna dialect word for the person who runs the household.

- **Features (stage 1):**
  - current weather and 3-day forecasts;
  - wind with local Adriatic wind names, knots and Beaufort scale;
  - sea state (waves on the Douglas scale, water temperature);
  - a daily 7 AM briefing with practical tips;
  - weather wherever you are: share your Telegram location, or ask for any town by name.
- **Stack:**
  - Python 3.12 and `httpx`, calling the Telegram Bot API directly (no bot framework);
  - Telegram webhook served by Vercel serverless functions;
  - schedules from cron-job.org;
  - data from the free, keyless Open-Meteo API.
- **Zero cost** on free tiers. Secrets live only in environment variables, never in the code.
- **Quality:**
  - business logic is decoupled from infrastructure through small protocols;
  - 200+ pytest tests, using real recorded API responses and respx HTTP mocks, including an end-to-end test of the Vercel handlers;
  - ruff lint and format checks;
  - GitHub Actions CI.
- **Roadmap:**
  - location-aware local news and weather alerts;
  - bill and car-tax reminders;
  - monthly expenses;
  - a shared to-do list and weekend ideas for couples.

Licensed under the [MIT License](LICENSE).
