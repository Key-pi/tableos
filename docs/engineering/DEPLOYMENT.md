# Запуск в продакшене

Как поднять Botera Shop на своём сервере (AA-038). Для разработки и тестов
— [DEVELOPMENT.md](DEVELOPMENT.md); здесь — то, что видят настоящие гости и
персонал.

## Что входит

`docker-compose.prod.yml` поднимает:

| Сервис | Что делает |
|---|---|
| `db` | PostgreSQL 16, данные в томе `postgres_data` |
| `redis` | Redis с паролем (`REDIS_PASSWORD`), брокер Celery и кэш |
| `init` | разово: проверки продакшен-настроек (`check --deploy`) и миграции; демо-данных нет |
| `web` | Admin, Mini App и экран кухни: gunicorn (`WEB_WORKERS`, по умолчанию 3), статика через whitenoise; порт только на `127.0.0.1` |
| `worker` | уведомления персоналу и гостям; `WORKER_CONCURRENCY` процессов |
| `broadcasts` | рассылки, один процесс — чтобы не задерживать уведомления |
| `beat` | периодические задачи (рассылки по расписанию, автоотмена неподтверждённых переводов, метрики, снимок нагрузки, чистка отметок бота сети); ровно один |
| `bot` | Telegram polling всех активных ботов; ровно один сервис, внутри — `BOT_PROCESSES` процессов (каждый бот ровно в одном) |
| `backup` | `pg_dump` раз в сутки в том `backups`, хранятся последние `BACKUP_KEEP_DAYS` дней |

Отличия от разработки:

- **Настройки.** Все процессы работают с `core.settings.production`.
  Модуль отказывается стартовать со слабыми значениями: стандартный
  `DJANGO_SECRET_KEY`, пустой ключ Fernet, `POSTGRES_PASSWORD=tableos`,
  `DJANGO_ALLOWED_HOSTS` без домена или с `*`, SQLite.
- **Без отладки.** `DEBUG` выключен.
- **Только HTTPS.** HTTP перенаправляется на HTTPS, включены HSTS и
  безопасные cookie; страницы нельзя встраивать во фреймы.
- **Защита входа.** После 5 неверных паролей подряд учётная запись Admin
  заблокирована на 15 минут (`core/security/login_throttle.py`; счёт в
  Redis, общий для всех процессов gunicorn).
- **Образ.** Код не монтируется, процессы работают от непривилегированного
  пользователя `app`, версии зависимостей закреплены в `requirements.lock`.

## Размер сервера

По нагрузочному тесту (`docs/engineering/LOAD_TESTING.md`): все партнёры —
кафе в одновременный пиковый час, история за год, цель — 95% ответов бота
быстрее 1 с.

| Партнёров в пик | Сервер | `BOT_PROCESSES` | `WORKER_CONCURRENCY` |
|---|---|---|---|
| до 30 | 2 ядра, 4 ГБ (минимум; 2 ГБ не хватит) | 1–2 | 2 |
| 30–50 | 2 ядра, 8 ГБ | 3 | 2 |
| 50–100 (оценка) | 4 ядра, 8–16 ГБ | 4–6 | 2–4 |
| 100+ (оценка) | приложение и база на разных серверах, вебхуки | — | — |

Таблица — по октябрьскому тесту без рабочих экранов Mini App. Повтор
2026-11-05 на более медленном стенде: 2 ядра держали около 35 точек без
экранов, а с двумя открытыми рабочими экранами на точку — меньше 30
(`LOAD_TESTING.md`, «Рабочие экраны Mini App»). Если персонал держит экраны
открытыми, берите сервер на ступень больше. Потолок для страницы «Нагрузка» —
`PLATFORM_CAPACITY_PARTNERS` (35), после нового теста его нужно поменять.

Память нужна не только процессам (вместе 3,5–4 ГБ при 3 процессах бота), но
и кэшу базы: когда база больше памяти, всё упирается в диск. База растёт
примерно на 190 МБ в год на активного партнёра.

