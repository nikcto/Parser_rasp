"""
Асинхронный клиент для взаимодействия с официальным API расписания УГНТУ (raspisanie.rusoil.net)
"""
import re
from typing import Any, Dict, List, Optional
import httpx

BASE_URL = "https://raspisanie.rusoil.net"

# Заголовки, точно имитирующие реальный браузер и AJAX запросы jQuery с сайта raspisanie.rusoil.net
DEFAULT_HEADERS = {
    "User-Agent": (
        "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
        "AppleWebKit/537.36 (KHTML, like Gecko) "
        "Chrome/124.0.0.0 Safari/537.36"
    ),
    "Accept": "application/json, text/javascript, */*; q=0.01",
    "Accept-Language": "ru-RU,ru;q=0.9,en-US;q=0.8,en;q=0.7",
    "Origin": BASE_URL,
    "Referer": f"{BASE_URL}/",
    "X-Requested-With": "XMLHttpRequest",
}


def clean_fio(fio: str) -> str:
    """Очищает ФИО от точек: 'Попов Денис.Владимирович.' -> 'Попов Денис Владимирович'"""
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

    async def _post_req(self, endpoint: str, payload: Dict[str, Any]) -> Any:
        """
        Отправляет POST запрос к raspisanie.rusoil.net.
        Сервер университета работает на PHP, поэтому первоочередной формат -
        application/x-www-form-urlencoded (data=form_data), так как именно его
        ожидает PHP $_POST при запросах jQuery.
        В случае неудачи предусмотрен fallback на application/json.
        """
        client = await self.get_client()
        form_data = {k: str(v) for k, v in payload.items()}
        
        # Попытка 1: Form-urlencoded (стандарт jQuery / PHP)
        try:
            resp = await client.post(endpoint, data=form_data)
            if resp.status_code == 200:
                try:
                    data = resp.json()
                    if isinstance(data, (dict, list)):
                        return data
                except Exception:
                    pass
        except Exception:
            pass

        # Попытка 2: JSON payload
        try:
            resp = await client.post(endpoint, json=payload)
            if resp.status_code == 200:
                try:
                    data = resp.json()
                    if isinstance(data, (dict, list)):
                        return data
                except Exception:
                    pass
        except Exception as e:
            return {"error": "Connection error", "detail": str(e)}

        return {}

    async def get_now(self) -> Dict[str, Any]:
        """Получает текущую неделю и статус семестра"""
        client = await self.get_client()
        try:
            resp = await client.post("/get_now?nocache=true", data={})
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
        POST /get_prepod -> id_fak=0, search="по"
        """
        if not query or len(query.strip()) < 2:
            return []
        data = await self._post_req("/get_prepod", {"id_fak": id_fak, "search": query.strip()})
        if not isinstance(data, dict):
            return []
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
        POST /get_groups -> id_fak=0, search="БПО"
        """
        if not query or len(query.strip()) < 2:
            return []
        data = await self._post_req("/get_groups", {"id_fak": id_fak, "search": query.strip()})
        if isinstance(data, dict):
            return data.get("groups", [])
        if isinstance(data, list):
            return data
        return []

    async def search_auditors(self, query: str) -> List[Dict[str, Any]]:
        """
        Поиск аудиторий
        POST /get_auditors -> search="432"
        """
        if not query or len(query.strip()) < 1:
            return []
        data = await self._post_req("/get_auditors", {"search": query.strip()})
        if isinstance(data, dict):
            return data.get("auditors", [])
        if isinstance(data, list):
            return data
        return []

    async def get_rasp_prepod(self, kadr_id: int | str, beginweek: int = 10, endweek: int = 10) -> List[Dict[str, Any]]:
        """
        Расписание преподавателя
        POST /get_rasp_prepod -> kadr_id="25903", beginweek=10, endweek=10
        """
        payload = {
            "kadr_id": str(kadr_id),
            "beginweek": int(beginweek),
            "endweek": int(endweek),
        }
        data = await self._post_req("/get_rasp_prepod", payload)
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
        data = await self._post_req("/get_rasp_group", payload)
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
        data = await self._post_req("/get_rasp_auditor", payload)
        if isinstance(data, dict):
            return data.get("rasp", [])
        return []
