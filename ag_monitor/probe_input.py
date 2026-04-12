import asyncio
from playwright.async_api import async_playwright

async def main():
    async with async_playwright() as p:
        b = await p.chromium.connect_over_cdp('http://127.0.0.1:9001')
        main_page = None
        for page in b.contexts[0].pages:
            t = await page.title()
            if "Antigravity" in t and "Launchpad" not in t:
                main_page = page
                break
        if not main_page:
            main_page = b.contexts[0].pages[0]

        html = await main_page.evaluate('document.querySelector("[contenteditable=true]") ? document.querySelector("[contenteditable=true]").outerHTML : "Not found true"')
        html2 = await main_page.evaluate('document.querySelector("[contenteditable=plaintext-only]") ? document.querySelector("[contenteditable=plaintext-only]").outerHTML : "Not found plaintext"')
        print("True:", html[:100])
        print("Plain:", html2[:100])

asyncio.run(main())
