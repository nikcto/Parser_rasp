"""
Асинхронный клиент для взаимодействия с официальным API расписания УГНТУ (raspisanie.rusoil.net)
"""
import re
from typing import Any, Dict, List, Optional
import httpx

BASE_URL = "https://raspisanie.rusoil.net"

DEFAULT_HEADERS = {
    "User-Agent": (
        "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
        "AppleWebKit/537.36 (KHTML, like Gecko) "
        "Chrome/124.0.0.0 Safari/537.36"
    ),
    "Accept": "application/json, text/plain, */*",
    "Accept-Language": "ru-RU,ru;q=0.9,en-US;q=0.8,en;q=0.7",
    "Origin": BASE_URL,
    "Referer": f"{BASE_URL}/",
    "Content-Type": "application/json",
}


def clean_fio(fio: str) -> str:
    """Очищает ФИО от лишних точек, например 'Попов Денис.Владимирович.' -> 'Попов Денис Владимирович'"""
    if not fio:
        return ""
    cleaned = fio.replace(".", " ")
    return re.sub(r"\s+", " ", cleaned).strip()


class RusoilClient:
    def __init__(self, timeout: float = 12.0):
        self.timeout = timeout
        self._client: Optional[httpx.AsyncClient] = None

    async def get_client(self) -> httpx.AsyncClient:
        if self._client is None or self._client.is_closed:
            self._client = httpx.AsyncClient(
                base_url=BASE_URL,
                headers=DEFAULT_HEADERS,
                timeout=self.timeout,
                verify=True,
                follow_redirects=True,
            )
        return self._client

    async def close(self):
        if self._client and not self._client.is_closed:
            await self._client.aclose()

    async def _post_json(self, endpoint: str, payload: Dict[str, Any]) -> Any:
        client = await self.get_client()
        try:
            response = await client.post(endpoint, json=payload)
            response.raise_for_status()
            return response.json()
        except httpx.HTTPStatusError as e:
            return {"error": f"HTTP {e.response.status_code}", "detail": str(e)}
        except Exception as e:
            return {"error": "Connection error", "detail": str(e)}

    async def get_now(self) -> Dict[str, Any]:
        """Получает текущую неделю и статус семестра"""
        client = await self.get_client()
        try:
            resp = await client.post("/get_now?nocache=true", json={})
            if resp.status_code == 200:
                try:
                    return resp.json()
                except Exception:
                    return {"raw": resp.text}
            return {"error": f"Status {resp.status_code}"}
        except Exception as e:
            return {"error": str(e)}

    async def search_prepod(self, query: str, id_fak: int = 0) -> List[Dict[str, Any]]:
        """
        Поиск преподавателей по фрагменту ФИО
        POST /get_prepod -> {"id_fak": 0, "search": "по"}
        """
        data = await self._post_json("/get_prepod", {"id_fak": id_fak, "search": query})
        prepods = data.get("prepods", [])
        results = []
        for p in prepods:
            raw_fio = p.get("fio", "")
            results.append({
                "id_prepod": p.get("id_prepod"),
                "fio": clean_fio(raw_fio),
                "raw_fio": raw_fio,
            })
        return results

    async def search_groups(self, query: str, id_fak: int = 0) -> List[Dict[str, Any]]:
        """
        Поиск групп по шифру
        POST /get_groups -> {"id_fak": 0, "search": "БЦШ02"}
        """
        data = await self._post_json("/get_groups", {"id_fak": id_fak, "search": query})
        return data.get("groups", data if isinstance(data, list) else [])

    async def search_auditors(self, query: str) -> List[Dict[str, Any]]:
        """
        Поиск аудиторий по номеру/корпусу
        POST /get_auditors -> {"search": "432"}
        """
        data = await self._post_json("/get_auditors", {"search": query})
        return data.get("auditors", data if isinstance(data, list) else [])

    async def get_rasp_prepod(self, kadr_id: int | str, beginweek: int = 10, endweek: int = 10) -> List[Dict[str, Any]]:
        """
        Расписание преподавателя
        POST /get_rasp_prepod -> {"kadr_id": "25903", "beginweek": 10, "endweek": 10}
        """
        payload = {
            "kadr_id": str(kadr_id),
            "beginweek": int(beginweek),
            "endweek": int(endweek),
        }
        data = await self._post_json("/get_rasp_prepod", payload)
        if isinstance(data, dict):
            return data.get("rasp", [])
        return []

    async def get_rasp_group(self, id_group: int | str, beginweek: int = 10, endweek: int = 10) -> List[Dict[str, Any]]:
        """Расписание академической группы"""
        payload = {
            "id_group": str(id_group),
            "beginweek": int(beginweek),
            "endweek": int(endweek),
        }
        data = await self._post_json("/get_rasp_group", payload)
        if isinstance(data, dict):
            return data.get("rasp", [])
        return []

    async def get_rasp_auditor(self, id_aud: int | str, beginweek: int = 10, endweek: int = 10) -> List[Dict[str, Any]]:
        """Расписание конкретной аудитории"""
        payload = {
            "id_aud": str(id_aud),
            "beginweek": int(beginweek),
            "endweek": int(endweek),
        }
        data = await self._post_json("/get_rasp_auditor", payload)
        if isinstance(data, dict):
            return data.get("rasp", [])
        return []
