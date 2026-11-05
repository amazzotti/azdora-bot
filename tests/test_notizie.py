from datetime import UTC, datetime, timedelta

import httpx
import pytest
import respx

from azdora.notizie import (
    FONTI_RIVIERA,
    Fonte,
    LettoreNotizie,
    Notizia,
    e_in_riviera,
    e_spazzatura,
    formatta,
    leggi_feed,
    ordina,
    parla_di,
    recenti,
    senza_doppioni,
    url_google_news,
)
from tests.conftest import FIXTURES

ADESSO = datetime(2026, 11, 5, 10, 0, tzinfo=UTC)
RICCIONE = (43.9993, 12.6566)
BOLOGNA = (44.494, 11.343)


def _feed(nome: str) -> bytes:
    return (FIXTURES / nome).read_bytes()


@pytest.mark.parametrize(
    "file",
    ["rss_riminitoday.xml", "rss_altarimini.xml", "rss_chiamamicitta.xml", "rss_carlino.xml"],
)
def test_feed_reali_dei_giornali_locali(file):
    notizie = leggi_feed(_feed(file), "Giornale")
    assert len(notizie) == 4
    for n in notizie:
        assert n.titolo and "<" not in n.titolo
        assert n.link.startswith("https://")
        assert n.fonte == "Giornale"
        assert len(n.sommario) <= 220
        assert "<" not in n.sommario


def test_feed_google_news_separa_titolo_e_giornale():
    notizie = leggi_feed(_feed("rss_googlenews.xml"), "Google News")
    assert notizie[0].fonte == "Newsrimini"
    assert not notizie[0].titolo.endswith("Newsrimini")
    assert notizie[0].sommario == ""
    assert notizie[0].data == datetime(2026, 11, 5, 8, 22, tzinfo=UTC)


def test_entita_html_decodificate():
    notizie = leggi_feed(_feed("rss_riminitoday.xml"), "Rimini Today")
    assert "nell'hotel dell'ex" in notizie[0].titolo


def test_feed_rotto_o_malevolo():
    assert leggi_feed(b"<rss><channel><item>", "x") == []
    bomba = b"""<?xml version="1.0"?>
<!DOCTYPE lol [<!ENTITY lol "lol"><!ENTITY lol2 "&lol;&lol;&lol;">]>
<rss><channel><item><title>&lol2;</title><link>https://a</link></item></channel></rss>"""
    assert leggi_feed(bomba, "x") == []  # defusedxml rifiuta le entità


def test_voci_senza_titolo_o_link_scartate():
    xml = b"""<rss><channel>
      <item><title>Solo titolo</title></item>
      <item><title>Link strano</title><link>javascript:alert(1)</link></item>
      <item><title>Buona</title><link>https://esempio.it/a</link>
        <pubDate>non una data</pubDate></item>
    </channel></rss>"""
    notizie = leggi_feed(xml, "x")
    assert [n.titolo for n in notizie] == ["Buona"]
    assert notizie[0].data is None


def test_parla_di_parola_intera():
    n = Notizia("Riccione celebra San Martino", "https://a", "x")
    assert parla_di(n, "Riccione")
    assert parla_di(n, "riccione")
    assert not parla_di(n, "Rimini")
    assert not parla_di(Notizia("Riccionese doc", "https://a", "x"), "Riccione")
    assert parla_di(
        Notizia("Titolo", "https://a", "x", sommario="a Misano Adriatico"), "Misano Adriatico"
    )


def test_recenti_doppioni_e_ordine():
    vecchia = Notizia("Vecchia", "https://a", "x", ADESSO - timedelta(days=3))
    nuova = Notizia("Nuova", "https://b", "x", ADESSO - timedelta(hours=1))
    nuovissima = Notizia("Nuovissima", "https://c", "x", ADESSO)
    senza_data = Notizia("Senza data", "https://d", "x")
    doppione = Notizia("  NUOVA ", "https://e", "y", ADESSO)
    assert recenti([vecchia, nuova, senza_data], ADESSO) == [nuova, senza_data]
    assert senza_doppioni([nuova, doppione]) == [nuova]
    assert ordina([senza_data, nuova, nuovissima]) == [nuovissima, nuova, senza_data]


def test_spazzatura():
    assert e_spazzatura(Notizia("Pronostico Parma-Bologna", "https://a", "x"))
    assert e_spazzatura(Notizia("Titolo", "https://a", "Siti scommesse"))
    assert not e_spazzatura(Notizia("Sagra della piadina", "https://a", "Rimini Today"))
    assert e_spazzatura(Notizia("Ultim'ora Cesena", "https://a", "Cesena Today"))
    assert e_spazzatura(Notizia("Notizie su riqualificazione urbana", "https://a", "x"))
    assert not e_spazzatura(Notizia("Rimini, doppio esame al Neri", "https://a", "Carlino"))


