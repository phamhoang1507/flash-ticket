FROM node:22-alpine AS build

WORKDIR /app

COPY package.json package-lock.json ./
RUN npm ci

COPY index.html vite.config.js ./
COPY src ./src

ARG VITE_API_BASE_URL=/api
ARG VITE_AUTH_PROVIDER=jwt
ARG VITE_COGNITO_AUTHORITY
ARG VITE_COGNITO_CLIENT_ID
ARG VITE_COGNITO_DOMAIN
ARG VITE_OIDC_SCOPE="openid email profile flash-ticket/bookings:write"

ENV VITE_API_BASE_URL=${VITE_API_BASE_URL} \
    VITE_AUTH_PROVIDER=${VITE_AUTH_PROVIDER} \
    VITE_COGNITO_AUTHORITY=${VITE_COGNITO_AUTHORITY} \
    VITE_COGNITO_CLIENT_ID=${VITE_COGNITO_CLIENT_ID} \
    VITE_COGNITO_DOMAIN=${VITE_COGNITO_DOMAIN} \
    VITE_OIDC_SCOPE=${VITE_OIDC_SCOPE}

RUN npm run build

FROM nginx:stable-alpine AS runtime

COPY nginx.conf /etc/nginx/nginx.conf
COPY --from=build --chown=nginx:nginx /app/dist /usr/share/nginx/html

USER nginx

EXPOSE 8080

ENTRYPOINT ["nginx", "-g", "daemon off;"]