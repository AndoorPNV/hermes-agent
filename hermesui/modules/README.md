# modules/

Декларации модулей HermesUI. Один модуль — одна папка, `id` == имя папки.

```
modules/<id>/
├── module.yaml          # манифест (обязателен, схема в docs/05-MODULES.md)
└── <артефакты>          # skill/ | plugin/ | plugin.js | theme.yaml | locales/
```

Пусто до M1 (`docs/07-ROADMAP.md`). Первый модуль — `skill-hermes-adapt`.

Не класть сюда: секреты, бэкапы, сгенерированные файлы, состояние (`hermesui.db` живёт
в `$HERMES_HOME`).