def test_formatta_con_link_sicuri():
    n = Notizia('Titolo <b>"strano"</b>', 'https://a.it/?q="x"&y=1', "Fonte & C.", ADESSO)
    testo = formatta([n], "<b>Notizie</b>")
    assert '<a href="https://a.it/?q=&quot;x&quot;&amp;y=1">' in testo
    assert "Titolo &lt;b&gt;&quot;strano&quot;&lt;/b&gt;" in testo
    assert "Fonte &amp; C. · 11:00" in testo  # ora di Roma


def test_url_google_news():
    url = url_google_news("Misano Adriatico")
    assert "q=%22Misano+Adriatico%22+when%3A1d" in url
    assert "hl=it" in url


FILE_PER_FONTE = {
    "il Resto del Carlino Cesena": "rss_carlino_cesena.xml",
    "Cesena Notizie": "rss_cesenanotizie.xml",
    "Altarimini": "rss_altarimini.xml",
    "Chiamami Città": "rss_chiamamicitta.xml",
    "il Resto del Carlino Rimini": "rss_carlino.xml",
    "il Resto del Carlino Pesaro": "rss_carlino_pesaro.xml",
    "Pesaro Notizie": "rss_pesaronotizie.xml",
    "Corriere Adriatico Pesaro": "rss_corriereadriatico_pesaro.xml",
}


def _finti_giornali():
    """Ogni giornale risponde con il suo feed salvato; quelli via Google con Google News."""
    for fonte in FONTI_RIVIERA:
        file = FILE_PER_FONTE.get(fonte.nome, "rss_googlenews.xml")
        respx.get(fonte.url).mock(return_value=httpx.Response(200, content=_feed(file)))
    return respx.get(url__startswith="https://news.google.com/rss/search?q=%22").mock(
        return_value=httpx.Response(200, content=_feed("rss_googlenews.xml"))
    )


def test_fonti_da_cesena_a_pesaro():
    from azdora.notizie import AREA_DELLA_FONTE

    assert set(AREA_DELLA_FONTE.values()) == {"Cesena", "Rimini", "Pesaro"}
    assert len({f.url for f in FONTI_RIVIERA}) == len(FONTI_RIVIERA)
    # I giornali che rifiutano i programmi automatici passano da Google News.
    bloccati = {"Rimini Today", "Cesena Today", "NewsRimini"}
    for fonte in FONTI_RIVIERA:
        assert fonte.url.startswith("https://news.google.com/") == (fonte.nome in bloccati)


@pytest.mark.parametrize(
    ("lat", "lon", "dentro"),
    [
        (44.139, 12.243, True),  # Cesena
        (44.200, 12.400, True),  # Cesenatico
        (43.999, 12.657, True),  # Riccione
        (43.910, 12.913, True),  # Pesaro
        (43.840, 13.020, True),  # Fano
        (44.494, 11.343, False),  # Bologna
        (43.616, 13.519, False),  # Ancona
    ],
)
def test_in_zona(lat, lon, dentro):
    from azdora.notizie import in_zona

    assert in_zona(lat, lon) is dentro


@respx.mock
def test_lettore_in_zona_usa_giornali_locali_e_google():
    google = _finti_giornali()
    with httpx.Client() as http:
        notizie = LettoreNotizie(http).notizie_per("Riccione", *RICCIONE, ADESSO)
    assert google.called
    fonti = {n.fonte for n in notizie}
    assert "Altarimini" in fonti  # dai giornali locali, filtrate su Riccione
    # Da Google News (ricerca per comune o giornali che passano da Google); i doppioni
    # tra le due strade vengono uniti, quindi basta che ce ne sia almeno uno.
    assert fonti & {"Newsrimini", "Cesena Today", "Rimini Today", "NewsRimini"}
    assert all(
        parla_di(n, "Riccione") for n in notizie if n.fonte in {f.nome for f in FONTI_RIVIERA}
    )
    date = [n.data for n in notizie if n.data]
    assert date == sorted(date, reverse=True)


@respx.mock
def test_lettore_fuori_riviera_usa_solo_google():
    locale = respx.get(FONTI_RIVIERA[0].url)
    respx.get(url__startswith="https://news.google.com/").mock(
        return_value=httpx.Response(200, content=_feed("rss_googlenews.xml"))
    )
    with httpx.Client() as http:
        notizie = LettoreNotizie(http).notizie_per("Bologna", *BOLOGNA, ADESSO)
    assert not locale.called
    assert notizie


