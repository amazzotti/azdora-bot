"""Il bot non deve perdere niente quando qualcosa va storto: Telegram che non risponde,
un giornale con un feed strano, il meteo giù alle 7, il cambio dell'ora legale."""

from datetime import datetime, time, timedelta

from azdora.archivio import ArchivioMemoria
from azdora.bot import Azdora
from azdora.config import FUSO_ORARIO, Impostazioni
from azdora.meteo.openmeteo import ErroreMeteo
from azdora.notizie import leggi_feed, recenti
from azdora.orari import Orari
from azdora.promemoria import Promemoria, RegistroPromemoria, prossima_volta
from azdora.scadenze import RegistroScadenze, leggi_scadenza
from azdora.telegram import ErroreTelegram
from tests.conftest import FintiLuoghi, FintoMessaggero, FintoMeteo, carica, messaggio

IO, BABBO = 111, 333
IMPOSTAZIONI = Impostazioni(
    token="1:x",
    chat_personale=IO,
    chat_famiglia=(BABBO,),
    segreto_webhook="s",
    segreto_cron="c",
    username_bot="b",
    nome_utente="Ale",
)


class Orologio:
    def __init__(self, ora: datetime) -> None:
        self.ora = ora

    def __call__(self) -> datetime:
        return self.ora


def crea(ora, messaggero=None, notizie=None, meteo=None):
    archivio = ArchivioMemoria(persistente=True)
    messaggero = messaggero or FintoMessaggero()
    meteo = meteo or FintoMeteo(carica("previsioni_riccione.json"), carica("mare_riccione.json"))
    bot = Azdora(
        IMPOSTAZIONI,
        messaggero,
        meteo,
        FintiLuoghi(),
        archivio=archivio,
        notizie=notizie,
        orologio=ora,
    )
    return archivio, messaggero, bot


class NotizieRotte:
    def notizie_per(self, *argomenti):
        raise TypeError("can't compare offset-naive and offset-aware datetimes")

    def del_giorno_in_riviera(self, *argomenti):
        return []

    def eventi(self, *argomenti):
        return []

    def principali(self, *argomenti):
        raise TypeError("dato inatteso")


def test_riepilogo_del_mattino_arriva_anche_se_le_notizie_si_rompono():
    ora = Orologio(datetime(2026, 11, 6, 7, 0, tzinfo=FUSO_ORARIO))
    _, messaggero, bot = crea(ora, notizie=NotizieRotte())
    assert bot.controlla_mattino() == 2
    assert {chat for chat, testo in messaggero.inviati if "📍" in testo} == {IO, BABBO}


class TelegramACapriccio(FintoMessaggero):
    """Fallisce le prime ``guasti`` volte, poi funziona."""

    def __init__(self, guasti=1, errore="sendMessage: Telegram non raggiungibile"):
        super().__init__()
        self.guasti = guasti
        self.errore = errore

    def invia_messaggio(self, chat_id, testo, tastiera=None):
        if self.guasti > 0:
            self.guasti -= 1
            raise ErroreTelegram(self.errore)
        super().invia_messaggio(chat_id, testo, tastiera)


def test_riepilogo_non_consegnato_si_riprova_al_giro_dopo():
    ora = Orologio(datetime(2026, 11, 6, 7, 0, tzinfo=FUSO_ORARIO))
    _, telegram, bot = crea(ora, messaggero=TelegramACapriccio(guasti=1))
    bot.orari.salva(BABBO, Orari(mattino=None))
    assert bot.controlla_mattino() == 0  # Telegram giù
    ora.ora += timedelta(minutes=15)
    assert bot.controlla_mattino() == 1
    assert "Buongiorno" in telegram.inviati[0][1]
    ora.ora += timedelta(minutes=15)
    assert bot.controlla_mattino() == 0  # una volta sola


class MeteoACapriccio(FintoMeteo):
    def previsioni(self, localita):
        if self.guasto:
            self.guasto = False
            raise ErroreMeteo("Open-Meteo giù")
        return super().previsioni(localita)


def test_meteo_giu_alle_7_si_riprova_invece_di_mandare_un_riepilogo_vuoto():
    ora = Orologio(datetime(2026, 11, 6, 7, 0, tzinfo=FUSO_ORARIO))
    meteo = MeteoACapriccio(carica("previsioni_riccione.json"), carica("mare_riccione.json"))
    meteo.guasto = True
    _, messaggero, bot = crea(ora, meteo=meteo)
    bot.orari.salva(BABBO, Orari(mattino=None))
    assert bot.controlla_mattino() == 0 and messaggero.inviati == []
    ora.ora += timedelta(minutes=15)
    assert bot.controlla_mattino() == 1 and "Buongiorno" in messaggero.ultimo


