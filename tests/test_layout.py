import re
import subprocess
import sys
import time
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]
SITE = ROOT / "site"
PAGES = ("index.html", "exports.html", "storage.html", "prices.html")

pytestmark = pytest.mark.browser


@pytest.fixture(scope="module")
def server():
    proc = subprocess.Popen(
        [sys.executable, "-m", "http.server", "8765", "--bind", "127.0.0.1"],
        cwd=SITE,
        stdout=subprocess.DEVNULL,
        stderr=subprocess.DEVNULL,
    )
    time.sleep(0.4)
    yield "http://127.0.0.1:8765"
    proc.terminate()
    proc.wait(timeout=10)


def _overlaps(boxes, gap=1):
    hits = []
    for i, a in enumerate(boxes):
        for b in boxes[i + 1 :]:
            dx = min(a["right"], b["right"]) - max(a["left"], b["left"])
            dy = min(a["bottom"], b["bottom"]) - max(a["top"], b["top"])
            if dx > gap and dy > gap:
                hits.append((a["text"], b["text"]))
    return hits


def _check(page):
    boxes = page.evaluate(
        """() => {
          const svg = document.querySelector('.pane.is-shown svg');
          const frame = svg.getBoundingClientRect();
          return [...svg.querySelectorAll('text')].map((node) => {
            const box = node.getBoundingClientRect();
            return {
              text: node.textContent,
              left: box.left, right: box.right, top: box.top, bottom: box.bottom,
              frameLeft: frame.left, frameRight: frame.right,
              frameTop: frame.top, frameBottom: frame.bottom
            };
          });
        }"""
    )
    outside = [
        item["text"]
        for item in boxes
        if item["left"] < item["frameLeft"] - 1
        or item["right"] > item["frameRight"] + 1
        or item["top"] < item["frameTop"] - 1
        or item["bottom"] > item["frameBottom"] + 1
    ]
    assert not outside, outside
    assert not _overlaps(boxes), _overlaps(boxes)
    clipped = page.evaluate(
        """() => {
          const width = window.innerWidth;
          const bad = [];
          for (const node of document.querySelectorAll('h1, .take, .k, .n')) {
            const box = node.getBoundingClientRect();
            if (box.left < -1 || box.right > width + 1) bad.push(node.textContent);
            if (node.classList.contains('n') && node.scrollWidth > node.clientWidth + 1) {
              bad.push(node.textContent);
            }
          }
          return bad;
        }"""
    )
    assert not clipped, clipped
    _check_line_labels(page)


_AXIS_QUARTER = re.compile(r"^Q([1-4]) (\d{2})$")
_PROSE_QUARTER = re.compile(r"Q([1-4]) (20\d{2})")
_ONE_DECIMAL = re.compile(r"(?<![\d,.])(\d{1,3}(?:,\d{3})*\.\d)(?!\d)")


def _quarter_key(quarter: str, year: int) -> int:
    return year * 4 + int(quarter)


def _check_quarter_order(page):
    found = page.evaluate(
        """() => {
          const card = [...document.querySelectorAll('.card')].find((node) =>
            (node.querySelector('h2')?.textContent || '').includes('transaction price')
          );
          if (!card) return null;
          return {
            axis: [...card.querySelectorAll('svg text.axis')].map((node) => node.textContent.trim()),
            exact: card.querySelector('.exact')?.textContent || '',
            gaps: card.querySelectorAll('svg .gap').length,
          };
        }"""
    )
    on_prices = page.url.endswith("prices.html")
    if found is None:
        assert not on_prices, "quarterly price chart missing"
        return
    axis = []
    for text in found["axis"]:
        match = _AXIS_QUARTER.match(text)
        if match:
            axis.append(_quarter_key(match.group(1), 2000 + int(match.group(2))))
    assert len(axis) >= 4, found["axis"]
    assert axis == sorted(axis) and all(b > a for a, b in zip(axis, axis[1:])), found["axis"]
    prose = [
        _quarter_key(quarter, int(year)) for quarter, year in _PROSE_QUARTER.findall(found["exact"])
    ]
    # The closing note may name the latest quarter again. Order still has to be chronological.
    assert prose == sorted(prose), found["exact"]
    assert prose, found["exact"]
    if "not published" in found["exact"]:
        assert found["gaps"] >= 1


