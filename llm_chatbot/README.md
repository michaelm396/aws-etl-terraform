# LLM Chatbot API

This folder contains the FastAPI service used by the `llm` branch. It runs on
EC2, connects to the existing PostgreSQL RDS database, executes controlled
predefined SQL queries, and asks local Ollama to summarize the query result.

The service does not let the model generate arbitrary SQL.

## Endpoints

- `GET /health`
- `POST /chat`

Example:

```bash
curl -X POST "http://<ec2-public-dns>:8000/chat" \
  -H "Content-Type: application/json" \
  -d '{"question":"How many records are business?"}'
```

## Model

The EC2 setup uses Ollama with:

```text
qwen2.5:0.5b
```

This model is small, efficient, and suitable for turning structured query
results into concise plain-English answers for demos.

## Supported Questions

- `How many records are business?`
- `How many records are commercial?`
- `What are the top countries in the dataset?`
- `How many records are from United States?`
- `How many records are missing city or region?`
- `Summarize the dataset.`

## Required Environment Variables

- `DB_HOST`
- `DB_PORT`
- `DB_NAME`
- `DB_USER`
- `DB_PASSWORD`

Optional:

- `OLLAMA_URL`, default `http://localhost:11434/api/chat`
- `OLLAMA_MODEL`, default `qwen2.5:0.5b`
