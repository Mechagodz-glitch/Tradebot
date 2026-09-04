from tradebot.models import Market
from tradebot.news import parse_rss
from tradebot.themes import theme_report

RSS = """<?xml version="1.0"?><rss version="2.0"><channel><title>Feed</title>
<item><title>Adani Ports to open empty container yard &amp; more</title><link>https://x/1</link><pubDate>Fri, 04 Sep 2026 10:00:00 GMT</pubDate><source url="https://a">Mint</source></item>
<item><title><![CDATA[Sugar import TRQ opens]]></title><link>https://x/2</link><pubDate>Thu, 03 Sep 2026 09:00:00 +0530</pubDate></item>
<item><title>   </title><link>https://x/3</link></item>
</channel></rss>"""


def test_parse_rss_handles_cdata_entities_and_blank_titles():
    items = parse_rss(RSS, "test")
    assert [i["title"] for i in items] == ["Adani Ports to open empty container yard & more", "Sugar import TRQ opens"]
    assert items[0]["source"] == "Mint" and items[1]["source"] == "test"
    assert items[0]["published"].startswith("2026-09-04T10:00:00")
    assert parse_rss("not xml", "x") == []


def test_theme_report_with_fake_data(engine, tmp_path, prices):
    (tmp_path / "themes.yaml").write_text("mini: [NSE:RELIANCE, NSE:INFY, NSE:UNKNOWNXYZ]\n")
    prices["NSE:INFY"] = 1130.0
    rows = theme_report(engine, ["mini", "nonexistent"], Market.IN)
    mini, bad = rows
    assert bad["error"] == "unknown theme"
    assert mini["n"] == 2 and len(mini["members"]) == 3
    assert any("error" in m for m in mini["members"])       # unknown symbol reported, not fatal
    assert mini["avg_d1"] is not None and mini["avg_d20"] is not None
