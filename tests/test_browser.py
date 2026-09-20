"""Parcours navigateur réels, activés lorsque Playwright et Chromium sont installés."""
import importlib
import importlib.util
import tempfile
import threading
import unittest
from pathlib import Path

import app
from core import Store


PLAYWRIGHT_AVAILABLE = importlib.util.find_spec("playwright") is not None


@unittest.skipUnless(PLAYWRIGHT_AVAILABLE, "Playwright non installé (voir requirements-dev.txt)")
class BrowserJourneyTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.playwright = importlib.import_module("playwright.sync_api").sync_playwright().start()
        try:
            cls.browser = cls.playwright.chromium.launch(headless=True)
        except Exception as exc:
            cls.playwright.stop()
            raise unittest.SkipTest(f"Chromium Playwright indisponible : {exc}") from exc

    @classmethod
    def tearDownClass(cls):
        if hasattr(cls, "browser"):
            cls.browser.close()
        if hasattr(cls, "playwright"):
            cls.playwright.stop()

    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.previous_store, self.previous_data = app.store, app.DATA
        app.DATA = Path(self.temp.name)
        app.store = Store(app.DATA / "browser.sqlite3")
        self.server = app.ThreadingHTTPServer(("127.0.0.1", 0), app.Handler)
        self.thread = threading.Thread(target=self.server.serve_forever, daemon=True)
        self.thread.start()
        self.page = self.browser.new_page(viewport={"width": 1440, "height": 1000})
        self.page.goto(f"http://127.0.0.1:{self.server.server_address[1]}/")

    def tearDown(self):
        self.page.close()
        self.server.shutdown(); self.server.server_close(); self.thread.join()
        app.store, app.DATA = self.previous_store, self.previous_data
        self.temp.cleanup()

    def test_navigation_has_no_design_mockups(self):
        self.assertEqual(self.page.title(), "Carnet Emploi 42")
        self.assertEqual(self.page.locator('nav button[data-page="mockups"]').count(), 0)
        self.page.get_by_role("button", name="Mes CV").click()
        self.assertTrue(self.page.get_by_role("heading", name="Mes CV vérifiés").is_visible())
        self.assertEqual(self.page.locator('#resume-file').get_attribute("accept"), ".pdf,.docx,.odt")
        self.assertEqual(self.page.locator('.skip-link').get_attribute("href"), "#main-content")
        self.assertEqual(self.page.locator('nav').get_attribute("aria-label"), "Navigation principale")
        self.page.keyboard.press("Tab")
        self.assertTrue(self.page.locator(":focus").count())

    def test_offer_can_be_created_from_the_interface(self):
        self.page.get_by_role("button", name="Mes offres").click()
        self.page.locator('#offer-form input[name="title"]').fill("Agent d’accueil")
        self.page.locator('#offer-form input[name="company"]').fill("Entreprise Test")
        self.page.locator("#offer-form button.primary").click()
        self.page.get_by_text("Offre enregistrée").wait_for()
        self.assertTrue(self.page.get_by_text("Agent d’accueil").is_visible())
        self.assertTrue(self.page.get_by_text("Entreprise Test").is_visible())


if __name__ == "__main__":
    unittest.main()
