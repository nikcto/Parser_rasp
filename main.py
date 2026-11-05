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
    version="2.0.0",
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
        "version": "2.0.0",
        "upstream": "https://raspisanie.rusoil.net",
    }


@app.get("/health")
async def health():
    return {"status": "ok"}


@app.get("/api/debug")
async def debug_call(q: str = "дил"):
    """Тест поиска через официальный эндпоинт /origins/get_prepod"""
    prepods = await rusoil.search_prepod(q)
    groups = await rusoil.search_groups(q)
    return {
        "query": q,
        "prepods_found": len(prepods),
        "prepods": prepods,
        "groups_found": len(groups),
        "groups": groups,
    }


@app.get("/api/now")
async def get_now_status():
    cache_key = "now_status"
    if cache_key in search_cache:
        return search_cache[cache_key]
    data = await rusoil.get_now()
    search_cache[cache_key] = data
    return data


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


@app.get("/api/groups/search")
@app.get("/api/group/search")
async def search_groups(q: str = Query(..., min_length=2)):
    return await rusoil.search_groups(q.strip())


@app.get("/api/auditors/search")
@app.get("/api/auditor/search")
async def search_auditors(q: str = Query(..., min_length=1)):
    return await rusoil.search_auditors(q.strip())


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


@app.get("/api/group/{id_group}/schedule")
async def get_group_schedule(id_group: str, week: int = Query(10), end_week: Optional[int] = Query(None)):
    final_end = end_week if end_week is not None else week
    return await rusoil.get_rasp_group(id_group, beginweek=week, endweek=final_end)


@app.get("/api/auditor/{id_aud}/schedule")
async def get_auditor_schedule(id_aud: str, week: int = Query(10), end_week: Optional[int] = Query(None)):
    final_end = end_week if end_week is not None else week
    return await rusoil.get_rasp_auditor(id_aud, beginweek=week, endweek=final_end)


if __name__ == "__main__":
    import os
    import uvicorn
    port = int(os.getenv("PORT", 8000))
    uvicorn.run("main:app", host="0.0.0.0", port=port)
