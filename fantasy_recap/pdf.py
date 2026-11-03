"""Render a weekly recap as a single tall PDF page shaped to read well on a phone.

Fonts are bundled (fantasy_recap/fonts, SIL Open Font License) and embedded in the PDF, so the
page looks the same and stays sharp in every viewer. Emoji in team names are drawn with Noto Emoji.
"""

from __future__ import annotations

import hashlib
import io
import json
import logging
import re
import urllib.request
from dataclasses import dataclass, field
from functools import lru_cache
from pathlib import Path
from typing import Optional, Union
from urllib.parse import urlparse
from xml.sax.saxutils import escape

from reportlab.lib import colors
from reportlab.lib.colors import Color
from reportlab.lib.styles import ParagraphStyle
from reportlab.lib.units import inch
from reportlab.lib.utils import ImageReader
from reportlab.pdfbase import pdfmetrics
from reportlab.pdfbase.pdfmetrics import registerFontFamily
from reportlab.pdfbase.ttfonts import TTFont
from reportlab.platypus import (
    BaseDocTemplate,
    Flowable,
    Frame,
    PageTemplate,
    Paragraph,
    Spacer,
    Table,
    TableStyle,
)

from .models import BigGame, Matchup, TeamWeek, WeekSummary
from .story import StoryParts, parse_story, team_of_the_week

log = logging.getLogger(__name__)

# Roughly a phone screen's aspect ratio, so the page fills the screen without zooming.
# Long recaps grow the page taller rather than shrinking the text or adding a second page.
PAGE_W, MIN_PAGE_H = 5.5 * inch, 12 * inch
MARGIN = 0.3 * inch
HEADER_H = 1.45 * inch
CONTENT_TOP_GAP = 0.22 * inch
CONTENT_W = PAGE_W - 2 * MARGIN
GAP = 8  # space between side-by-side cards

NAVY = colors.HexColor("#14213d")
NAVY_LIGHT = colors.HexColor("#22335c")
ORANGE = colors.HexColor("#f77f00")
ORANGE_DARK = colors.HexColor("#c45f00")
CREAM = colors.HexColor("#fbf8f3")
INK = colors.HexColor("#1f2430")
MUTED = colors.HexColor("#7a7f8a")
RULE = colors.HexColor("#e6e0d6")
HEADER_SUB = colors.HexColor("#aab4c8")
GREEN = colors.HexColor("#2b9348")
RED = colors.HexColor("#d62839")

# Team colors, handed out in ESPN team id order so each team keeps its color week to week.
PALETTE = [
    "#e63946", "#1e6fd9", "#2b9348", "#8e44ad", "#f08c00", "#0b9fab",
    "#d63384", "#4c5fd5", "#a0522d", "#5f8d00", "#b8860b", "#3d405b",
]

# ---------------------------------------------------------------------------
# Fonts and text

FONT_DIR = Path(__file__).parent / "fonts"
_FONT_FILES = {
    "Body": "Barlow-Regular",
    "Body-Italic": "Barlow-Italic",
    "Body-Semi": "Barlow-SemiBold",
    "Body-Bold": "Barlow-Bold",
    "Cond": "BarlowCondensed-SemiBold",
    "Cond-Bold": "BarlowCondensed-Bold",
    "Display": "BebasNeue-Regular",
    "Emoji": "NotoEmoji",
}
_INVISIBLE = {0xFE0E, 0xFE0F, 0x200D}  # emoji variation selectors and zero-width joiners


@lru_cache(maxsize=None)
def _register_fonts() -> None:
    for name, file in _FONT_FILES.items():
        pdfmetrics.registerFont(TTFont(name, str(FONT_DIR / f"{file}.ttf")))
    registerFontFamily("Body", normal="Body", bold="Body-Bold", italic="Body-Italic", boldItalic="Body-Bold")


@lru_cache(maxsize=None)
def _cmap(font: str) -> dict:
    return pdfmetrics.getFont(font).face.charToGlyph


def _runs(text: str, font: str) -> list[tuple[str, str]]:
    """Split text into (font, chunk) runs: characters the font lacks go to the emoji font,
    and characters neither font can draw are dropped."""
    base, emoji = _cmap(font), _cmap("Emoji")
    runs: list[list[str]] = []
    for ch in text:
        cp = ord(ch)
        if cp in _INVISIBLE:
            continue
        f = font if cp in base else "Emoji" if cp in emoji else None
        if f is None:
            continue
        if runs and runs[-1][0] == f:
            runs[-1][1] += ch
        else:
            runs.append([f, ch])
    return [(f, s) for f, s in runs]


def _rich(text: str) -> str:
    """Escape text for Paragraph markup, switching to the emoji font where needed."""
    out = []
    for f, chunk in _runs(re.sub(r"\s+", " ", text), "Body"):
        chunk = escape(chunk)
        out.append(f'<font name="Emoji">{chunk}</font>' if f == "Emoji" else chunk)
    return "".join(out)


def _text_width(text: str, font: str, size: float, char_space: float = 0) -> float:
    runs = _runs(text, font)
    return sum(pdfmetrics.stringWidth(s, f, size) for f, s in runs) + char_space * sum(len(s) for _, s in runs)


def _draw_text(c, x: float, y: float, text: str, font: str, size: float, color,
               char_space: float = 0, align: str = "left") -> float:
    width = _text_width(text, font, size, char_space)
    if align == "right":
        x -= width
    elif align == "center":
        x -= width / 2
    t = c.beginText(x, y)
    t.setFillColor(color)
    t.setCharSpace(char_space)
    for f, chunk in _runs(text, font):
        t.setFont(f, size)
        t.textOut(chunk)
    if char_space:
        t.setCharSpace(0)  # PDF text state outlives the text object; don't leak tracking into later text
    c.drawText(t)
    return width