def _check_line_labels(page):
    hits = page.evaluate(
        """() => {
          function hitsRect(box, x1, y1, x2, y2, pad) {
            const left = box.left - pad, right = box.right + pad;
            const top = box.top - pad, bottom = box.bottom + pad;
            const inside = (x, y) => x >= left && x <= right && y >= top && y <= bottom;
            if (inside(x1, y1) || inside(x2, y2)) return true;
            function cross(ax, ay, bx, by, cx, cy, dx, dy) {
              const d = (bx - ax) * (dy - cy) - (by - ay) * (dx - cx);
              if (d === 0) return false;
              const t = ((cx - ax) * (dy - cy) - (cy - ay) * (dx - cx)) / d;
              const u = ((cx - ax) * (by - ay) - (cy - ay) * (bx - ax)) / d;
              return t >= 0 && t <= 1 && u >= 0 && u <= 1;
            }
            return cross(x1, y1, x2, y2, left, top, right, top)
              || cross(x1, y1, x2, y2, right, top, right, bottom)
              || cross(x1, y1, x2, y2, right, bottom, left, bottom)
              || cross(x1, y1, x2, y2, left, bottom, left, top);
          }
          const hits = [];
          for (const svg of document.querySelectorAll('svg.chart')) {
            const frame = svg.getBoundingClientRect();
            if (frame.width < 2 || !svg.getScreenCTM()) continue;
            const scale = frame.width / (svg.viewBox.baseVal.width || frame.width);
            for (const line of svg.querySelectorAll('polyline')) {
              const raw = line.getAttribute('points').trim().split(/[\\s,]+/).map(Number);
              const pad = (parseFloat(line.getAttribute('stroke-width') || '3') / 2) * scale;
              const pts = [];
              for (let i = 0; i < raw.length; i += 2) {
                const point = svg.createSVGPoint();
                point.x = raw[i];
                point.y = raw[i + 1];
                pts.push(point.matrixTransform(svg.getScreenCTM()));
              }
              for (const label of svg.querySelectorAll('text.bar-label')) {
                const box = label.getBoundingClientRect();
                for (let i = 0; i < pts.length - 1; i++) {
                  if (hitsRect(box, pts[i].x, pts[i].y, pts[i + 1].x, pts[i + 1].y, pad)) {
                    hits.push(label.textContent);
                    break;
                  }
                }
              }
            }
          }
          return hits;
        }"""
    )
    assert not hits, hits


def _check_kpi_end_label(page):
    found = page.evaluate(
        """() => {
          const card = [...document.querySelectorAll('.kpi')].find((node) =>
            (node.querySelector('.k')?.textContent || '').trim() === 'Iona now'
          );
          const svg = [...document.querySelectorAll('svg.chart')].find((node) =>
            node.querySelector('polyline') && node.getBoundingClientRect().width > 2
          );
          if (!card || !svg) return null;
          const unit = card.querySelector('.n span')?.textContent || '';
          return {
            kpi: card.querySelector('.n').textContent.replace(unit, '').trim(),
            label: svg.querySelector('text.bar-label')?.textContent || '',
          };
        }"""
    )
    if found is None:
        assert not page.url.endswith("storage.html"), "Iona end label or KPI missing"
        return
    match = _ONE_DECIMAL.search(found["label"])
    assert match, found
    assert match.group(1) == found["kpi"], found


def test_desktop_and_phone(server):
    from playwright.sync_api import sync_playwright

    with sync_playwright() as pw:
        browser = pw.chromium.launch()
        for width, height in ((1280, 800), (390, 844)):
            page = browser.new_page(viewport={"width": width, "height": height})
            for name in PAGES:
                page.goto(f"{server}/{name}", wait_until="networkidle")
                if width == 1280:
                    tops = page.eval_on_selector_all(
                        ".kpi", "nodes => nodes.map(n => n.getBoundingClientRect().top)"
                    )
                    assert max(tops) - min(tops) < 2, tops
                else:
                    overflow = page.evaluate(
                        "document.documentElement.scrollWidth > window.innerWidth + 1"
                    )
                    assert not overflow
                _check(page)
                _check_quarter_order(page)
                _check_kpi_end_label(page)
                pills = page.locator(".pill")
                for i in range(pills.count()):
                    pills.nth(i).click()
                    _check(page)
            page.close()
        browser.close()
