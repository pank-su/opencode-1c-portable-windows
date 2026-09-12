# OpenCode 1C Portable

[![CI](https://github.com/pank-su/opencode-1c-portable-windows/actions/workflows/ci.yml/badge.svg)](https://github.com/pank-su/opencode-1c-portable-windows/actions/workflows/ci.yml)

Переносимая сборка OpenCode для Windows x64 с русскоязычным навыком разработки решений 1С.

## Возможности

- официальный Windows-бинарник [OpenCode](https://github.com/anomalyco/opencode);
- настройки, авторизация, кэш и история внутри каталога `userdata`;
- модель по умолчанию `opencode-go/gpt-5.6-luna`;
- навык `1c-development` для BSL, EDT, конфигуратора, OneScript, Vanessa Automation, xUnitFor1C и YAxUnit;
- скрытый ввод API-ключа при первом запуске;
- проверка SHA-256 исходного дистрибутива;
- воспроизводимые ZIP-релизы через GitHub Actions.

## Установка

1. Скачайте ZIP из [Releases](https://github.com/pank-su/opencode-1c-portable-windows/releases).
2. Распакуйте архив в каталог без ограничений на запись.
3. Откройте PowerShell в своём проекте 1С.
4. Запустите launcher по абсолютному пути:

```powershell
& "D:\Tools\OpenCode-1C-Portable-Windows-x64\opencode.cmd"
```

При первом запуске введите API-ключ OpenCode Go. Ввод скрыт, а ключ сохраняется только в локальном `userdata\.local\share\opencode\auth.json`.

Для проверки установки:

```powershell
& "D:\Tools\OpenCode-1C-Portable-Windows-x64\check.cmd"
```

## Безопасность ключа

Репозиторий и публичные релизы **не содержат API-ключей**. Не добавляйте `auth.json` в Git и не прикладывайте его к issue. Если ключ случайно опубликован, немедленно отзовите его в панели OpenCode.

`setup-key.ps1` записывает `auth.json` атомарно и оставляет доступ только текущему пользователю Windows. Для этого нужен NTFS: на FAT/exFAT и некоторых сетевых/синхронизируемых томах ACL могут быть недоступны, поэтому скрипт завершится с ошибкой и не сохранит ключ без защиты.

## Создание релиза

Версия OpenCode и контрольная сумма закреплены в `opencode-version.json`. Новый тег запускает сборку и публикацию:

```bash
git tag v1.1.0
git push origin v1.1.0
```

Workflow скачивает официальный `opencode-windows-x64.zip`, проверяет SHA-256, запускает тесты, собирает portable-архив без секретов и прикрепляет ZIP с файлом `.sha256` к GitHub Release.

## Локальная проверка

```bash
python3 -m unittest discover -s tests -v
python3 scripts/build_release.py --release-version 1.0.0
```

Для второй команды требуется доступ к GitHub Releases; результат появится в `dist/`.
