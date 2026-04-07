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
│   ├── pipeline.py    # yt-dlp → ffmpeg → whisper
│   └── ai.py          # Claude CLI интеграция
├── frontend/
│   └── index.html     # Весь UI в одном файле
└── data/              # (gitignored) видео, кадры, база
    ├── videos/
    ├── frames/
    ├── audio/
    └── factory.db
```
