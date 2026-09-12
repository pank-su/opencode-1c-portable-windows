# OpenCode 1C Portable для Windows x64

Сборка содержит официальный `opencode.exe`, переносимые настройки и навык `1c-development`.

## Запуск

Откройте PowerShell в каталоге проекта 1С и вызовите launcher по абсолютному пути:

```powershell
& "D:\Tools\OpenCode-1C-Portable-Windows-x64\opencode.cmd"
```

Текущий каталог PowerShell останется рабочим каталогом OpenCode. Аргументы CLI передаются напрямую:

```powershell
& "D:\Tools\OpenCode-1C-Portable-Windows-x64\opencode.cmd" run "Проверь модули 1С"
```

При первом запуске появится запрос API-ключа OpenCode Go. Ввод скрыт. Ключ хранится только в `userdata\.local\share\opencode\auth.json` рядом с программой.

Для повторной настройки ключа запустите:

```powershell
& ".\setup-key.cmd"
```

Для проверки установки:

```powershell
& ".\check.cmd"
```

## Что настроено

- модель: `opencode-go/gpt-5.6-luna`;
- автообновление отключено;
- конфигурация, авторизация, кэш и история находятся в `userdata`;
- навык 1С находится в `userdata\.config\opencode\skills\1c-development\SKILL.md`.

OpenCode увидит навык автоматически. Его можно вызвать явно: `Загрузи навык 1c-development и ...`.

## Безопасность

Не публикуйте `userdata\.local\share\opencode\auth.json`. Удаление `userdata` сбрасывает локальную авторизацию, настройки, кэш и историю.

`setup-key.ps1` записывает ключ атомарно и применяет ACL только для текущего пользователя Windows. Требуется NTFS; на FAT/exFAT и некоторых сетевых или синхронизируемых томах ACL может быть недоступен. В этом случае скрипт завершится с ошибкой и не оставит ключ в незащищённом файле.

Бинарные файлы 1С (`.cf`, `.cfe`, `.dt`, `.epf`, `.erf`) нельзя безопасно редактировать как текст. Используйте выгруженные исходники конфигуратора/EDT либо проект OneScript.
