FROM node:20-slim

WORKDIR /app

# Только package.json для кеша слоёв. Полная установка будет в entrypoint при запуске (быстрее при разработке с изменениями).
COPY package*.json ./

COPY . .

RUN chmod +x docker-entrypoint.sh

ENTRYPOINT ["/app/docker-entrypoint.sh"]