# Передача Price Parser через GitHub

## Рекомендуемая схема

- репозиторий создаётся в аккаунте заказчика;
- Operator добавляется collaborator;
- основная ветка: `main`;
- релизный тег: `v0.7.1rc3`;
- GitHub Release содержит delivery ZIP и SHA-256.

## В репозиторий включаются

- исходный код;
- тесты;
- `README.md`;
- `.gitignore`;
- `.env.example`;
- зависимости;
- `CHANGELOG.md`;
- `RELEASE_MANIFEST.md`;
- актуальная документация.

## Не включаются

- `.env`;
- API-ключи;
- `.venv`;
- `input/`;
- `data/`;
- `reports/`;
- `archives/`;
- `installers/`;
- реальные прайсы;
- приватные evidence.

## Минимальные команды

```bash
git init
git add .
git commit -m "Release v0.7.1rc3"
git branch -M main
git remote add origin <REPOSITORY_URL>
git push -u origin main
git tag -a v0.7.1rc3 -m "Price Parser v0.7.1rc3"
git push origin v0.7.1rc3
```

Перед push обязательно проверить секреты и размер файлов.