def _fit(text: str, font: str, size: float, width: float) -> str:
    """Trim text with an ellipsis so it fits on one line."""
    if _text_width(text, font, size) <= width:
        return text
    while text and _text_width(text + "…", font, size) > width:
        text = text[:-1]
    return text.rstrip() + "…"


def _style(name: str, **kw) -> ParagraphStyle:
    base = {} if "parent" in kw else {"fontName": "Body", "fontSize": 10, "leading": 13, "textColor": INK}
    base.update(kw)
    return ParagraphStyle(name, **base)


BODY = _style("body", fontSize=10.5, leading=15.5, spaceAfter=6)
HEADLINE = _style("headline", fontName="Cond-Bold", fontSize=13.5, leading=16, textColor=NAVY,
                  spaceBefore=4, spaceAfter=2)
QUOTE = _style("quote", fontName="Body-Italic", fontSize=14, leading=18.5, textColor=NAVY)
AWARD_TEAM = _style("award_team", fontName="Body-Bold", fontSize=17, leading=19.5, textColor=colors.white)
CARD_WIN = _style("card_win", fontName="Body-Bold", fontSize=9, leading=10.5)
CARD_LOSE = _style("card_lose", fontSize=9, leading=10.5, textColor=MUTED)
STANDING_NAME = _style("standing_name", fontName="Body-Semi", fontSize=9, leading=10.5)
STANDING_CELL = _style("standing_cell", fontName="Cond", fontSize=9, leading=10.5, alignment=2)
STANDING_HEAD = _style("standing_head", fontName="Cond-Bold", fontSize=7, leading=8.5, textColor=MUTED)
GOTW_DETAIL = _style("gotw_detail", fontSize=9, leading=12, textColor=HEADER_SUB)
BYE = _style("bye", fontSize=8.5, leading=11, textColor=MUTED)

# ---------------------------------------------------------------------------
# Colors


def _hex(c: Color) -> str:
    return "#" + c.hexval()[2:]


def _luminance(c: Color) -> float:
    def channel(v: float) -> float:
        return v / 12.92 if v <= 0.03928 else ((v + 0.055) / 1.055) ** 2.4
    return 0.2126 * channel(c.red) + 0.7152 * channel(c.green) + 0.0722 * channel(c.blue)


def _ink(c: Color, contrast: float = 4.5) -> Color:
    """Darken a color until text in it is readable on a white or cream background."""
    for _ in range(25):
        if 1.05 / (_luminance(c) + 0.05) >= contrast:
            break
        c = Color(c.red * 0.9, c.green * 0.9, c.blue * 0.9)
    return c


def _tint(c: Color, amount: float) -> Color:
    """Mix a color toward white (amount 1 = white)."""
    return Color(*(v + (1 - v) * amount for v in (c.red, c.green, c.blue)))


_HEX_RE = re.compile(r"^#?[0-9a-fA-F]{6}$")


def load_team_colors(path: Path) -> dict[str, str]:
    """Read a JSON object mapping team names or ESPN team ids to hex colors like "#e63946"."""
    data = json.loads(Path(path).read_text(encoding="utf-8"))
    if not isinstance(data, dict):
        raise ValueError("expected a JSON object like {\"Team Name\": \"#e63946\"}")
    for key, value in data.items():
        if not isinstance(value, str) or not _HEX_RE.match(value):
            raise ValueError(f"{key!r}: {value!r} isn't a hex color like \"#e63946\"")
    return {str(k): v if v.startswith("#") else "#" + v for k, v in data.items()}


def assign_team_colors(summary: WeekSummary, overrides: Optional[dict[str, str]] = None) -> dict:
    """Map each team's key to a color, letting overrides (by name or team id) win."""
    teams = sorted(summary.teams, key=lambda t: (t.team_id is None, t.team_id or 0, t.name.lower()))
    out = {t.key: colors.HexColor(PALETTE[i % len(PALETTE)]) for i, t in enumerate(teams)}
    for t in teams:
        for k in (t.name, str(t.team_id)):
            if overrides and k in overrides:
                out[t.key] = colors.HexColor(overrides[k])
    return out


# ---------------------------------------------------------------------------
# Logos

LOGO_CACHE = Path.home() / ".cache" / "fantasy_recap" / "logos"


def _download(url: str, config, timeout: float) -> bytes:
    headers = {"User-Agent": "Mozilla/5.0 (fantasy_recap)"}
    host = urlparse(url).hostname or ""
    # Custom league logos sit behind the same login as the league, so send the ESPN cookies,
    # but only ever to ESPN.
    if config is not None and getattr(config, "espn_s2", None) and (host == "espn.com" or host.endswith(".espn.com")):
        headers["Cookie"] = f"espn_s2={config.espn_s2}; SWID={config.swid}"
    with urllib.request.urlopen(urllib.request.Request(url, headers=headers), timeout=timeout) as resp:
        return resp.read(5_000_000)


def _logo(url: str, config, timeout: float) -> Optional[ImageReader]:
    stem = LOGO_CACHE / hashlib.sha256(url.encode()).hexdigest()[:24]
    png, unusable = stem.with_suffix(".png"), stem.with_suffix(".none")
    if png.exists():
        return ImageReader(str(png))
    if unusable.exists():
        return None
    from PIL import Image  # reportlab depends on Pillow

    try:
        data = _download(url, config, timeout)
    except Exception as exc:  # network trouble: try again next time
        log.debug("couldn't download logo %s: %s", url, exc)
        return None
    LOGO_CACHE.mkdir(parents=True, exist_ok=True)
    try:
        im = Image.open(io.BytesIO(data))
        im.load()
    except Exception:  # SVG (ESPN's default logos) or not an image: use initials instead
        unusable.touch()
        return None
    im = im.convert("RGBA")
    side = min(im.size)
    left, top = (im.width - side) // 2, (im.height - side) // 2
    im = im.crop((left, top, left + side, top + side)).resize((160, 160))
    im.save(png)
    return ImageReader(str(png))


