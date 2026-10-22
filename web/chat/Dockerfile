FROM node:24-alpine AS verify
WORKDIR /app
COPY package.json package-lock.json server.mjs start.mjs session.mjs ./
RUN npm ci --ignore-scripts --no-audit --no-fund
COPY scripts ./scripts
COPY public ./public
COPY test ./test
RUN npm test

FROM node:24-alpine AS runtime
ENV NODE_ENV=production HOST=0.0.0.0 PORT=3000 AGENTAI_URL=http://api:8000 CHAT_SESSION_KEY_FILE=/app/data/session.key
WORKDIR /app
COPY --from=verify --chown=node:node /app/package.json /app/server.mjs /app/start.mjs /app/session.mjs ./
COPY --from=verify --chown=node:node /app/public ./public
RUN mkdir -p /app/data && chown node:node /app/data
USER node
EXPOSE 3000
HEALTHCHECK --interval=15s --timeout=3s --start-period=5s --retries=3 CMD node -e "fetch('http://127.0.0.1:3000/health').then(r=>{if(!r.ok)process.exit(1)}).catch(()=>process.exit(1))"
CMD ["node", "start.mjs"]
