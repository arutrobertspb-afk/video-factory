# Video Factory

AI-нативный инструмент для парсинга YouTube видео, декомпозиции на кадры, транскрипции и анализа через Claude.

## Что умеет

- 📥 **Скачивание YouTube** через yt-dlp
- 📸 **Раскадровка** — 1 кадр в секунду через ffmpeg
- 💬 **Транскрипция** речи через OpenAI Whisper (локально)
- 🖼️ **Vision tagging** — Claude описывает каждый кадр
- 🔥 **Highlights** — AI находит виральные моменты
- 🎬 **AI чат** — свободные вопросы о видео (кадры + транскрипция в контексте)
- 🗂️ **Вложенные доски** — организуй видео по темам

## Стек

- **Backend**: Python + FastAPI + SQLite
- **Pipeline**: yt-dlp + ffmpeg + openai-whisper
- **AI**: Claude CLI subprocess (использует подписку Claude Code)
- **Frontend**: vanilla HTML/CSS/JS, одна страница

## Запуск

```bash
# Зависимости
brew install yt-dlp ffmpeg
pip3 install fastapi uvicorn openai-whisper

# Сервер
cd backend
python3 app.py
```

Открыть: http://127.0.0.1:8765/

## API

- `GET  /api/boards` — список досок (с parent_id для дерева)
- `POST /api/boards` — создать доску
- `DELETE /api/boards/{id}` — удалить
- `GET  /api/videos?board_id=N` — видео в доске
- `POST /api/videos` — добавить YouTube URL → запустить пайплайн
- `GET  /api/videos/{id}` — полные данные (видео + кадры + транскрипция)
- `PATCH /api/videos/{id}/move` — переместить в другую доску
- `POST /api/videos/{id}/reparse` — перезапустить пайплайн
- `POST /api/ai/ask` — задать вопрос Claude о видео
- `POST /api/ai/describe_frames/{id}` — описать все кадры
- `POST /api/ai/highlights/{id}` — найти виральные моменты

## Структура

```
video-factory/
├── backend/
│   ├── app.py         # FastAPI сервер
│   ├── db.py          # SQLite модели
│   ├── pipeline.py    # yt-dlp → ffmpeg → whisper + remix engine
│   ├── ai.py          # Claude CLI интеграция
│   └── trending.py    # YouTube search через yt-dlp
├── frontend/
│   └── index.html     # Весь UI в одном файле
├── mcp_server.py      # MCP сервер для AI агентов
└── data/              # (gitignored) видео, кадры, база
    ├── videos/
    ├── frames/
    ├── audio/
    ├── clips/
    ├── remixes/
    └── factory.db
```

## MCP сервер

Файл `mcp_server.py` экспортирует 18 инструментов через Model Context Protocol — любой MCP-совместимый агент (Claude Code, кастомные боты) может управлять Video Factory.

**Установка для Claude Code:**

Добавить в `mcp-config.json`:
```json
{
  "mcpServers": {
    "video-factory": {
      "command": "/opt/homebrew/bin/python3",
      "args": ["/path/to/video-factory/mcp_server.py"],
      "env": {"VIDEO_FACTORY_URL": "http://127.0.0.1:8765/api"}
    }
  }
}
```

**Доступные tools:**
- `vf_list_boards`, `vf_create_board`, `vf_delete_board`
- `vf_list_videos`, `vf_get_video`, `vf_add_video`, `vf_bulk_import`, `vf_move_video`
- `vf_search` — full-text по описаниям кадров и транскриптам
- `vf_trending` — поиск trending YouTube видео
- `vf_ask_video`, `vf_extract_highlights`, `vf_describe_frames` — AI анализ
- `vf_create_clip`, `vf_list_clips`, `vf_create_remix`, `vf_list_remixes`
- `vf_director` — автономный AI агент для multi-step пайплайнов

**Зависимость:** `pip install mcp` (Python 3.10+).