def fetch_logos(summary: WeekSummary, config=None, timeout: float = 8) -> dict:
    """Download (and cache) each team's ESPN logo. Teams without a usable one get initials."""
    logos = {}
    for team in summary.teams:
        if team.logo_url:
            image = _logo(team.logo_url, config, timeout)
            if image is not None:
                logos[team.key] = image
    return logos


# ---------------------------------------------------------------------------
# Building blocks


@dataclass
class _Ctx:
    summary: WeekSummary
    colors: dict
    logos: dict = field(default_factory=dict)

    def color(self, team: TeamWeek) -> Color:
        return self.colors.get(team.key, NAVY)


def initials(name: str) -> str:
    words = re.findall(r"[^\W_]+", re.sub(r"['’]s\b", "", name))
    if not words:
        return "?"
    if len(words) > 1 and words[0].lower() == "the":
        words = words[1:]
    if len(words) == 1:
        return words[0][:2].upper()
    return "".join(w[0] for w in words[:3]).upper()


def _draw_badge(c, cx: float, cy: float, d: float, team: TeamWeek, ctx: _Ctx, ring=None) -> None:
    """A team's logo in a circle, or its initials on its team color."""
    r = d / 2
    color = ctx.color(team)
    logo = ctx.logos.get(team.key)
    c.saveState()
    if logo is not None:
        c.setFillColor(colors.white)
        c.circle(cx, cy, r, stroke=0, fill=1)
        clip = c.beginPath()
        clip.circle(cx, cy, r)
        c.clipPath(clip, stroke=0, fill=0)
        c.drawImage(logo, cx - r, cy - r, d, d, mask="auto")
        c.restoreState()
        c.saveState()
        c.setStrokeColor(ring or color)
        c.setLineWidth(max(1.0, d / 16))
        c.circle(cx, cy, r, stroke=1, fill=0)
    else:
        c.setFillColor(color)
        c.circle(cx, cy, r, stroke=0, fill=1)
        if ring is not None:
            c.setStrokeColor(ring)
            c.setLineWidth(max(1.0, d / 16))
            c.circle(cx, cy, r, stroke=1, fill=0)
        text = initials(team.name)
        size = d * (0.44 if len(text) < 3 else 0.36)
        _draw_text(c, cx, cy - size * 0.36, text, "Cond-Bold", size, colors.white, align="center")
    c.restoreState()


class Badge(Flowable):
    def __init__(self, team: TeamWeek, ctx: _Ctx, d: float):
        super().__init__()
        self.team, self.ctx, self.d = team, ctx, d
        self.width = self.height = d

    def draw(self):
        _draw_badge(self.canv, self.d / 2, self.d / 2, self.d, self.team, self.ctx)


PILL_H, PILL_PAD, PILL_SIZE, PILL_GAP = 12.5, 5, 6.8, 4


def _pill_width(text: str) -> float:
    return 2 * PILL_PAD + _text_width(text, "Cond-Bold", PILL_SIZE, 0.4)


def _pill_rows(texts: list[str], width: float) -> list[list[str]]:
    rows: list[list[str]] = []
    used = width + 1
    for text in texts:
        w = _pill_width(text)
        if rows and used + PILL_GAP + w <= width:
            rows[-1].append(text)
            used += PILL_GAP + w
        else:
            rows.append([text])
            used = w
    return rows


def _draw_pill(c, x: float, y: float, text: str, fill, ink) -> float:
    w = _pill_width(text)
    c.setFillColor(fill)
    c.roundRect(x, y, w, PILL_H, PILL_H / 2, stroke=0, fill=1)
    _draw_text(c, x + PILL_PAD, y + 3.7, text, "Cond-Bold", PILL_SIZE, ink, 0.4)
    return w


def _num(x: float) -> str:
    return f"{x:,.0f}" if float(x).is_integer() else f"{x:,.1f}"


def _is_final(summary: WeekSummary) -> bool:
    return all(m.status == "final" for m in summary.real_matchups)


class SectionHead(Flowable):
    def __init__(self, title: str):
        super().__init__()
        self.title = title

    def wrap(self, aw, ah):
        self.width = aw
        return aw, 26

    def draw(self):
        c = self.canv
        c.setFillColor(ORANGE)
        c.roundRect(0, 8, 4, 13, 1.5, stroke=0, fill=1)
        w = _draw_text(c, 10, 8.5, self.title, "Display", 18, NAVY, char_space=0.6)
        c.setStrokeColor(RULE)
        c.setLineWidth(0.8)
        c.line(10 + w + 8, 14, self.width, 14)


# ---------------------------------------------------------------------------
# Header


def _draw_basketball(c, cx: float, cy: float, r: float) -> None:
    c.saveState()
    c.setFillColor(ORANGE)
    c.circle(cx, cy, r, stroke=0, fill=1)
    clip = c.beginPath()
    clip.circle(cx, cy, r)
    c.clipPath(clip, stroke=0, fill=0)
    c.setStrokeColor(NAVY)
    c.setLineWidth(1.6)
    c.line(cx - r, cy, cx + r, cy)
    c.line(cx, cy - r, cx, cy + r)
    c.circle(cx - 1.55 * r, cy, 1.15 * r, stroke=1, fill=0)
    c.circle(cx + 1.55 * r, cy, 1.15 * r, stroke=1, fill=0)
    c.restoreState()
    c.setStrokeColor(NAVY)
    c.setLineWidth(1.6)
    c.circle(cx, cy, r, stroke=1, fill=0)


