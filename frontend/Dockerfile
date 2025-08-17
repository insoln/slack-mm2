FROM node:20-slim

# Установка tini для корректного управления процессами
RUN apt-get update && apt-get install -y tini && rm -rf /var/lib/apt/lists/*

WORKDIR /app

COPY package*.json ./
RUN npm install

COPY . .

# Использование tini для запуска процесса
ENTRYPOINT ["/usr/bin/tini", "--"]
CMD ["npm", "run", "dev", "--", "--host"] 