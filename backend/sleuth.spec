# -*- mode: python ; coding: utf-8 -*-
"""Сборка «Ищейки» под Windows.

Одна папка, а не один файл: onefile каждый раз распаковывает ~200 МБ
во временный каталог, и запуск занимает десятки секунд. Пользователю
достаточно ярлыка на Ищейка.exe внутри папки.
"""

datas = [
    ("app/report/templates", "app/report/templates"),
    ("app/web", "app/web"),
    (".env.example", "."),
]

hiddenimports = [
    "aiosqlite",
    "uvicorn.logging",
    "uvicorn.loops.auto",
    "uvicorn.protocols.http.auto",
    "uvicorn.protocols.websockets.auto",
    "uvicorn.lifespan.on",
    "app.llm.anthropic_provider",
    "app.report.reportlab_export",
]

a = Analysis(
    ["launcher.py"],
    pathex=[],
    binaries=[],
    datas=datas,
    hiddenimports=hiddenimports,
    hookspath=[],
    runtime_hooks=[],
    # Тяжёлые пакеты, не нужные в работе: интерактивные окна matplotlib,
    # блокноты, средства разработки. Без исключений сборка распухает вдвое.
    excludes=["tkinter", "PyQt5", "PyQt6", "PySide2", "PySide6", "IPython",
              "jupyter", "notebook", "pytest", "playwright", "PIL.ImageQt"],
    noarchive=False,
)
pyz = PYZ(a.pure)

exe = EXE(
    pyz,
    a.scripts,
    [],
    exclude_binaries=True,
    name="Ищейка",
    debug=False,
    strip=False,
    upx=False,
    console=True,
)

coll = COLLECT(
    exe,
    a.binaries,
    a.datas,
    strip=False,
    upx=False,
    name="Ищейка",
)
