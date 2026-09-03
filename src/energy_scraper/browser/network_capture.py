from __future__ import annotations

import asyncio
import json
from pathlib import Path
from urllib.parse import parse_qsl, urlparse

BLOCKED=("google-analytics","doubleclick","facebook","hotjar","consent")


async def capture(url:str,output:Path,browser_profile:Path|None=None,wait_seconds:int=12)->list[dict]:
    from playwright.async_api import async_playwright
    report=[]; output.mkdir(parents=True,exist_ok=True)
    async with async_playwright() as pw:
        if browser_profile:
            context=await pw.chromium.launch_persistent_context(str(browser_profile),headless=False); browser=None
        else:
            browser=await pw.chromium.launch(headless=True); context=await browser.new_context()
        page=await context.new_page()
        async def response_handler(response):
            request=response.request
            if request.resource_type not in {"xhr","fetch"} or any(x in response.url for x in BLOCKED): return
            content_type=response.headers.get("content-type",""); body=b""; keys=[]
            try:
                body=await response.body()
                if "json" in content_type:
                    value=json.loads(body); keys=list(value)[:30] if isinstance(value,dict) else []
                    (output/f"response_{len(report):04d}.json").write_bytes(body)
            except Exception: pass
            parsed=urlparse(response.url)
            report.append({"url":response.url,"method":request.method,"status":response.status,"mime_type":content_type,
              "response_size":len(body),"query_parameters":dict(parse_qsl(parsed.query)),"sample_json_keys":keys})
        page.on("response",response_handler)
        await page.goto(url,wait_until="domcontentloaded",timeout=60000); await page.wait_for_timeout(wait_seconds*1000)
        await context.close()
        if browser: await browser.close()
    (output/"network_report.json").write_text(json.dumps(report,indent=2,ensure_ascii=False),encoding="utf-8")
    return report


def run_capture(*args,**kwargs): return asyncio.run(capture(*args,**kwargs))
