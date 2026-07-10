# Stage 1 — build the React app
FROM node:20-slim AS build
WORKDIR /web
COPY package.json ./
RUN npm install
COPY . .
RUN npm run build

# Stage 2 — serve the static build with nginx (also proxies /api -> api service)
FROM nginx:alpine
COPY nginx.conf /etc/nginx/conf.d/default.conf
COPY --from=build /web/dist /usr/share/nginx/html
EXPOSE 80