def _page_decor(summary: WeekSummary, page_h: float):
    def draw(c, _doc) -> None:
        c.saveState()
        c.setFillColor(CREAM)
        c.rect(0, 0, PAGE_W, page_h, stroke=0, fill=1)

        top = page_h - HEADER_H
        c.setFillColor(NAVY)
        c.rect(0, top, PAGE_W, HEADER_H, stroke=0, fill=1)
        # faint diagonal court stripes
        c.saveState()
        clip = c.beginPath()
        clip.rect(0, top, PAGE_W, HEADER_H)
        c.clipPath(clip, stroke=0, fill=0)
        c.setStrokeColor(NAVY_LIGHT)
        c.setLineWidth(9)
        x = -HEADER_H
        while x < PAGE_W:
            c.line(x, top, x + HEADER_H, page_h)
            x += 28
        c.restoreState()
        c.setFillColor(ORANGE)
        c.rect(0, top - 4, PAGE_W, 4, stroke=0, fill=1)

        _draw_text(c, MARGIN, top + 80, summary.league_name.upper(), "Cond-Bold", 10, ORANGE, char_space=1.8)
        _draw_text(c, MARGIN, top + 34, f"WEEK {summary.week} RECAP", "Display", 50, colors.white, char_space=0.5)
        status = "FINAL" if _is_final(summary) else "IN PROGRESS"
        season = f"{summary.season - 1}-{str(summary.season)[-2:]} SEASON"
        _draw_text(c, MARGIN, top + 16, f"{season}  •  {status}", "Cond", 9, HEADER_SUB, char_space=1.4)

        _draw_basketball(c, PAGE_W - MARGIN - 0.4 * inch, top + 0.7 * inch, 0.38 * inch)
        c.restoreState()

    return draw


# ---------------------------------------------------------------------------
# Team of the week and stat tiles


class AwardCard(Flowable):
    PAD, BADGE = 14, 50

    def __init__(self, summary: WeekSummary, ctx: _Ctx):
        super().__init__()
        self.team = summary.high_score_team
        self.ctx = ctx
        self.label = "\U0001F3C6 TEAM OF THE WEEK" + ("" if _is_final(summary) else " (SO FAR)")

    def wrap(self, aw, ah):
        self.width = aw
        self.score_w = _text_width(_num(self.team.score), "Display", 44) + 4
        self.name_x = self.PAD + self.BADGE + 12
        self.name = Paragraph(_rich(self.team.name), AWARD_TEAM)
        _, self.name_h = self.name.wrap(aw - self.name_x - self.score_w - self.PAD - 6, ah)
        self.height = max(84, 2 * self.PAD + 14 + self.name_h)
        return aw, self.height

    def draw(self):
        c, h = self.canv, self.height
        c.setFillColor(ORANGE)
        c.roundRect(0, 0, self.width, h, 10, stroke=0, fill=1)
        _draw_badge(c, self.PAD + self.BADGE / 2, h / 2, self.BADGE, self.team, self.ctx, ring=colors.white)
        block = 14 + self.name_h
        y_top = h / 2 + block / 2
        _draw_text(c, self.name_x, y_top - 8, self.label, "Cond-Bold", 8.5, colors.white, char_space=1.2)
        self.name.drawOn(c, self.name_x, y_top - block)
        right = self.width - self.PAD
        _draw_text(c, right, h / 2 - 6, _num(self.team.score), "Display", 44, colors.white, align="right")
        _draw_text(c, right, h / 2 - 18, "POINTS", "Cond-Bold", 8, colors.white, char_space=1.2, align="right")


@dataclass
class _Tile:
    emoji: str
    label: str
    value: str
    line1: str
    line1_team: Optional[TeamWeek] = None
    line2: str = ""
    line2_team: Optional[TeamWeek] = None


def _opponent(summary: WeekSummary, team: TeamWeek) -> Optional[TeamWeek]:
    for m in summary.real_matchups:
        if m.home is team:
            return m.away
        if m.away is team:
            return m.home
    return None


def _tiles(summary: WeekSummary) -> list[_Tile]:
    if summary.scoring != "points" or not summary.real_matchups:
        return []
    tiles = []
    top = summary.top_player
    if top and top[0].points > 0:
        tiles.append(_Tile("\U0001F451", "TOP PLAYER", _num(top[0].points), top[0].name, None, top[1].name, top[1]))
    worst = summary.worst_starter
    if worst and (not top or worst[0] is not top[0]):
        tiles.append(_Tile("\U0001F9F1", "WORST START", _num(worst[0].points), worst[0].name, None,
                           worst[1].name, worst[1]))
    low = summary.lowest_score_team
    if low:
        opp = _opponent(summary, low)
        tiles.append(_Tile("\U0001F9CA", "LOWEST SCORE", _num(low.score), low.name, low,
                           f"vs {opp.name}" if opp else "", None))
    bench = summary.most_bench_points
    if bench:
        best = bench.top_bench_player
        tiles.append(_Tile("\U0001FA91", "LEFT ON THE BENCH", _num(bench.bench_points), bench.name, bench,
                           f"incl. {best.name} ({_num(best.points)})" if best else "", None))
    blowout = summary.biggest_blowout
    if blowout and blowout.leader:
        tiles.append(_Tile("\U0001F4CF", "BIGGEST MARGIN", _num(blowout.margin), blowout.leader.name,
                           blowout.leader, f"over {blowout.trailer.name}", None))
    return tiles[:4]


