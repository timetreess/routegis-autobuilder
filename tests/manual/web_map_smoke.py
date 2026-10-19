"""Run with: uv run --no-project --with playwright tests/manual/web_map_smoke.py map.html"""
from __future__ import annotations

import argparse
import shutil
import tempfile
from pathlib import Path

from playwright.sync_api import sync_playwright


def main():
    parser = argparse.ArgumentParser(description="Verify a standalone RouteGIS HTML map offline.")
    parser.add_argument("html", type=Path)
    parser.add_argument("--channel", default="chromium")
    parser.add_argument("--screenshot", type=Path)
    args = parser.parse_args()
    approved = Path(tempfile.gettempdir()) / "opencode"
    with tempfile.TemporaryDirectory(prefix="routegis-web-smoke-", dir=approved if approved.is_dir() else None) as directory, sync_playwright() as playwright:
        # Deliberately remove access to the original project, scripts and data.
        html = Path(directory) / args.html.name
        shutil.copy2(args.html, html)
        browser = playwright.chromium.launch(channel=args.channel, headless=True)
        context = browser.new_context(viewport={"width": 1360, "height": 900}, offline=True)
        page = context.new_page()
        errors, requests = [], []
        page.on("pageerror", lambda error: errors.append(str(error)))
        page.on("request", lambda request: requests.append(request.url) if request.url.startswith(("http:", "https:")) else None)
        page.goto(html.as_uri())
        page.wait_for_selector('#load-status[data-state="ready"]', timeout=120000)
        # The map opens on the VWorld general map; turning it off must leave it offline.
        assert page.locator("#basemap").input_value() == "street"
        page.wait_for_timeout(500)
        assert requests and all(url.startswith("https://xdworld.vworld.kr/2d/Base/") for url in requests), requests
        page.select_option("#basemap", "none")
        page.wait_for_timeout(500)
        baseline = len(requests)
        assert page.evaluate("config.layers.filter(item => item.data).every(item => overlays.get(item.id).getLayers().length === item.count)")
        entry = page.evaluate("config.layers.find(item => item.data && item.count)")
        assert entry, "Smoke input must contain at least one feature"
        checkbox = page.locator('input[data-layer="' + entry["id"] + '"]')
        checkbox.uncheck()
        assert not page.evaluate("id => map.hasLayer(overlays.get(id))", entry["id"])
        checkbox.check()
        assert page.evaluate("id => map.hasLayer(overlays.get(id))", entry["id"])

        zoom = page.evaluate("map.getZoom()")
        page.locator(".leaflet-control-zoom-in").click()
        page.wait_for_function("zoom => map.getZoom() > zoom", arg=zoom)
        page.wait_for_function("!map._animatingZoom")
        box = page.locator("#map").bounding_box()
        center = page.evaluate("[map.getCenter().lat, map.getCenter().lng]")
        page.mouse.move(box["x"] + box["width"] / 2, box["y"] + box["height"] / 2)
        page.mouse.down()
        page.mouse.move(box["x"] + box["width"] / 2 + 90, box["y"] + box["height"] / 2 + 30, steps=8)
        page.mouse.up()
        page.wait_for_function("before => Math.abs(map.getCenter().lng - before[1]) > 1e-7", arg=center)
        page.locator("#fit-map").click()
        page.wait_for_function("map.getBounds().contains(bounds)")
        page.wait_for_function("!map._animatingZoom && (!map._panAnim || !map._panAnim._inProgress)")

        # Exercise the actual Leaflet click listener and lazy popup DOM builder.
        page.evaluate("""id => {
            let layer = overlays.get(id).getLayers()[0];
            while (layer.getLayers) layer = layer.getLayers()[0];
            const latlng = layer.getLatLng ? layer.getLatLng() : layer.getBounds().getCenter();
            layer.fire('click', {latlng}, true);
        }""", entry["id"])
        page.wait_for_selector(".popup-content")
        assert not page.locator(".popup-content img, .popup-content script").count()
        assert page.evaluate("typeof window.pwned") == "undefined"
        assert all(page.locator('input[data-layer="' + item["id"] + '"]').is_disabled()
                   for item in page.evaluate("config.layers.filter(item => item.status !== 'success' || !item.count)"))
        assert len(requests) == baseline, requests[baseline:]
        assert not errors, errors
        page.locator(".leaflet-popup-close-button").click()
        page.evaluate("() => new Promise(resolve => requestAnimationFrame(() => requestAnimationFrame(resolve)))")
        if args.screenshot:
            args.screenshot.parent.mkdir(parents=True, exist_ok=True)
            page.screenshot(path=str(args.screenshot))
        total = page.evaluate("config.layers.reduce((sum, item) => sum + item.count, 0)")
        print(f"Standalone HTML smoke passed: {total:,} features, zoom/pan/layer toggles/popups, "
              "VWorld basemap on by default, zero requests once switched off")
        browser.close()


if __name__ == "__main__":
    main()
