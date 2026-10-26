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
| `web` | Admin: gunicorn, статика через whitenoise; порт только на `127.0.0.1` |
| `worker` | уведомления персоналу и гостям, рассылки |
| `beat` | периодические задачи (рассылки по расписанию, автоотмена неподтверждённых переводов, метрики); ровно один |
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
4. **HTTPS.** Перед `web` нужен обратный прокси с HTTPS. Проще всего
   [Caddy](https://caddyserver.com/) — он сам получает сертификат. Файл
   `/etc/caddy/Caddyfile`:

   ```
   shop.example.com {
       reverse_proxy 127.0.0.1:8000
   }
   ```

   Прокси обязан передавать `X-Forwarded-Proto`; Caddy и стандартный nginx с
   `proxy_set_header X-Forwarded-Proto $scheme;` это делают.
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
   (пользователь, доступ к Admin, профиль сотрудника с Telegram ID). Затем:

   ```bash
   docker compose --env-file .env.production -f docker-compose.prod.yml restart bot
   ```

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
  гостей. Направление (PQ-039): отдельный информационный Telegram-бот для
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

- **Политика персональных данных** (телефоны, адреса гостей) — PQ-024.
