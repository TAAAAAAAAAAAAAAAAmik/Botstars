#!/usr/bin/env bash
#
# Подставляет данные из config.env в шаблоны документов и складывает
# готовый сайт в dist/. Шаблоны при этом не изменяются — скрипт можно
# запускать сколько угодно раз.
#
#   cp config.env.example config.env
#   nano config.env
#   ./build.sh

set -euo pipefail

SITE_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
cd "$SITE_DIR"

[ -f config.env ] || {
    echo "Нет config.env. Создайте его:" >&2
    echo "    cp config.env.example config.env && nano config.env" >&2
    exit 1
}

# shellcheck disable=SC1091
set -a; . ./config.env; set +a

REQUIRED=(SELLER_NAME SELLER_INN SELLER_EMAIL SUPPORT_TG SITE_URL BOT_URL DOC_DATE)
MISSING=()
for key in "${REQUIRED[@]}"; do
    [ -n "${!key:-}" ] || MISSING+=("$key")
done
if [ "${#MISSING[@]}" -gt 0 ]; then
    echo "Не заполнено в config.env: ${MISSING[*]}" >&2
    exit 1
fi

SUPPORT_TG_PLAIN="${SUPPORT_TG#@}"
SITE_URL="${SITE_URL%/}"

# Собираем в docs/ в корне репозитория: GitHub Pages умеет отдавать
# эту папку из ветки main без дополнительной настройки.
OUT="$(cd "$SITE_DIR/.." && pwd)/docs"

rm -rf "$OUT"
mkdir -p "$OUT/assets"
cp assets/style.css "$OUT/assets/"
touch "$OUT/.nojekyll"

for page in index.html offer.html terms.html privacy.html refund.html; do
    sed -e "s|{{SELLER_NAME}}|$SELLER_NAME|g" \
        -e "s|{{SELLER_INN}}|$SELLER_INN|g" \
        -e "s|{{SELLER_EMAIL}}|$SELLER_EMAIL|g" \
        -e "s|{{SUPPORT_TG_PLAIN}}|$SUPPORT_TG_PLAIN|g" \
        -e "s|{{SUPPORT_TG}}|$SUPPORT_TG|g" \
        -e "s|{{SITE_URL}}|$SITE_URL|g" \
        -e "s|{{BOT_URL}}|$BOT_URL|g" \
        -e "s|{{DOC_DATE}}|$DOC_DATE|g" \
        "$page" > "$OUT/$page"
done

# подсветку плейсхолдеров в готовой версии убираем, сам текст оставляем
sed -i -E 's|<span class="fill">([^<]*)</span>|\1|g' "$OUT"/*.html

LEFT=$(grep -l '{{' "$OUT"/*.html 2>/dev/null || true)
if [ -n "$LEFT" ]; then
    echo "Остались незаполненные плейсхолдеры в: $LEFT" >&2
    grep -o '{{[A-Z_]*}}' "$OUT"/*.html | sort -u >&2
    exit 1
fi

echo "Готово. Сайт собран в $OUT"
echo
echo "Проверить локально:   cd $OUT && python3 -m http.server 8080"
echo "Опубликовать:         git add docs && git commit -m 'site' && git push"
echo "                      затем Settings -> Pages -> Source: main, папка /docs"
