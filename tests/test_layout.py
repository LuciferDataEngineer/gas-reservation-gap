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
                pills = page.locator(".pill")
                for i in range(pills.count()):
                    pills.nth(i).click()
                    _check(page)
            page.close()
        browser.close()
