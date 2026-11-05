"""
FastAPI прокси-сервер и парсер расписания УГНТУ (raspisanie.rusoil.net)
Разворачивается на российском сервере для обхода геоблокировок Telegram Serverless и внешних клиентов.
"""
import asyncio
from contextlib import asynccontextmanager
from typing import Any, Dict, List, Optional
from cachetools import TTLCache
from fastapi import FastAPI, HTTPException, Query
from fastapi.middleware.cors import CORSMiddleware

from client import RusoilClient

# Кэш: результаты поиска храним 1 час, расписание 30 минут
search_cache = TTLCache(maxsize=1000, ttl=3600)
schedule_cache = TTLCache(maxsize=500, ttl=1800)

rusoil = RusoilClient()


@asynccontextmanager
async def lifespan(app: FastAPI):
    yield
    await rusoil.close()


app = FastAPI(
    title="USPTU Schedule API & Proxy",
    description="Проксирующий REST API для официального расписания УГНТУ (raspisanie.rusoil.net)",
    version="1.0.0",
    lifespan=lifespan,
)

# Разрешаем CORS для любых источников (включая Telegram Mini Apps и localhost)
app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)


@app.get("/", tags=["Общее"])
async def root():
    return {
        "status": "online",
        "service": "USPTU Schedule Proxy API",
        "docs": "/docs",
        "upstream": "https://raspisanie.rusoil.net",
    }


@app.get("/health", tags=["Общее"])
async def health():
    return {"status": "ok"}


@app.get("/api/now", tags=["Общее"], summary="Текущая учебная неделя и статус")
async def get_now_status():
    cache_key = "now_status"
    if cache_key in search_cache:
        return search_cache[cache_key]
    data = await rusoil.get_now()
    search_cache[cache_key] = data
    return data


@app.get("/api/search", tags=["Поиск"], summary="Универсальный поиск (преподаватели, группы, аудитории)")
async def global_search(
    q: str = Query(..., min_length=2, description="Поисковый запрос (например: 'Попов', 'БЦШ', '432')")
):
    """Параллельно ищет в базе преподавателей, групп и аудиторий"""
    cache_key = f"global_search_{q.strip().lower()}"
    if cache_key in search_cache:
        return search_cache[cache_key]

    prepods_task = rusoil.search_prepod(q)
    groups_task = rusoil.search_groups(q)
    auditors_task = rusoil.search_auditors(q)

    prepods, groups, auditors = await asyncio.gather(
        prepods_task, groups_task, auditors_task, return_exceptions=True
    )

    result = {
        "query": q,
        "prepods": prepods if isinstance(prepods, list) else [],
        "groups": groups if isinstance(groups, list) else [],
        "auditors": auditors if isinstance(auditors, list) else [],
    }
    search_cache[cache_key] = result
    return result


@app.get("/api/prepods/search", tags=["Преподаватели"], summary="Поиск преподавателей по ФИО")
async def search_prepods(q: str = Query(..., min_length=2, description="ФИО или часть")):
    cache_key = f"prepod_search_{q.strip().lower()}"
    if cache_key in search_cache:
        return search_cache[cache_key]

    results = await rusoil.search_prepod(q)
    search_cache[cache_key] = results
    return results


@app.get("/api/groups/search", tags=["Группы"], summary="Поиск групп по шифру")
async def search_groups(q: str = Query(..., min_length=2, description="Шифр группы (например: БЦШ02)")):
    cache_key = f"group_search_{q.strip().lower()}"
    if cache_key in search_cache:
        return search_cache[cache_key]

    results = await rusoil.search_groups(q)
    search_cache[cache_key] = results
    return results


@app.get("/api/auditors/search", tags=["Аудитории"], summary="Поиск аудиторий")
async def search_auditors(q: str = Query(..., min_length=1, description="Номер аудитории или корпус")):
    cache_key = f"auditor_search_{q.strip().lower()}"
    if cache_key in search_cache:
        return search_cache[cache_key]

    results = await rusoil.search_auditors(q)
    search_cache[cache_key] = results
    return results


@app.get("/api/prepod/{kadr_id}/schedule", tags=["Преподаватели"], summary="Расписание преподавателя по ID")
async def get_prepod_schedule(
    kadr_id: str,
    week: int = Query(10, ge=1, le=30, description="Номер учебной недели"),
    end_week: Optional[int] = Query(None, description="Конечная неделя интервала (по умолчанию равна week)"),
):
    final_end_week = end_week if end_week is not None else week
    cache_key = f"prepod_rasp_{kadr_id}_{week}_{final_end_week}"

    if cache_key in schedule_cache:
        return schedule_cache[cache_key]

    rasp = await rusoil.get_rasp_prepod(kadr_id, beginweek=week, endweek=final_end_week)
    schedule_cache[cache_key] = rasp
    return rasp


@app.get("/api/prepod/by-name", tags=["Преподаватели"], summary="Быстрый поиск расписания преподавателя по имени")
async def get_prepod_schedule_by_name(
    name: str = Query(..., description="ФИО преподавателя (например: 'Попов Денис')"),
    week: int = Query(10, ge=1, le=30, description="Номер недели"),
):
    """Удобный эндпоинт в один шаг: ищет преподавателя по имени и сразу возвращает его расписание"""
    prepods = await rusoil.search_prepod(name)
    if not prepods:
        raise HTTPException(status_code=404, detail="Преподаватель не найден")

    first_prepod = prepods[0]
    kadr_id = first_prepod["id_prepod"]

    rasp = await rusoil.get_rasp_prepod(kadr_id, beginweek=week, endweek=week)
    return {
        "prepod": first_prepod,
        "week": week,
        "schedule": rasp,
    }


@app.get("/api/group/{id_group}/schedule", tags=["Группы"], summary="Расписание группы по ID")
async def get_group_schedule(
    id_group: str,
    week: int = Query(10, ge=1, le=30, description="Номер учебной недели"),
    end_week: Optional[int] = Query(None, description="Конечная неделя"),
):
    final_end_week = end_week if end_week is not None else week
    cache_key = f"group_rasp_{id_group}_{week}_{final_end_week}"

    if cache_key in schedule_cache:
        return schedule_cache[cache_key]

    rasp = await rusoil.get_rasp_group(id_group, beginweek=week, endweek=final_end_week)
    schedule_cache[cache_key] = rasp
    return rasp


@app.get("/api/auditor/{id_aud}/schedule", tags=["Аудитории"], summary="Расписание аудитории по ID")
async def get_auditor_schedule(
    id_aud: str,
    week: int = Query(10, ge=1, le=30, description="Номер учебной недели"),
    end_week: Optional[int] = Query(None, description="Конечная неделя"),
):
    final_end_week = end_week if end_week is not None else week
    cache_key = f"aud_rasp_{id_aud}_{week}_{final_end_week}"

    if cache_key in schedule_cache:
        return schedule_cache[cache_key]

    rasp = await rusoil.get_rasp_auditor(id_aud, beginweek=week, endweek=final_end_week)
    schedule_cache[cache_key] = rasp
    return rasp


if __name__ == "__main__":
    import uvicorn
    uvicorn.run("main:app", host="0.0.0.0", port=8000, reload=True)
