import sys
import unittest
from pathlib import Path

from playwright.sync_api import TimeoutError, sync_playwright

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "scripts"))
from fetch_fillrates import navigate_to_containers


PORTAL = """
<style>
.mx-templategrid-item { display: contents; }
@media (min-width: 800px) { .phone { display: none; } }
@media (max-width: 799px) { .desktop { display: none; } }
</style>
<main></main>
<script>
function show(stage) {
    const screens = {
        address: '<input placeholder="uw postcode"><input placeholder="uw huisnummer">'
            + '<button onclick="show(\\'select\\')">Volgende</button>',
        select: '<div role="button" onclick="show(\\'info\\')">'
            + 'de Klenckestraat 4J, 9402JK Assen</div>',
        info: '<div role="button" onclick="show(\\'containers\\')">'
            + '<h2>Informatie</h2><p>Afvalkalender</p></div>',
        containers: '<div role="button" onclick="show(\\'done\\')">'
            + '<h2>Ondergrondse containers</h2><p>Waar staan ze ?</p></div>',
        done: '<h1>Container map</h1>'
    };
    document.querySelector('main').innerHTML = ['phone', 'desktop'].map(
        mode => '<section class="' + mode + '"><div class="mx-templategrid-item">'
            + screens[stage] + '</div></section>'
    ).join('');
}
show('address');
</script>
"""


class NavigationTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.pw = sync_playwright().start()
        cls.browser = cls.pw.chromium.launch(headless=True)

    @classmethod
    def tearDownClass(cls):
        cls.browser.close()
        cls.pw.stop()

    def test_responsive_boxless_wrappers(self):
        # The real address fields have one shared form, not responsive copies.
        portal = PORTAL.replace(
            "['phone', 'desktop'].map(",
            "(stage === 'address' ? ['shared'] : ['phone', 'desktop']).map(",
        )
        for width in (1920, 390):
            with self.subTest(width=width):
                page = self.browser.new_page(viewport={"width": width, "height": 1080})
                try:
                    page.route("**/p/assen/landing/", lambda route: route.fulfill(
                        content_type="text/html", body=portal
                    ))
                    navigate_to_containers(page)
                    page.get_by_role("heading", name="Container map").wait_for()
                finally:
                    page.close()

    def test_missing_address_fails_explicitly(self):
        page = self.browser.new_page()
        try:
            page.set_default_timeout(500)
            page.route("**/p/assen/landing/", lambda route: route.fulfill(
                content_type="text/html",
                body='<input placeholder="uw postcode"><input placeholder="uw huisnummer">'
                     '<button>Volgende</button>',
            ))
            with self.assertRaises(TimeoutError):
                navigate_to_containers(page)
        finally:
            page.close()


if __name__ == "__main__":
    unittest.main()
