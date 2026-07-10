FROM node:20-slim

WORKDIR /app

COPY package.json ./
# Optional @croo-network/sdk won't fail the build if unresolved (optionalDependencies).
RUN npm install

COPY . .

CMD ["node", "provider.mjs"]
