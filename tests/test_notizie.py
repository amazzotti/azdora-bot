from datetime import UTC, datetime, timedelta

import httpx
import pytest
import respx

from azdora.notizie import (
    FONTI_RIVIERA,
    Fonte,
    LettoreNotizie,
    Notizia,
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


@respx.mock
def test_lettore_in_riviera_usa_giornali_locali_e_google():
    for fonte, file in zip(
        FONTI_RIVIERA,
        ["rss_riminitoday.xml", "rss_altarimini.xml", "rss_chiamamicitta.xml", "rss_carlino.xml"],
        strict=True,
    ):
        respx.get(fonte.url).mock(return_value=httpx.Response(200, content=_feed(file)))
    google = respx.get(url__startswith="https://news.google.com/").mock(
        return_value=httpx.Response(200, content=_feed("rss_googlenews.xml"))
    )
    with httpx.Client() as http:
        notizie = LettoreNotizie(http).notizie_per("Riccione", *RICCIONE, ADESSO)
    assert google.called
    fonti = {n.fonte for n in notizie}
    assert "Altarimini" in fonti  # dai giornali locali, filtrate su Riccione
    assert "Newsrimini" in fonti  # da Google News
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