@respx.mock
def test_un_giornale_giu_non_ferma_gli_altri():
    fonti = [Fonte("Giù", "https://giu.example/rss"), Fonte("Su", "https://su.example/rss")]
    respx.get("https://giu.example/rss").mock(side_effect=httpx.ConnectError("giù"))
    respx.get("https://su.example/rss").mock(
        return_value=httpx.Response(200, content=_feed("rss_altarimini.xml"))
    )
    respx.get(url__startswith="https://news.google.com/").mock(return_value=httpx.Response(503))
    with httpx.Client() as http:
        notizie = LettoreNotizie(http, fonti).notizie_per("Riccione", *RICCIONE, ADESSO)
    assert notizie and all(n.fonte == "Su" for n in notizie)


@respx.mock
def test_eventi_dalla_riviera():
    from azdora.notizie import URL_ALTARIMINI, URL_EVENTI_GOOGLE

    respx.get(URL_ALTARIMINI).mock(
        return_value=httpx.Response(200, content=_feed("rss_altarimini.xml"))
    )
    respx.get(URL_EVENTI_GOOGLE).mock(
        return_value=httpx.Response(200, content=_feed("rss_googlenews.xml"))
    )
    with httpx.Client() as http:
        eventi = LettoreNotizie(http).eventi(ADESSO)
    altarimini = [n for n in eventi if n.fonte == "Altarimini"]
    assert altarimini and all("Eventi" in n.categorie for n in altarimini)
    assert all(e_in_riviera(n) for n in eventi)


def test_categorie_lette_dal_feed():
    notizie = leggi_feed(_feed("rss_altarimini.xml"), "Altarimini")
    assert notizie[0].categorie == ("Riccione", "Eventi")
    assert parla_di(
        Notizia("Titolo", "https://a", "x", categorie=("Misano Adriatico",)), "misano adriatico"
    )


def test_del_giorno_alternate_e_prima_quelle_del_comune():
    from datetime import timedelta as td

    from azdora.notizie import alternate, del_giorno, prima_quelle_di

    ieri = ADESSO - td(days=1, hours=2)
    carlino = [
        Notizia(f"Carlino {i}", f"https://c/{i}", "Carlino", ADESSO - td(minutes=i))
        for i in range(8)
    ]
    alta = [
        Notizia(f"Alta {i}", f"https://a/{i}", "Altarimini", ADESSO - td(minutes=30 + i))
        for i in range(3)
    ]
    vecchia = Notizia("Vecchia", "https://v", "Carlino", ieri)
    oggi = del_giorno([*carlino, *alta, vecchia], ADESSO)
    assert vecchia not in oggi and len(oggi) == 11
    scelte = alternate(oggi, 6)
    assert {n.fonte for n in scelte} == {"Carlino", "Altarimini"}
    assert sum(n.fonte == "Altarimini" for n in scelte) == 3
    riccione = Notizia("Festa a Riccione", "https://r", "Carlino", ADESSO - td(hours=3))
    ordinate = prima_quelle_di([*scelte, riccione], "Riccione")
    assert ordinate[0] == riccione


def test_del_giorno_con_poche_notizie_usa_le_ultime_24_ore():
    from datetime import timedelta as td

    from azdora.notizie import del_giorno

    mezzanotte = ADESSO.replace(hour=0, minute=30)  # 01:30 a Roma: oggi c'è poco
    ieri_sera = [
        Notizia(f"Sera {i}", f"https://s/{i}", "x", mezzanotte - td(hours=i + 1)) for i in range(6)
    ]
    assert len(del_giorno(ieri_sera, mezzanotte)) == 6


@respx.mock
def test_giornale_via_google_prende_il_suo_nome():
    from azdora.notizie import FONTI_RIVIERA

    rimini_today = next(f for f in FONTI_RIVIERA if f.nome == "Rimini Today")
    respx.get(rimini_today.url).mock(
        return_value=httpx.Response(200, content=_feed("rss_googlenews.xml"))
    )
    with httpx.Client() as http:
        notizie = LettoreNotizie(http, [rimini_today])._scarica(rimini_today)
    assert notizie and all(n.fonte == "Rimini Today" for n in notizie)


@respx.mock
def test_del_giorno_divide_tra_cesena_rimini_e_pesaro():
    from azdora.notizie import AREA_DELLA_FONTE

    _finti_giornali()
    with httpx.Client() as http:
        notizie = LettoreNotizie(http).del_giorno_in_riviera("Riccione", ADESSO)
    assert 5 <= len(notizie) <= 10
    assert parla_di(notizie[0], "Riccione")
    sul_comune = [n for n in notizie if parla_di(n, "Riccione")]
    assert len(sul_comune) <= 4
    aree = {AREA_DELLA_FONTE.get(n.fonte) for n in notizie if not parla_di(n, "Riccione")}
    assert {"Cesena", "Pesaro"} <= aree
