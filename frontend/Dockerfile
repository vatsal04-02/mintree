# Web image: Next.js production build. /api is proxied to the backend at the address baked in at build time (BACKEND_URL).
FROM node:22-slim AS build
WORKDIR /srv/web
COPY package.json package-lock.json ./
RUN npm ci
COPY . .
ARG BACKEND_URL=http://api:8000
ENV BACKEND_URL=${BACKEND_URL} NEXT_TELEMETRY_DISABLED=1
RUN npm run build

FROM node:22-slim
WORKDIR /srv/web
ENV NODE_ENV=production NEXT_TELEMETRY_DISABLED=1 BACKEND_URL=http://api:8000
COPY --from=build /srv/web/package.json /srv/web/package-lock.json ./
COPY --from=build /srv/web/node_modules ./node_modules
COPY --from=build /srv/web/.next ./.next
COPY --from=build /srv/web/public ./public
COPY --from=build /srv/web/next.config.ts ./
USER node
EXPOSE 3000
CMD ["npx", "next", "start", "-p", "3000"]
