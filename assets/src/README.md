# Исходники баннеров

`assets/manager.png` — баннер службы поддержки, 1448×1086 (как `welcome.png`).
Собирается в две стадии: сначала рендерится 3D-звезда, затем она верстается
в баннер. Обе стадии работают через headless Chromium.

## Файлы

| Файл | Что это |
|---|---|
| `star-3d.html` | сцена three.js: выдавленная звезда со скруглениями и фаской, постамент, свет с тенями |
| `star.png` | результат рендера сцены — звезда на прозрачном фоне |
| `manager-banner.html` | вёрстка баннера: фон, логотип, текст, `star.png` |

## Поменять только текст

Это частый случай — 3D пересобирать не нужно:

```bash
cd assets/src
npx playwright screenshot --viewport-size=1448,1086 \
    manager-banner.html ../manager.png
```

## Пересобрать звезду

Нужно, если меняется форма, цвет или ракурс. Понадобится three.js и локальный
сервер (модули ES через `file://` Chromium не грузит):

```bash
cd assets/src
npm init -y && npm install three@0.169.0
python3 -m http.server 8099 --bind 127.0.0.1 &
npx playwright screenshot --viewport-size=1500,1800 --omit-background \
    http://127.0.0.1:8099/star-3d.html star-raw.png
convert star-raw.png -trim +repage star.png
```

Затем пересоберите баннер командой из предыдущего раздела.

Ракурс, свет и пропорции задаются в начале `star-3d.html`: `star.rotation`,
`makeStar(радиус, толщина, скругление_вершин, скругление_впадин)` и блок
источников света.