## Первый запуск

1. **Сервер.** Нужен Linux с Docker и Compose v2, домен (например
   `shop.example.com`) должен указывать на сервер. Снаружи откройте только
   80 и 443.
2. **Код:** `git clone`, затем ветка, с которой выкатываетесь.
3. **Настройки:**

   ```bash
   cp .env.production.example .env.production
   ```

   Заполните все значения, генерируйте их на сервере. Команды генерации —
   в комментариях файла. Сохраните `BOT_TOKEN_ENCRYPTION_KEY` и пароли в
   менеджере паролей: без ключа сохранённые токены ботов не расшифровать.
   `.env.production` не коммитится.
4. **HTTPS и фото — Caddy (обязательно).** Перед `web` нужен обратный прокси
   с HTTPS, и он же отдаёт фото меню: иначе их отдаёт Django, и несколько
   гостей на медленной связи займут все веб-процессы (их 3) — Admin и
   Mini App встанут. [Caddy](https://caddyserver.com/) сам получает
   сертификат. Папка фото (`MEDIA_DIR` в `.env.production`, по умолчанию
   `/srv/botera-shop/media`) — на хосте; `web` пишет в неё от пользователя
   с uid 10001:

   ```bash
   sudo mkdir -p /srv/botera-shop/media && sudo chown 10001 /srv/botera-shop/media
   ```

   Файл `/etc/caddy/Caddyfile` (готовый — `infrastructure/caddy/Caddyfile`):

   ```
   shop.example.com {
       encode gzip zstd
       handle_path /media/* {
           root * /srv/botera-shop/media
           header Cache-Control "public, max-age=2592000, immutable"
           file_server
       }
       handle {
           reverse_proxy 127.0.0.1:8000
       }
   }
   ```

   Затем `sudo systemctl reload caddy`. Проверка: картинка позиции открывается
   по `https://shop.example.com/media/menu/…webp`, а в `docker compose logs web`
   запросов `/media/` нет. Прокси обязан передавать `X-Forwarded-Proto`; Caddy
   и стандартный nginx с `proxy_set_header X-Forwarded-Proto $scheme;` это
   делают. Фото при загрузке ужимаются до WebP, длинная сторона до 1200 px
   (около 100–200 КБ); загруженные раньше — `python manage.py
   compress_menu_photos`.
5. **Запуск:**

   ```bash
   docker compose --env-file .env.production -f docker-compose.prod.yml up -d --build
   docker compose --env-file .env.production -f docker-compose.prod.yml logs init
   ```

   `--env-file` обязателен: из него Compose берёт пароли для `db`, `redis`
   и `backup`. Если `init` упал, в его логе написано, какое значение не так.
6. **Администратор платформы:**

   ```bash
   docker compose --env-file .env.production -f docker-compose.prod.yml \
     run --rm web python manage.py createsuperuser
   ```

7. **Заведение.** В Admin (`https://shop.example.com/admin/`) создайте
   партнёра, его настройки бота, бота с токеном из @BotFather, владельца
   (пользователь, доступ к Admin, профиль сотрудника с Telegram ID).
   Остальных сотрудников владелец или менеджер добавит сам ссылкой из
   рабочего режима (PQ-046). Перезапускать ничего не нужно: в течение минуты сервис `bot` подхватит
   нового бота сам (PQ-042). Если бот не отвечает, причина видна в Admin →
   Боты, колонка «Состояние» (PQ-041).
8. **Mini App (по желанию, PQ-048).** В `.env.production` впишите
   `WEBAPP_BASE_URL=https://shop.example.com` (тот же домен, что и Admin) и
   перезапустите `web` и `bot`. Затем в Admin → «Настройки» → «Настройки
   бота», вкладка «Экраны», включите модуль «Mini App» у заведения (там же —
   «Рабочий экран персонала» и «Экран кухни и бара»). В течение минуты у бота появится кнопка «Меню и
   заказ» слева от поля ввода, а в меню бота — «📱 Открыть меню в приложении».
   В @BotFather ничего настраивать не нужно.

## Обновление

```bash
git pull
docker compose --env-file .env.production -f docker-compose.prod.yml up -d --build
```

`init` применит новые миграции, остальные сервисы перезапустятся после
него. Перед обновлением с миграциями сделайте внеочередную копию базы
(ниже).

## Резервные копии

- **Где.** Копии лежат в томе `backups` на этом же сервере. Это защищает от
  ошибки, но не от потери сервера: копируйте их наружу (другой сервер,
  облачное хранилище). Например, по cron раз в сутки:

  ```bash
  docker run --rm -v botera-shop-prod_backups:/b -v /srv/offsite:/o alpine \
    sh -c 'cp -n /b/*.sql.gz /o/'
  ```

  Затем `/srv/offsite` синхронизируется наружу (rclone, restic).
- **Фото меню** (PQ-048) лежат в папке `MEDIA_DIR` на хосте, не в базе.
  Копируйте её вместе с базой, например: `rsync -a /srv/botera-shop/media/
  /srv/offsite/media/`. Отдаёт их Caddy (шаг 4); `core/media.py:serve_media` в
  `web` остаётся только запасным путём для разработки.
- **Внеочередная копия:**

  ```bash
  docker compose --env-file .env.production -f docker-compose.prod.yml exec backup \
    sh -c 'pg_dump --no-owner --clean --if-exists | gzip > /backups/manual-$(date +%F-%H%M).sql.gz'
  ```

- **Восстановление** (остановите приложение, чтобы никто не писал в базу):

  ```bash
  docker compose --env-file .env.production -f docker-compose.prod.yml stop web worker beat bot
  docker compose --env-file .env.production -f docker-compose.prod.yml exec backup \
    sh -c 'gunzip -c /backups/<файл>.sql.gz | psql --single-transaction'
  docker compose --env-file .env.production -f docker-compose.prod.yml up -d
  ```

  Проверяйте восстановление на отдельной базе хотя бы раз в месяц:
  непроверенная копия — не копия.

## Зависимости

Образ ставит ровно `requirements.lock`. После изменения зависимостей в
`pyproject.toml`:

```bash
pip install pip-tools
pip-compile --strip-extras --no-emit-index-url --output-file requirements.lock pyproject.toml
pip-audit -r requirements.lock --disable-pip --no-deps   # известные уязвимости
```

Обновлять зависимости — отдельным изменением, с полным прогоном тестов.

## Что ещё не сделано

Это решения и работа за пределами репозитория:

- **Мониторинг ошибок и доступности.** Нет Sentry или аналога и нет
  внешней проверки `https://…/health/`, поэтому об ошибках вы узнаете от
  гостей. Страница «Нагрузка» (PQ-051) показывает платформе процессор,
  память, очереди и сломанных ботов с предупреждениями — но только тому, кто
  её открыл. Направление (PQ-039): отдельный информационный Telegram-бот для
  ошибок всех систем Botera — обсуждается позже.
- **Двухфакторный вход в Admin.** Его нет; длинные уникальные пароли
  обязательны. Владелец решил пока не делать (PQ-039).
- **Копии вне сервера и проверка восстановления.** Пока не делаем
  (PQ-039); при необходимости — по разделу «Резервные копии».
- **Ротация логов Docker.** Ограничьте размер логов в
  `/etc/docker/daemon.json`:

  ```json
  {"log-driver": "local", "log-opts": {"max-size": "20m"}}
  ```

- **Настройка PostgreSQL под память сервера.** Контейнер `db` работает со
  стандартными `shared_buffers = 128MB`. Когда база вырастет (около 190 МБ в
  год на партнёра), задайте `shared_buffers` около четверти памяти и
  `effective_cache_size` около половины (например, `command: postgres -c
  shared_buffers=2GB -c effective_cache_size=4GB` на сервере с 8 ГБ).
- **Политика персональных данных** (телефоны, адреса гостей) — PQ-024.
