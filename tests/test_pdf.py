import re

from fantasy_recap.espn import summarize_box_scores
from fantasy_recap.pdf import MIN_PAGE_H, render_pdf


def _pages(path):
    return len(re.findall(rb"/Type\s*/Page(?!s)", path.read_bytes()))


def test_long_recap_stays_on_one_taller_page(tmp_path, points_box_scores):
    s = summarize_box_scores(points_box_scores, "Test League", 2027, 3, "H2H_POINTS")
    story = "\n\n".join(["Lots of words about basketball & bad lineups. " * 40] * 6)
    path = render_pdf(s, story, tmp_path / "long.pdf")
    assert _pages(path) == 1
    height = float(re.search(rb"/MediaBox\s*\[\s*0 0 [\d.]+ ([\d.]+)", path.read_bytes()).group(1))
    assert height > MIN_PAGE_H


def test_category_league_and_emoji_names(tmp_path, category_box_scores):
    s = summarize_box_scores(category_box_scores, "Cats \U0001F3C0", 2027, 2, "H2H_CATEGORY")
    path = render_pdf(s, "A short recap.", tmp_path / "cats.pdf")
    assert _pages(path) == 1


def test_fonts_are_embedded(tmp_path, points_box_scores):
    s = summarize_box_scores(points_box_scores, "Test League", 2027, 3, "H2H_POINTS")
    data = render_pdf(s, "## Headline\nA short recap.\n\nPull quote: A short recap.", tmp_path / "f.pdf").read_bytes()
    assert b"FontFile2" in data  # TrueType fonts embedded
    assert b"Helvetica" not in data


def test_story_markup_keeps_spaces_and_colors_team_names(points_box_scores):
    from fantasy_recap.pdf import _Ctx, _register_fonts, _story_markup, assign_team_colors

    _register_fonts()
    s = summarize_box_scores(points_box_scores, "Test League", 2027, 3, "H2H_POINTS")
    ctx = _Ctx(s, assign_team_colors(s))
    out = _story_markup("dunk dynasty scored 1,012.5 and won 2-0.", ctx)
    assert re.search(r'<font name="Body-Bold" color="#[0-9a-f]{6}">dunk dynasty</font> scored <b>1,012\.5</b> and won '
                     r"<b>2</b>-<b>0</b>\.$", out)


def test_team_colors_are_stable_and_overridable(points_box_scores):
    from fantasy_recap.pdf import PALETTE, assign_team_colors

    s = summarize_box_scores(points_box_scores, "Test League", 2027, 3, "H2H_POINTS")
    colors = assign_team_colors(s, {"Hoop Dreams": "#123456"})
    by_name = {t.name: colors[t.key] for t in s.teams}
    assert by_name["Dunk Dynasty"].hexval() == "0x" + PALETTE[0][1:]
    assert by_name["Hoop Dreams"].hexval() == "0x123456"


def test_load_team_colors_rejects_bad_values(tmp_path):
    import pytest
    from fantasy_recap.pdf import load_team_colors

    good = tmp_path / "c.json"
    good.write_text('{"Dunk Dynasty": "e63946", "2": "#1E6FD9"}')
    assert load_team_colors(good) == {"Dunk Dynasty": "#e63946", "2": "#1E6FD9"}
    bad = tmp_path / "bad.json"
    bad.write_text('{"Dunk Dynasty": "red"}')
    with pytest.raises(ValueError):
        load_team_colors(bad)


def test_initials():
    from fantasy_recap.pdf import initials

    assert initials("Andrew's Astounding Team") == "AAT"
    assert initials("The Haliban") == "HA"
    assert initials("Johnny 's Scary Team") == "JST"
    assert initials("tingus pingus") == "TP"
