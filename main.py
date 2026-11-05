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

from client import RusoilClient, clean_fio

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
    version="1.2.0",
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
        "version": "1.2.0",
        "docs": "/docs",
        "upstream": "https://raspisanie.rusoil.net",
    }


@app.get("/health", tags=["Общее"])
async def health():
    return {"status": "ok"}


@app.get("/api/debug", tags=["Диагностика"])
async def debug_test(q: str = "по"):
    """Тестовый вызов к raspisanie.rusoil.net для проверки ответа официального сервера"""
    client = await rusoil.get_client()
    form_data = {"id_fak": "0", "search": q}
    try:
        resp = await client.post("/get_prepod", data=form_data)
        try:
            parsed = resp.json()
        except Exception:
            parsed = None
        return {
            "status": resp.status_code,
            "headers": dict(resp.headers),
            "parsed_type": type(parsed).__name__,
            "prepods_count": len(parsed.get("prepods", [])) if isinstance(parsed, dict) else 0,
            "sample": parsed.get("prepods", [])[:3] if isinstance(parsed, dict) else parsed,
            "raw_preview": resp.text[:400]
        }
    except Exception as e:
        return {"error": str(e)}


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


@app.get("/api/prepods/search", tags=["Преподаватели"], summary="Поиск преподавателей по ФИО")
@app.get("/api/prepod/search", tags=["Преподаватели"], include_in_schema=False)
async def search_prepods(q: str = Query(..., min_length=2, description="ФИО или часть")):
    query_clean = q.strip()
    cache_key = f"prepod_search_{query_clean.lower()}"
    if cache_key in search_cache:
        return search_cache[cache_key]

    results = await rusoil.search_prepod(query_clean)
    search_cache[cache_key] = results
    return results


@app.get("/api/groups/search", tags=["Группы"], summary="Поиск групп по шифру")
@app.get("/api/group/search", tags=["Группы"], include_in_schema=False)
async def search_groups(q: str = Query(..., min_length=2, description="Шифр группы (например: БЦШ02)")):
    query_clean = q.strip()
    cache_key = f"group_search_{query_clean.lower()}"
    if cache_key in search_cache:
        return search_cache[cache_key]

    results = await rusoil.search_groups(query_clean)
    search_cache[cache_key] = results
    return results


@app.get("/api/auditors/search", tags=["Аудитории"], summary="Поиск аудиторий")
@app.get("/api/auditor/search", tags=["Аудитории"], include_in_schema=False)
async def search_auditors(q: str = Query(..., min_length=1, description="Номер аудитории или корпус")):
    query_clean = q.strip()
    cache_key = f"auditor_search_{query_clean.lower()}"
    if cache_key in search_cache:
        return search_cache[cache_key]

    results = await rusoil.search_auditors(query_clean)
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
    name: str = Query(..., description="ФИО преподавателя (например: 'Попов Денис Владимирович')"),
    week: int = Query(10, ge=1, le=30, description="Номер недели"),
):
    """
    Умный поиск расписания преподавателя:
    1. Ищет по фамилии (первое слово) на официальном сервере
    2. Сопоставляет полное ФИО независимо от точек и опечаток
    3. Загружает расписание по найденному kadr_id
    """
    clean_target = clean_fio(name).strip().lower()
    parts = clean_target.split()
    surname = parts[0] if parts else name.strip()

    # Сначала ищем по фамилии
    prepods = await rusoil.search_prepod(surname if len(surname) >= 2 else name)
    if not prepods and len(name.strip()) >= 2:
        prepods = await rusoil.search_prepod(name.strip())

    if not prepods:
        raise HTTPException(status_code=404, detail="Преподаватель не найден в базе УГНТУ")

    # Ищем наиболее точное совпадение
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
    cache_key = f"prepod_rasp_{kadr_id}_{week}_{week}"
    if cache_key in schedule_cache:
        rasp = schedule_cache[cache_key]
    else:
        rasp = await rusoil.get_rasp_prepod(kadr_id, beginweek=week, endweek=week)
        schedule_cache[cache_key] = rasp

    return {
        "prepod": matched,
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
    import os
    import uvicorn

    port = int(os.getenv("PORT", 8000))
    uvicorn.run("main:app", host="0.0.0.0", port=port)
