# Почтовый контур Price Parser 0.6.0rc1

## Реализовано

- модели запросов, писем, вложений и попыток обработки;
- миграция БД `0002`;
- стабильный Message-ID и token запроса;
- связывание по `In-Reply-To`, `References`, затем по точному request token;
- дедупликация по Message-ID и SHA-256 исходного `.eml`;
- сохранение оригинального `.eml`;
- безопасные имена файлов;
- проверка размера, расширения и сигнатуры;
- quarantine для отклонённых вложений;
- retry и `DEAD_LETTER`;
- mock SMTP и локальный replay;
- API и CLI;
- явный флаг `--confirm-live-mail` для реального SMTP.

Тема письма без request token не используется как единственный идентификатор.
Письма не удаляются из IMAP: live-адаптер читает через `BODY.PEEK[]` в readonly-режиме.

## CLI

```powershell
price-parser mail request-create `
  --request-key "deal-42:supplier-1" `
  --to "supplier@example.ru" `
  --from-email "sales@example.ru" `
  --subject "Запрос цены" `
  --body "Просим сообщить цену и срок"

price-parser mail replay `
  --eml "C:\Temp\reply.eml" `
  --storage-root "C:\AI Test\mail_storage"

price-parser mail list
```

Live SMTP заблокирован без `--confirm-live-mail` и локальных переменных:

```text
MAIL_USERNAME
MAIL_PASSWORD
MAIL_SMTP_HOST
MAIL_SMTP_PORT
```

Пароли в чат и репозиторий не передаются.

## API

- `POST /mail/requests`
- `GET /mail/requests/{request_id}`
- `POST /mail/replay`
- `GET /mail/messages`
- `GET /mail/attachments`

## Ограничения

- live IMAP/SMTP не проверены;
- безопасные вложения получают `parser_status=READY`, но background worker ещё не подключён;
- антивирусный scanner не выбран;
- архивы во вложениях не распаковываются.

## Вопросы проверки и тестирования

Полный накопительный список находится в `docs/VERIFICATION_REGISTER.md`.
Для этого контура обязательны live SMTP/IMAP, сетевые ошибки, лимиты Mail.ru,
пересланные письма, несколько вложений и повреждённые документы.