def test_scadenze_una_volta_sola_anche_con_orari_diversi():
    ora = Orologio(datetime(2026, 11, 6, 7, 0, tzinfo=FUSO_ORARIO))
    archivio, messaggero, bot = crea(ora)
    bot.orari.salva(BABBO, Orari(mattino=time(8, 0)))
    RegistroScadenze(archivio, IO).aggiungi(leggi_scadenza(("7/11", "bollo"), ora.ora.date()))
    bot.controlla_mattino()
    ora.ora = ora.ora.replace(hour=8)
    bot.controlla_mattino()
    bot.controlli()
    avvisi = [t for chat, t in messaggero.inviati if chat == IO and "bollo" in t.lower()]
    assert len(avvisi) == 1


def test_scadenze_arrivano_anche_col_riepilogo_spento():
    ora = Orologio(datetime(2026, 11, 6, 8, 15, tzinfo=FUSO_ORARIO))
    archivio, messaggero, bot = crea(ora)
    for chat in (IO, BABBO):
        bot.orari.salva(chat, Orari(mattino=None))
    RegistroScadenze(archivio, IO).aggiungi(leggi_scadenza(("7/11", "bollo"), ora.ora.date()))
    bot.controlli()
    assert any(chat == IO and "bollo" in t.lower() for chat, t in messaggero.inviati)


def test_promemoria_non_consegnato_non_va_perso():
    ora = Orologio(datetime(2026, 11, 6, 9, 0, tzinfo=FUSO_ORARIO))
    archivio, telegram, bot = crea(ora, messaggero=TelegramACapriccio(guasti=1))
    registro = RegistroPromemoria(archivio, IO)
    registro.aggiungi(ora.ora - timedelta(minutes=5), "uno")
    registro.aggiungi(ora.ora - timedelta(minutes=4), "due")
    assert bot.controlla_promemoria() == 1  # il primo non è partito
    ora.ora += timedelta(minutes=15)
    assert bot.controlla_promemoria() == 1  # riprovato al giro dopo
    assert sorted("uno" in t for _, t in telegram.inviati) == [False, True]
    assert registro.tutti() == []


def test_promemoria_a_chi_ha_bloccato_il_bot_non_si_ripete_all_infinito():
    ora = Orologio(datetime(2026, 11, 6, 9, 0, tzinfo=FUSO_ORARIO))
    telegram = TelegramACapriccio(guasti=5, errore="sendMessage: Forbidden: bot was blocked")
    archivio, _, bot = crea(ora, messaggero=telegram)
    RegistroPromemoria(archivio, IO).aggiungi(ora.ora - timedelta(minutes=5), "uno")
    assert bot.controlla_promemoria() == 0
    assert RegistroPromemoria(archivio, IO).tutti() == []


def test_promemoria_ricorrente_resta_alla_stessa_ora_dopo_l_ora_legale():
    ottobre = datetime(2026, 10, 20, 8, 0, tzinfo=FUSO_ORARIO)  # ora legale, +02:00
    salvato = Promemoria(1, ottobre, "pillola", "giorno").in_dizionario()
    letto = Promemoria.da_dizionario(salvato)
    quando = letto.quando
    for _ in range(10):  # si passa il 25 ottobre, fine dell'ora legale
        quando = prossima_volta(quando, "giorno")
    assert quando.astimezone(FUSO_ORARIO).hour == 8
    assert quando.astimezone(FUSO_ORARIO).date().day == 30
    settimana = prossima_volta(ottobre, "settimana")
    assert settimana.astimezone(FUSO_ORARIO).hour == 8


FEED_STRANO = b"""<?xml version="1.0" encoding="utf-8"?>
<rss><channel>
<item><title>Senza fuso</title><link>https://a/1</link>
<pubDate>Fri, 06 Nov 2026 06:00:00 -0000</pubDate></item>
<item><title>Con fuso</title><link>https://a/2</link>
<pubDate>Fri, 06 Nov 2026 07:00:00 +0100</pubDate></item>
</channel></rss>"""


def test_feed_con_date_senza_fuso_non_rompe_le_notizie():
    voci = leggi_feed(FEED_STRANO, "Prova")
    assert all(v.data is not None and v.data.tzinfo is not None for v in voci)
    adesso = datetime(2026, 11, 6, 9, 0, tzinfo=FUSO_ORARIO)
    assert len(recenti(voci, adesso)) == 2  # prima: TypeError tra date con e senza fuso


def test_feed_con_codifica_sconosciuta_viene_scartato():
    xml = b'<?xml version="1.0" encoding="klingon"?><rss><channel></channel></rss>'
    assert leggi_feed(xml, "Prova") == []


def test_comando_che_si_rompe_risponde_comunque():
    ora = Orologio(datetime(2026, 11, 6, 9, 0, tzinfo=FUSO_ORARIO))
    _, messaggero, bot = crea(ora, notizie=NotizieRotte())
    bot.gestisci_update(messaggio("/italia", chat_id=IO))
    assert "Qualcosa è andato storto" in messaggero.ultimo


def test_nome_con_caratteri_html_non_rompe_il_riepilogo():
    from azdora.riepilogo import riepilogo_oggi

    adesso = datetime(2026, 11, 6, 7, 0, tzinfo=FUSO_ORARIO)
    assert "Ale &lt;3" in riepilogo_oggi([], adesso, "Ale <3")