class StatTiles(Flowable):
    TILE_H, PAD = 76, 10

    def __init__(self, tiles: list[_Tile], ctx: _Ctx):
        super().__init__()
        self.tiles, self.ctx = tiles, ctx

    def wrap(self, aw, ah):
        self.width = aw
        rows = (len(self.tiles) + 1) // 2
        self.height = rows * self.TILE_H + (rows - 1) * GAP
        return aw, self.height

    def draw(self):
        c = self.canv
        tw = (self.width - GAP) / 2
        for i, tile in enumerate(self.tiles):
            x = (i % 2) * (tw + GAP)
            y = self.height - (i // 2 + 1) * self.TILE_H - (i // 2) * GAP
            self._tile(c, x, y, tw, tile)

    def _tile(self, c, x, y, w, t: _Tile):
        h, pad = self.TILE_H, self.PAD
        accent = self.ctx.color(t.line1_team or t.line2_team) if (t.line1_team or t.line2_team) else ORANGE
        c.setFillColor(colors.white)
        c.setStrokeColor(RULE)
        c.setLineWidth(0.6)
        c.roundRect(x, y, w, h, 7, stroke=1, fill=1)
        c.saveState()
        clip = c.beginPath()
        clip.roundRect(x, y, w, h, 7)
        c.clipPath(clip, stroke=0, fill=0)
        c.setFillColor(accent)
        c.rect(x, y + h - 3, w, 3, stroke=0, fill=1)
        c.restoreState()
        inner = w - 2 * pad
        ty = y + h - pad - 9
        _draw_text(c, x + pad, ty, f"{t.emoji} {t.label}", "Cond-Bold", 7.5, MUTED, char_space=1)
        ty -= 25
        _draw_text(c, x + pad, ty, t.value, "Display", 28, INK)
        ty -= 12
        ink1 = _ink(self.ctx.color(t.line1_team)) if t.line1_team else INK
        _draw_text(c, x + pad, ty, _fit(t.line1, "Body-Bold", 8.5, inner), "Body-Bold", 8.5, ink1)
        if t.line2:
            ty -= 10.5
            ink2 = _ink(self.ctx.color(t.line2_team)) if t.line2_team else MUTED
            font = "Body-Semi" if t.line2_team else "Body"
            _draw_text(c, x + pad, ty, _fit(t.line2, font, 7.5, inner), font, 7.5, ink2)


# ---------------------------------------------------------------------------
# Scoreboard

TAG_STYLES = {
    "closest": ("\U0001F3AF CLOSEST", colors.HexColor("#1e6fd9")),
    "blowout": ("\U0001F4A5 BLOWOUT", RED),
    "upset": ("\U0001F525 UPSET", colors.HexColor("#e8590c")),
    "bench": ("\U0001F480 BENCH BLUNDER", colors.HexColor("#6f42c1")),
}


def _tags(m: Matchup, summary: WeekSummary) -> list[str]:
    tags = []
    if m is summary.closest:
        tags.append("closest")
    elif m is summary.biggest_blowout:
        tags.append("blowout")
    if m.is_upset:
        tags.append("upset")
    if m.bench_cost_it:
        tags.append("bench")
    return tags


def _team_score(team: TeamWeek, scoring: str) -> str:
    if scoring == "categories" and team.category_record:
        return team.category_record
    return _num(team.score)


def _share(m: Matchup, team: TeamWeek) -> float:
    other = m.away if team is m.home else m.home
    if m.scoring == "categories":
        a, b = team.score, other.score
    else:
        a, b = max(team.score, 0), max(other.score, 0)
    return 0.5 if a + b <= 0 else a / (a + b)


class MatchupCard(Flowable):
    PAD, STRIPE, ROW, BADGE, BAR = 8, 4, 29, 19, 4

    def __init__(self, m: Matchup, ctx: _Ctx, width: float, tag_h: float):
        super().__init__()
        self.m, self.ctx, self.width, self.tag_h = m, ctx, width, tag_h
        self.tags = [TAG_STYLES[t] for t in _tags(m, ctx.summary)]
        leader = m.leader
        self.teams = sorted((m.home, m.away), key=lambda t: t is not leader)
        self.height = (self.PAD + (tag_h + 6 if tag_h else 0) + 2 * self.ROW + 7 + self.BAR + self.PAD)

    @classmethod
    def tag_height(cls, m: Matchup, summary: WeekSummary, width: float) -> float:
        texts = [TAG_STYLES[t][0] for t in _tags(m, summary)]
        if not texts:
            return 0
        rows = len(_pill_rows(texts, width - cls.STRIPE - 2 * cls.PAD))
        return rows * PILL_H + (rows - 1) * 3

    def wrap(self, aw, ah):
        return self.width, self.height

    def _name(self, team: TeamWeek, won: bool, width: float) -> Paragraph:
        rec = team.short_record
        for size in (9, 8, 7):
            style = _style("n", parent=CARD_WIN if won else CARD_LOSE, fontSize=size, leading=size + 1.5)
            text = _rich(team.name)
            if rec:
                text += f' <font name="Cond" size="{size - 1}" color="{_hex(MUTED)}">{rec}</font>'
            p = Paragraph(text, style)
            if p.wrap(width, 1000)[1] <= self.ROW - 3:
                break
        return p

    def draw(self):
        c, m, w, h = self.canv, self.m, self.width, self.height
        pad, x0 = self.PAD, self.STRIPE + self.PAD
        c.setFillColor(colors.white)
        c.setStrokeColor(RULE)
        c.setLineWidth(0.6)
        c.roundRect(0, 0, w, h, 7, stroke=1, fill=1)

        y = h - pad
        if self.tag_h:
            px, row_y = x0, y - PILL_H
            for row in _pill_rows([t for t, _ in self.tags], w - x0 - pad):
                px = x0
                for text in row:
                    color = dict(self.tags)[text]
                    px += _draw_pill(c, px, row_y, text, _tint(color, 0.86), _ink(color)) + PILL_GAP
                row_y -= PILL_H + 3
            y -= self.tag_h + 6

        leader = m.leader
        score_w = max(_text_width(_team_score(t, m.scoring), "Display", 18) for t in self.teams) + 2
        name_x = x0 + self.BADGE + 6
        name_w = w - name_x - pad - score_w - 4
        rows_top = y
        for team in self.teams:
            won = leader is None or team is leader
            cy = y - self.ROW / 2
            _draw_badge(c, x0 + self.BADGE / 2, cy, self.BADGE, team, self.ctx)
            para = self._name(team, won, name_w)
            _, ph = para.wrap(name_w, self.ROW)
            para.drawOn(c, name_x, cy - ph / 2)
            _draw_text(c, w - pad, cy - 6, _team_score(team, m.scoring), "Display", 18,
                       INK if won else MUTED, align="right")
            y -= self.ROW

        # stripe down the left edge, one color per team row
        c.saveState()
        clip = c.beginPath()
        clip.roundRect(0, 0, w, h, 7)
        c.clipPath(clip, stroke=0, fill=0)
        mid = rows_top - self.ROW
        c.setFillColor(self.ctx.color(self.teams[0]))
        c.rect(0, mid, self.STRIPE, h - mid, stroke=0, fill=1)
        c.setFillColor(self.ctx.color(self.teams[1]))
        c.rect(0, 0, self.STRIPE, mid, stroke=0, fill=1)
        c.restoreState()

        # margin bar: each team's share of the combined score
        bar_y, bar_w = pad, w - x0 - pad
        split = bar_w * _share(m, self.teams[0])
        c.saveState()
        clip = c.beginPath()
        clip.roundRect(x0, bar_y, bar_w, self.BAR, self.BAR / 2)
        c.clipPath(clip, stroke=0, fill=0)
        c.setFillColor(self.ctx.color(self.teams[0]))
        c.rect(x0, bar_y, split, self.BAR, stroke=0, fill=1)
        c.setFillColor(_tint(self.ctx.color(self.teams[1]), 0.35))
        c.rect(x0 + split, bar_y, bar_w - split, self.BAR, stroke=0, fill=1)
        c.setFillColor(colors.white)
        c.rect(x0 + split - 0.75, bar_y, 1.5, self.BAR, stroke=0, fill=1)
        c.restoreState()


def _scoreboard(summary: WeekSummary, ctx: _Ctx) -> list:
    games = summary.real_matchups
    if not games:
        return []
    card_w = (CONTENT_W - GAP) / 2
    rows = []
    for i in range(0, len(games), 2):
        pair = games[i:i + 2]
        tag_h = max(MatchupCard.tag_height(m, summary, card_w) for m in pair)
        cards = [MatchupCard(m, ctx, card_w, tag_h) for m in pair]
        rows.append(cards + [""] * (2 - len(cards)))
    grid = Table(rows, colWidths=[card_w + GAP / 2, card_w + GAP / 2])
    grid.setStyle(TableStyle([
        ("FONT", (0, 0), (-1, -1), "Body", 9),
        ("VALIGN", (0, 0), (-1, -1), "TOP"),
        ("LEFTPADDING", (0, 0), (0, -1), 0),
        ("RIGHTPADDING", (0, 0), (0, -1), GAP / 2),
        ("LEFTPADDING", (1, 0), (1, -1), GAP / 2),
        ("RIGHTPADDING", (1, 0), (1, -1), 0),
        ("TOPPADDING", (0, 0), (-1, -1), 0),
        ("BOTTOMPADDING", (0, 0), (-1, -1), GAP),
    ]))
    out = [SectionHead("SCOREBOARD"), grid]
    byes = [m.home for m in summary.matchups if m.is_bye]
    if byes:
        out.append(Paragraph("Bye: " + ", ".join(_rich(t.name) for t in byes), BYE))
    return out + [Spacer(1, 10)]


# ---------------------------------------------------------------------------
# Standings


class Movement(Flowable):
    def __init__(self, delta: Optional[int]):
        super().__init__()
        self.delta = delta
        self.width, self.height = 22, 10

    def draw(self):
        c = self.canv
        if not self.delta:
            if self.delta == 0:
                c.setStrokeColor(RULE)
                c.setLineWidth(1.2)
                c.line(4, 5, 10, 5)
            return
        up = self.delta > 0
        c.setFillColor(GREEN if up else RED)
        p = c.beginPath()
        if up:
            p.moveTo(2, 1.5); p.lineTo(9, 1.5); p.lineTo(5.5, 7.5)
        else:
            p.moveTo(2, 7.5); p.lineTo(9, 7.5); p.lineTo(5.5, 1.5)
        p.close()
        c.drawPath(p, stroke=0, fill=1)
        _draw_text(c, 11, 1.6, str(abs(self.delta)), "Cond-Bold", 8, GREEN if up else RED)


def _standings(summary: WeekSummary, ctx: _Ctx) -> list:
    table = summary.standings
    if not table:
        return []
    points = summary.scoring == "points"
    head = ["#", "", "", Paragraph("TEAM", STANDING_HEAD),
            Paragraph("W-L", _style("h", parent=STANDING_HEAD, alignment=2))]
    widths = [26, 26, 24, None, 34]
    if points:
        head.append(Paragraph("PF", _style("h2", parent=STANDING_HEAD, alignment=2)))
        widths.append(50)
    rows = [head]
    for r in table:
        row = [
            Paragraph(str(r.rank), _style("rk", fontName="Cond-Bold", fontSize=11, leading=12, textColor=NAVY)),
            Movement(r.movement if r.prev_rank is not None else None),
            Badge(r.team, ctx, 16),
            Paragraph(_rich(r.team.name), STANDING_NAME),
            Paragraph(r.team.short_record, STANDING_CELL),
        ]
        if points:
            row.append(Paragraph(_num(r.team.points_for), _style("pf", parent=STANDING_CELL, textColor=MUTED)))
        rows.append(row)
    fixed = sum(w for w in widths if w)
    widths = [w or CONTENT_W - fixed for w in widths]
    t = Table(rows, colWidths=widths, rowHeights=[16] + [22] * len(table))
    styles = [
        ("BACKGROUND", (0, 0), (-1, 0), CREAM),
        ("BACKGROUND", (0, 1), (-1, -1), colors.white),
        ("FONT", (0, 0), (-1, -1), "Body", 9),
        ("FONT", (0, 0), (0, 0), "Cond-Bold", 7),
        ("TEXTCOLOR", (0, 0), (0, 0), MUTED),
        ("VALIGN", (0, 0), (-1, -1), "MIDDLE"),
        ("LEFTPADDING", (0, 0), (-1, -1), 4),
        ("RIGHTPADDING", (0, 0), (-1, -1), 4),
        ("LEFTPADDING", (0, 0), (0, -1), 9),
        ("RIGHTPADDING", (0, 0), (0, -1), 0),
        ("RIGHTPADDING", (-1, 0), (-1, -1), 9),
        ("TOPPADDING", (0, 0), (-1, -1), 0),
        ("BOTTOMPADDING", (0, 0), (-1, -1), 0),
        ("LINEBELOW", (0, 0), (-1, -2), 0.5, RULE),
        ("BOX", (0, 0), (-1, -1), 0.6, RULE),
        ("ROUNDEDCORNERS", [7, 7, 7, 7]),
    ]
    for i in range(2, len(rows), 2):
        styles.append(("BACKGROUND", (0, i), (-1, i), colors.HexColor("#fdfcfa")))
    t.setStyle(TableStyle(styles))
    return [SectionHead("STANDINGS"), t, Spacer(1, 4)]


# ---------------------------------------------------------------------------
# Recap text

_NUMBER = r"(?<![\w.])(?:\d{1,3}(?:,\d{3})+|\d+)(?:\.\d+)?(?!\w)"


def _norm(name: str) -> str:
    return re.sub(r"\W", "", name).lower()


def _story_markup(text: str, ctx: _Ctx) -> str:
    """Paragraph markup with team names bold in their team color and numbers bold."""
    teams = {_norm(t.name): t for t in ctx.summary.teams if _norm(t.name)}
    names = sorted({t.name for t in teams.values()}, key=len, reverse=True)
    name_re = "|".join(r"\s*".join(re.escape(w) for w in n.split()) for n in names)
    pattern = re.compile((rf"(?P<team>(?<!\w)(?i:{name_re})(?!\w))|" if name_re else "") + rf"(?P<num>{_NUMBER})")
    out, pos = [], 0
    for match in pattern.finditer(text):
        out.append(_rich(text[pos:match.start()]))
        found = match.group()
        team = teams.get(_norm(found)) if match.lastgroup == "team" else None
        if team is not None:
            out.append(f'<font name="Body-Bold" color="{_hex(_ink(ctx.color(team)))}">{_rich(found)}</font>')
        else:
            out.append(f"<b>{escape(found)}</b>")
        pos = match.end()
    out.append(_rich(text[pos:]))
    return "".join(out)


def _pull_quote(quote: str) -> list:
    para = Paragraph(f"“{_rich(quote)}”", QUOTE)
    box = Table([[para]], colWidths=[CONTENT_W])
    box.setStyle(TableStyle([
        ("FONT", (0, 0), (-1, -1), "Body", 9),
        ("LINEBEFORE", (0, 0), (0, -1), 3.5, ORANGE),
        ("LEFTPADDING", (0, 0), (-1, -1), 14),
        ("RIGHTPADDING", (0, 0), (-1, -1), 8),
        ("TOPPADDING", (0, 0), (-1, -1), 4),
        ("BOTTOMPADDING", (0, 0), (-1, -1), 6),
    ]))
    return [Spacer(1, 6), box, Spacer(1, 10)]


def _recap(parts: StoryParts, ctx: _Ctx) -> list:
    if not parts.sections:
        return []
    out: list = [SectionHead("THE RECAP")]
    quote_after = 0 if len(parts.sections) > 1 else len(parts.sections) - 1
    for i, (headline, para) in enumerate(parts.sections):
        if headline:
            out.append(Paragraph(_rich(headline.upper()), HEADLINE))
        out.append(Paragraph(_story_markup(para, ctx), BODY))
        if parts.pull_quote and i == quote_after:
            out += _pull_quote(parts.pull_quote)
    return out


# ---------------------------------------------------------------------------
# Game of the week

GOTW_STATS = (("PTS", "PTS"), ("REB", "REB"), ("AST", "AST"), ("STL", "STL"), ("BLK", "BLK"), ("3PM", "3PM"))


class GameOfTheWeek(Flowable):
    PAD, BOX_H = 14, 46

    def __init__(self, game: BigGame, ctx: _Ctx):
        super().__init__()
        self.game, self.ctx = game, ctx
        self.stats = [(label, int(game.stats[key])) for key, label in GOTW_STATS
                      if game.stats.get(key) is not None and (key in ("PTS", "REB", "AST") or game.stats[key] > 0)]
        self.team = next((t for t in ctx.summary.teams if t.name == game.fantasy_team), None)

    def _details(self) -> str:
        g = self.game
        team = _rich(g.fantasy_team)
        if self.team is not None:
            team = f'<font name="Body-Bold" color="{_hex(_tint(self.ctx.color(self.team), 0.45))}">{team}</font>'
        bits = [f"for {team}" if g.started else f"wasted on {team}’s bench"]
        if g.opponent:
            bits.append(f"vs {_rich(g.opponent)}")
        if g.date:
            bits.append(_rich(g.date))
        return "  •  ".join(bits)

    def wrap(self, aw, ah):
        self.width = aw
        self.fpts_w = 0
        if self.game.fantasy_points is not None:
            self.fpts_w = max(_text_width(_num(self.game.fantasy_points), "Display", 30),
                              _text_width("FANTASY PTS", "Cond-Bold", 7, 1)) + 8
        self.detail = Paragraph(self._details(), GOTW_DETAIL)
        _, self.detail_h = self.detail.wrap(aw - 2 * self.PAD - self.fpts_w, ah)
        self.note_rows = _pill_rows([n.upper() for n in self.game.notes], aw - 2 * self.PAD) if self.game.notes else []
        notes_h = len(self.note_rows) * (PILL_H + 4) + 6 if self.note_rows else 0
        boxes_h = self.BOX_H + 12 if self.stats else 0
        self.height = self.PAD + 8 + 28 + 8 + self.detail_h + boxes_h + notes_h + self.PAD - 4
        return aw, self.height

    def draw(self):
        c, w, h, pad = self.canv, self.width, self.height, self.PAD
        c.setFillColor(NAVY)
        c.roundRect(0, 0, w, h, 10, stroke=0, fill=1)
        y = h - pad - 8
        _draw_text(c, pad, y, "⭐ GAME OF THE WEEK", "Cond-Bold", 8.5, ORANGE, char_space=1.5)
        y -= 6 + 22
        name_w = w - 2 * pad - self.fpts_w
        _draw_text(c, pad, y, _fit(self.game.player.upper(), "Display", 28, name_w), "Display", 28, colors.white,
                   char_space=0.4)
        if self.fpts_w:
            _draw_text(c, w - pad, y, _num(self.game.fantasy_points), "Display", 30, ORANGE, align="right")
            _draw_text(c, w - pad, y - 10, "FANTASY PTS", "Cond-Bold", 7, HEADER_SUB, char_space=1, align="right")
        y -= 8 + self.detail_h
        self.detail.drawOn(c, pad, y)
        y -= 12
        if self.stats:
            n = len(self.stats)
            bw = (w - 2 * pad - (n - 1) * 6) / n
            y -= self.BOX_H
            for i, (label, value) in enumerate(self.stats):
                bx = pad + i * (bw + 6)
                c.setFillColor(NAVY_LIGHT)
                c.roundRect(bx, y, bw, self.BOX_H, 6, stroke=0, fill=1)
                _draw_text(c, bx + bw / 2, y + 17, str(value), "Display", 26,
                           ORANGE if label == "PTS" else colors.white, align="center")
                _draw_text(c, bx + bw / 2, y + 7, label, "Cond-Bold", 7, HEADER_SUB, char_space=1, align="center")
        if self.note_rows:
            y -= 6
            for row in self.note_rows:
                y -= PILL_H
                x = pad
                for note in row:
                    x += _draw_pill(c, x, y, note, NAVY_LIGHT, ORANGE) + PILL_GAP
                y -= 4


# ---------------------------------------------------------------------------


def render_pdf(
    summary: WeekSummary,
    story: Union[str, StoryParts],
    path: Path,
    team_colors: Optional[dict[str, str]] = None,
    logos: Optional[dict] = None,
) -> Path:
    """Write a one-page PDF of the recap.

    `story` is the recap without the award line, either as written (with '## Headline' and
    'Pull quote:' lines) or already parsed. `team_colors` overrides colors by team name or id;
    `logos` maps team keys to images (see `fetch_logos`).
    """
    _register_fonts()
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    parts = parse_story(story) if isinstance(story, str) else story
    ctx = _Ctx(summary, assign_team_colors(summary, team_colors), logos or {})

    content: list = []
    if team_of_the_week(summary):
        content += [AwardCard(summary, ctx), Spacer(1, GAP)]
    tiles = _tiles(summary)
    if len(tiles) >= 2:
        content += [StatTiles(tiles, ctx), Spacer(1, 12)]
    content += _scoreboard(summary, ctx)
    content += _recap(parts, ctx)
    if summary.big_games:
        content += [Spacer(1, 6), GameOfTheWeek(summary.big_games[0], ctx), Spacer(1, 14)]
    content += _standings(summary, ctx)

    content_h = sum(f.wrap(CONTENT_W, 10**6)[1] + f.getSpaceBefore() + f.getSpaceAfter() for f in content)
    frame_h = content_h + 0.3 * inch  # slack for rounding in reportlab's layout
    page_h = max(MIN_PAGE_H, HEADER_H + CONTENT_TOP_GAP + frame_h + MARGIN)
    frame_h = page_h - HEADER_H - CONTENT_TOP_GAP - MARGIN
    frame = Frame(MARGIN, MARGIN, CONTENT_W, frame_h,
                  leftPadding=0, rightPadding=0, topPadding=0, bottomPadding=0)
    doc = BaseDocTemplate(
        str(path),
        pagesize=(PAGE_W, page_h),
        leftMargin=MARGIN, rightMargin=MARGIN, topMargin=MARGIN, bottomMargin=MARGIN,
        title=f"{summary.league_name} - Week {summary.week} Recap",
        author=summary.league_name,
        initialFontName="Body",
    )
    doc.addPageTemplates([PageTemplate(id="recap", frames=[frame], onPage=_page_decor(summary, page_h))])
    doc.build(content)
    return path
