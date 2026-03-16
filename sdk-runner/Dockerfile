FROM node:18-bullseye

# Устанавливаем зависимости для puppeteer
RUN apt-get update && apt-get install -y \
    chromium \
    && rm -rf /var/lib/apt/lists/*

# Устанавливаем puppeteer
WORKDIR /app
COPY package.json ./
RUN npm install

COPY . .
EXPOSE 3001 3002
CMD ["node", "src/server.js"]
