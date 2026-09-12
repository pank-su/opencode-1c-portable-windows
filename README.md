# OpenCode 1C Portable

[![CI](https://github.com/pank-su/opencode-1c-portable-windows/actions/workflows/ci.yml/badge.svg)](https://github.com/pank-su/opencode-1c-portable-windows/actions/workflows/ci.yml)

Переносимая сборка OpenCode для Windows x64 с полным включённым набором из 79 навыков разработки 1С; рискованный `web-publish` отключён по умолчанию.

## Возможности

- официальный Windows-бинарник [OpenCode](https://github.com/anomalyco/opencode);
- настройки, авторизация, кэш и история внутри каталога `userdata`;
- модель по умолчанию `opencode-go/gpt-5.6-luna`;
- полный готовый набор [Nikolay-Shirokov/cc-1c-skills](https://github.com/Nikolay-Shirokov/cc-1c-skills/tree/05b3b3a58700f337a8b4e5c9e0e23f9bc0d8fdd0/.opencode/skills): 79 навыков для OpenCode с PowerShell-скриптами;
- скрытый ввод API-ключа при первом запуске;
- проверка SHA-256 исходного дистрибутива;
- воспроизводимые ZIP-релизы через GitHub Actions.

## Готовые навыки 1С

В сборку без изменений включены все 79 навыков из специальной OpenCode-ветки [Nikolay-Shirokov/cc-1c-skills](https://github.com/Nikolay-Shirokov/cc-1c-skills/tree/port-opencode), commit `05b3b3a58700f337a8b4e5c9e0e23f9bc0d8fdd0`. Это готовый upstream-набор для полного цикла разработки: конфигурации, расширения, EPF/ERF, формы, СКД, роли, XDTO, базы и веб-тестирование.

В portable-архив входят исходная лицензия MIT, `SOURCE.json` и SHA-256 manifest всех 341 upstream-файлов. Сборка завершается ошибкой при изменении, добавлении или удалении любого файла. Сами upstream-навыки не переписываются; portable-инструкция лишь перенаправляет их проектные пути `.opencode/skills/` в каталог из `OPENCODE_1C_SKILLS_DIR`.

Базовые требования upstream-набора:

- Windows с PowerShell 5.1+;
- 1С:Предприятие 8.3 — для сборки/разборки EPF/ERF и операций с базами;
- Node.js 18+ — только для навыка `/web-test`; в его каталоге `scripts` сначала выполните `npm install` для пакетов, затем `npx playwright install chromium` для отдельной загрузки браузера.

Для записи видео через `/web-test` дополнительно нужен `ffmpeg`. TTS-озвучка опциональна и может требовать отдельный API-ключ выбранного провайдера; этот ключ не связан с OpenCode Go и не записывается `setup-key.ps1`. Основные команды OpenCode-ветки используют PowerShell, однако в наборе также поставляются Python-варианты вспомогательных скриптов: при их ручном вызове нужен Python 3 и `lxml`, а `/img-grid` требует Python 3 и Pillow. Эти зависимости не входят в архив; установить их можно командой `py -m pip install lxml Pillow`.

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

Команды upstream-навыков могут запускать PowerShell, изменять проект и скачивать внешние инструменты. Поэтому portable-конфигурация требует подтверждение пользователя перед каждым shell-запуском; проверяйте команду и источник загрузки перед разрешением.

Навык `web-publish` включён в побайтово точный upstream-набор, но отключён по умолчанию: его скрипт скачивает изменяемую «последнюю» сборку Apache без закреплённой контрольной суммы и затем запускает её. Для безопасной публикации установите проверенный Apache вручную и передайте `-ApachePath`; разрешение навыка меняйте только после собственного аудита.

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
python3 scripts/build_release.py --release-version 1.0.2
```

Для второй команды требуется доступ к GitHub Releases; результат появится в `dist/`.
