import json
import os
from pathlib import Path
import time
import pytest
from playwright.sync_api import sync_playwright, expect


@pytest.fixture(scope="session")
def origin():
    value=json.loads((Path(os.environ["MODULE14_EVIDENCE_DIR"])/"browser-origin.json").read_text())
    assert value["offline"] is True and value["origin"].startswith("http://")
    return value["origin"]


@pytest.fixture(scope="session")
def browser():
    expect.set_options(timeout=30000)
    with sync_playwright() as runtime:
        instance=runtime.chromium.launch(headless=True,args=["--no-sandbox","--disable-dev-shm-usage"])
        try: yield instance
        finally: instance.close()


@pytest.fixture
def page(browser,origin):
    context=browser.new_context(accept_downloads=True)
    page=context.new_page();page.set_default_timeout(30000)
    failures=[];page.on("pageerror",lambda error:failures.append(type(error).__name__))
    try: yield page
    finally:
        context.close()
        assert not failures,failures
