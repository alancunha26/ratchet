# api

HTTP/RPC services: Express, Fastify, Hono, Nest, FastAPI, Flask, Django, Go net/http, Axum, Actix.

## Run
Start: `{{port_env}}=$PORT {{run}}`, with PORT from `{{R}} port`. Base URL: {{url}}. Ready when the log shows: {{ready_text}}.

## Exercise
1. Start the service in the background, logging to `{{scratch}}/api.log`, and wait until it's ready.
2. Call the endpoints the criteria name with `curl -si`: the happy path, at least one invalid input, and auth failures where relevant. Check the status, headers and body shape.
3. If the repo has an OpenAPI or GraphQL schema, confirm responses match it.
4. Read the service log for errors and stack traces during your calls.
5. Stop the service.

## Evidence
- The exact request and response (status plus the relevant body) for every criterion. Save transcripts to `{{scratch}}/calls.txt` and register that file.
- Log lines that show no errors.

## Good criteria
- "POST /orders with a missing `sku` returns 422 with `{error: 'sku is required'}`, and creates nothing."
- "GET /health returns 200 `{ok:true}`, and 503 when the DB is unreachable."
