"""
FastAPI прокси-сервер и парсер расписания УГНТУ (raspisanie.rusoil.net)
"""
import asyncio
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
    version="1.3.0",
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
        "version": "1.3.0",
    }


@app.get("/health")
async def health():
    return {"status": "ok"}


@app.get("/api/debug")
async def debug_all(q: str = "дил"):
    """
    Глубокая диагностика взаимодействия с raspisanie.rusoil.net:
    Тестирует отправку с сессионными cookies, JSON vs Form Data, и возвращает полные логи.
    """
    results = {}
    
    # Создаем изолированный клиент с поддержкой cookies
    async with httpx.AsyncClient(base_url=BASE_URL, headers=DEFAULT_HEADERS, timeout=10.0, follow_redirects=True) as client:
        # 0. Заходим на главную страницу, получаем cookies
        try:
            r_main = await client.get("/")
            results["main_status"] = r_main.status_code
            results["cookies"] = dict(client.cookies)
        except Exception as e:
            results["main_error"] = str(e)

        # 1. POST /get_prepod с JSON {"id_fak": 0, "search": q}
        try:
            r1 = await client.post("/get_prepod", json={"id_fak": 0, "search": q})
            results["json_test"] = {
                "status": r1.status_code,
                "content_type": r1.headers.get("content-type", ""),
                "text_preview": r1.text[:300],
            }
            try:
                j = r1.json()
                results["json_test"]["prepods_count"] = len(j.get("prepods", [])) if isinstance(j, dict) else 0
                results["json_test"]["sample"] = j.get("prepods", [])[:3] if isinstance(j, dict) else j
            except Exception as je:
                results["json_test"]["json_parse_err"] = str(je)
        except Exception as e:
            results["json_test"] = {"error": str(e)}

        # 2. POST /get_prepod с Form Data id_fak=0&search=q
        try:
            r2 = await client.post("/get_prepod", data={"id_fak": "0", "search": q})
            results["form_test"] = {
                "status": r2.status_code,
                "content_type": r2.headers.get("content-type", ""),
                "text_preview": r2.text[:300],
            }
            try:
                j = r2.json()
                results["form_test"]["prepods_count"] = len(j.get("prepods", [])) if isinstance(j, dict) else 0
                results["form_test"]["sample"] = j.get("prepods", [])[:3] if isinstance(j, dict) else j
            except Exception as je:
                results["form_test"]["json_parse_err"] = str(je)
        except Exception as e:
            results["form_test"] = {"error": str(e)}

        # 3. POST /get_groups с JSON
        try:
            r3 = await client.post("/get_groups", json={"id_fak": 0, "search": q})
            results["groups_json"] = {
                "status": r3.status_code,
                "text_preview": r3.text[:200]
            }
        except Exception as e:
            results["groups_json"] = {"error": str(e)}

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


@app.get("/api/prepod/{kadr_id}/schedule")
async def get_prepod_schedule(kadr_id: str, week: int = Query(10), end_week: Optional[int] = Query(None)):
    final_end = end_week if end_week is not None else week
    return await rusoil.get_rasp_prepod(kadr_id, beginweek=week, endweek=final_end)


if __name__ == "__main__":
    import os
    import uvicorn
    port = int(os.getenv("PORT", 8000))
    uvicorn.run("main:app", host="0.0.0.0", port=port)
