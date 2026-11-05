"""
FastAPI прокси-сервер и парсер расписания УГНТУ (raspisanie.rusoil.net)
"""
import asyncio
import re
from contextlib import asynccontextmanager
from typing import Any, Dict, List, Optional
from cachetools import TTLCache
from fastapi import FastAPI, HTTPException, Query
from fastapi.middleware.cors import CORSMiddleware
import httpx

from client import RusoilClient, clean_fio, BASE_URL, DEFAULT_HEADERS

search_cache = TTLCache(maxsize=1000, ttl=3600)
schedule_cache = TTLCache(maxsize=500, ttl=1800)

rusoil = RusoilClient()


@asynccontextmanager
async def lifespan(app: FastAPI):
    yield
    await rusoil.close()


app = FastAPI(
    title="USPTU Schedule API & Proxy",
    version="1.4.0",
    lifespan=lifespan,
)

app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)


@app.get("/")
async def root():
    return {
        "status": "online",
        "service": "USPTU Schedule Proxy API",
        "version": "1.4.0",
    }


@app.get("/health")
async def health():
    return {"status": "ok"}


@app.get("/api/inspect-frontend")
async def inspect_frontend():
    async with httpx.AsyncClient(base_url=BASE_URL, headers=DEFAULT_HEADERS, timeout=15.0, follow_redirects=True) as client:
        try:
            r = await client.get("/")
            html = r.text
            scripts = re.findall(r'<script[^>]+src=["\']([^"\']+)["\']', html)
            
            js_findings = []
            for s in scripts:
                script_url = s if s.startswith("http") else f"{BASE_URL}/{s.lstrip('./')}"
                try:
                    js_res = await client.get(script_url)
                    js_code = js_res.text
                    
                    # 1. Находим определение O
                    # Ищем "var O=", "let O=", "const O=", "O="
                    o_defs = re.findall(r'(\b[a-zA-Z0-9_$]+\s*=\s*["\'][^"\']*["\'])', js_code[:20000])
                    # Ищем где создается M() или axios
                    m_defs = re.findall(r'(\b[a-zA-Z0-9_$]+\.create\([^)]+\))', js_code)
                    
                    # Ищем точное место перед M().post(O+"/get_prepod"
                    idx = js_code.find('"/get_prepod"')
                    sub_context = js_code[max(0, idx - 500):min(len(js_code), idx + 200)] if idx != -1 else ""
                    
                    # Ищем строки похожие на http:// или https:// или порт или домен
                    urls = re.findall(r'["\'](https?://[^"\']+|/[^"\']+)["\']', js_code)
                    
                    js_findings.append({
                        "url": script_url,
                        "sub_context": sub_context,
                        "axios_create": m_defs[:5],
                        "urls_in_code": [u for u in set(urls) if any(k in u for k in ['rusoil', 'api', 'rasp', '80', 'port', 'get_'])]
                    })
                except Exception as e:
                    js_findings.append({"url": script_url, "error": str(e)})

            return {
                "js_analysis": js_findings
            }
        except Exception as e:
            return {"error": str(e)}


@app.get("/api/debug")
async def debug_all(q: str = "дил"):
    results = {}
    async with httpx.AsyncClient(base_url=BASE_URL, headers=DEFAULT_HEADERS, timeout=10.0, follow_redirects=True) as client:
        # Тестируем разные варианты путей
        endpoints = ["/get_prepod", "get_prepod", "/api/get_prepod", "/get_prepod/"]
        
        for ep in endpoints:
            # 1. JSON
            try:
                r = await client.post(ep, json={"id_fak": 0, "search": q})
                results[f"{ep}_json"] = {
                    "status": r.status_code,
                    "ct": r.headers.get("content-type"),
                    "is_html": "<!doctype" in r.text.lower(),
                    "preview": r.text[:150]
                }
            except Exception as e:
                results[f"{ep}_json"] = {"error": str(e)}

            # 2. Form data
            try:
                r = await client.post(ep, data={"id_fak": "0", "search": q})
                results[f"{ep}_form"] = {
                    "status": r.status_code,
                    "ct": r.headers.get("content-type"),
                    "is_html": "<!doctype" in r.text.lower(),
                    "preview": r.text[:150]
                }
            except Exception as e:
                results[f"{ep}_form"] = {"error": str(e)}

    return results


@app.get("/api/search")
async def global_search(q: str = Query(..., min_length=2)):
    query_clean = q.strip()
    cache_key = f"global_search_{query_clean.lower()}"
    if cache_key in search_cache:
        return search_cache[cache_key]

    prepods_task = rusoil.search_prepod(query_clean)
    groups_task = rusoil.search_groups(query_clean)
    auditors_task = rusoil.search_auditors(query_clean)

    prepods, groups, auditors = await asyncio.gather(
        prepods_task, groups_task, auditors_task, return_exceptions=True
    )

    result = {
        "query": query_clean,
        "prepods": prepods if isinstance(prepods, list) else [],
        "groups": groups if isinstance(groups, list) else [],
        "auditors": auditors if isinstance(auditors, list) else [],
    }
    search_cache[cache_key] = result
    return result


@app.get("/api/prepods/search")
@app.get("/api/prepod/search")
async def search_prepods(q: str = Query(..., min_length=2)):
    return await rusoil.search_prepod(q.strip())


@app.get("/api/prepod/by-name")
async def get_prepod_schedule_by_name(name: str = Query(...), week: int = Query(10)):
    clean_target = clean_fio(name).strip().lower()
    parts = clean_target.split()
    surname = parts[0] if parts else name.strip()

    prepods = await rusoil.search_prepod(surname if len(surname) >= 2 else name)
    if not prepods and len(name.strip()) >= 2:
        prepods = await rusoil.search_prepod(name.strip())

    if not prepods:
        raise HTTPException(status_code=404, detail="Преподаватель не найден")

    matched = None
    for p in prepods:
        pfio = p["fio"].lower()
        if clean_target in pfio or pfio in clean_target:
            matched = p
            break
        if len(parts) >= 2 and parts[0] in pfio and parts[1] in pfio:
            matched = p
            break

    if not matched:
        matched = prepods[0]

    kadr_id = matched["id_prepod"]
    rasp = await rusoil.get_rasp_prepod(kadr_id, beginweek=week, endweek=week)
    return {
        "prepod": matched,
        "week": week,
        "schedule": rasp,
    }


if __name__ == "__main__":
    import os
    import uvicorn
    port = int(os.getenv("PORT", 8000))
    uvicorn.run("main:app", host="0.0.0.0", port=port)
