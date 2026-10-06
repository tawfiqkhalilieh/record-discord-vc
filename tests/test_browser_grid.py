from pathlib import Path

from playwright.sync_api import sync_playwright


def test_live_grid_reacts_to_roster_video_and_names():
    script = Path("capture/grid.js").read_text()
    with sync_playwright() as playwright:
        browser = playwright.chromium.launch(headless=True)
        page = browser.new_page(viewport={"width": 1280, "height": 720}, bypass_csp=True)
        page.set_content('<meta http-equiv="Content-Security-Policy" content="script-src \'self\'"><div id="call"></div>')
        stats = page.evaluate(script, {"width": 1280, "height": 720, "videoSelector": "video", "nameSelector": "",
                                    "participants": [{"id": "1", "name": "Alex"}]})
        assert stats["tileCount"] == 1 and stats["videoCount"] == 0
        page.evaluate("""async () => {
          const source = document.createElement('canvas'); source.width=320; source.height=180;
          source.getContext('2d').fillRect(0,0,320,180);
          const video = document.createElement('video'); video.setAttribute('aria-label', 'Sam');
          video.muted = true; video.autoplay = true; video.srcObject = source.captureStream(15);
          document.getElementById('call').appendChild(video); await video.play();
          window.__vcStash.setParticipants([{id:'1',name:'Alex'},{id:'2',name:'Sam'}]);
        }""")
        page.wait_for_function("window.__vcStash.stats.videoCount === 1")
        stats = page.evaluate("window.__vcStash.stats")
        assert stats["tileCount"] == 2 and set(stats["names"]) == {"Alex", "Sam"}
        page.evaluate("window.__vcStash.setParticipants([{id:'2',name:'Sam'}])")
        assert page.evaluate("window.__vcStash.stats.tileCount") == 1
        page.evaluate("window.__vcStash.stop()")
        assert page.locator("#vc-stash-grid").count() == 0
        browser.close()
