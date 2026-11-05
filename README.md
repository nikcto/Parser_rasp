# USPTU Schedule Proxy & Parser (УГНТУ)

🚀 **Высокопроизводительный асинхронный прокси-сервер и парсер расписания УГНТУ** на базе **FastAPI**, **httpx** и **кэширования в памяти**.

Предназначен для обхода геоблокировок `raspisanie.rusoil.net` (сайт доступен только с российских IP-адресов) и интеграции с **Telegram Serverless Ботами** и **Telegram Mini Apps**.

---

## 🌟 Возможности

* ⚡ **Чистый REST API:** принимает запросы от любых клиентов (Telegram Cloud, браузер, боты).
* 🛡️ **Обход GeoIP блокировок:** сервер запускается на РФ VPS и напрямую общается с бэкендом УГНТУ.
* 📦 **Кэширование в памяти (TTLCache):**
  * Поисковые запросы кэшируются на **1 час**.
  * Расписания кэшируются на **30 минут**.
  * Защищает сервер вуза от перегрузок и банов, отдавая ответы вашему боту за **5–15 мс**.
* 🌐 **CORS включён:** Telegram Mini App может делать `fetch()` напрямую без проксирования через бэкенд.
* 📖 **Интерактивная Swagger-документация** доступна из коробки по адресу `/docs`.

---

## 📡 Эндпоинты API

| Метод | Эндпоинт | Описание | Пример параметров |
| :--- | :--- | :--- | :--- |
| `GET` | `/health` | Проверка работоспособности сервиса | — |
| `GET` | `/api/now` | Текущая учебная неделя и статус | — |
| `GET` | `/api/search` | Универсальный поиск (преподаватели, группы, аудитории) | `?q=Попов` |
| `GET` | `/api/prepods/search` | Поиск преподавателей по ФИО | `?q=Попов` |
| `GET` | `/api/groups/search` | Поиск групп по шифру | `?q=БЦШ02` |
| `GET` | `/api/auditors/search` | Поиск аудиторий по номеру/корпусу | `?q=432` |
| `GET` | `/api/prepod/{kadr_id}/schedule` | Расписание преподавателя по ID | `week=10` |
| `GET` | `/api/prepod/by-name` | Расписание преподавателя **сразу по имени** в 1 клик | `?name=Попов Денис&week=10` |
| `GET` | `/api/group/{id_group}/schedule` | Расписание академической группы | `week=10` |
| `GET` | `/api/auditor/{id_aud}/schedule` | Расписание аудитории | `week=10` |

---

## 🚀 Быстрый запуск

### Вариант 1: Через Docker (рекомендуется для любого VPS)

```bash
git clone https://github.com/nikcto/Parser_rasp.git
cd Parser_rasp

# Сборка и фоновый запуск
docker compose up -d --build
```
Сервис будет доступен по адресу `http://ВАШ_IP:8000/docs`.

---

### Вариант 2: Запуск без Docker (Python 3.10+)

```bash
# Клонирование
git clone https://github.com/nikcto/Parser_rasp.git
cd Parser_rasp

# Создание виртуального окружения
python3 -m venv venv
source venv/bin/activate  # На Windows: venv\Scripts\activate

# Установка зависимостей
pip install -r requirements.txt

# Запуск сервера
uvicorn main:app --host 0.0.0.0 --port 8000 --reload
```

---

## 🛠️ Настройка Systemd (автозапуск на Linux VPS)

Если вы запускаете проект без Docker на Ubuntu/Debian:

Создайте файл `/etc/systemd/system/rusoil-parser.service`:
```ini
[Unit]
Description=USPTU Schedule Proxy API
After=network.target

[Service]
User=root
WorkingDirectory=/root/Parser_rasp
ExecStart=/root/Parser_rasp/venv/bin/uvicorn main:app --host 0.0.0.0 --port 8000
Restart=always
RestartSec=5

[Install]
WantedBy=multi-user.target
```

Активируйте службу:
```bash
sudo systemctl daemon-reload
sudo systemctl enable --now rusoil-parser
```

---

## 📱 Пример использования в Telegram Mini App

```javascript
// Запрос расписания преподавателя прямо из Telegram Mini App
const res = await fetch('https://ваш-сервер.рф/api/prepod/by-name?name=Попов Денис&week=10');
const data = await res.json();

console.log(data.schedule);
// [{ DAYWEEK: 5, PARA: 6, NDISC: "Управление требованиями...", AUD: "-on-line" }, ...]
```
